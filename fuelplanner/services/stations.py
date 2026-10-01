"""In-memory spatial index of fuel stations and route-corridor matching.

Stations are loaded from the DB once per process into numpy arrays. For each request we
build a KD-tree over the route vertices (3-D unit vectors, so distances are exact on the
sphere) and query every station against it: O(S log R), a few milliseconds for ~7k
stations and a coast-to-coast route.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_MILES = 3958.7613

_lock = threading.Lock()
_index: StationIndex | None = None


def to_unit_xyz(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def chord_to_miles(chord: np.ndarray) -> np.ndarray:
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.clip(chord / 2, 0, 1))


def miles_to_chord(miles: float) -> float:
    return 2 * np.sin(miles / (2 * EARTH_RADIUS_MILES))


def cumulative_miles(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Haversine cumulative distance along a polyline."""
    la, lo = np.radians(lat), np.radians(lon)
    dlat, dlon = np.diff(la), np.diff(lo)
    a = np.sin(dlat / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(dlon / 2) ** 2
    seg = 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.concatenate(([0.0], np.cumsum(seg)))


@dataclass
class StationIndex:
    ids: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    price: np.ndarray
    xyz: np.ndarray
    records: list[dict]

    @classmethod
    def from_records(cls, records: list[dict]) -> StationIndex:
        lat = np.array([r["latitude"] for r in records], dtype=float)
        lon = np.array([r["longitude"] for r in records], dtype=float)
        return cls(
            ids=np.array([r["opis_id"] for r in records], dtype=np.int64),
            lat=lat,
            lon=lon,
            price=np.array([float(r["retail_price"]) for r in records], dtype=float),
            xyz=to_unit_xyz(lat, lon) if records else np.empty((0, 3)),
            records=records,
        )

    def __len__(self) -> int:
        return len(self.records)


@dataclass
class Candidate:
    index: int  # position in StationIndex.records
    mile: float  # distance from the start along the route
    off_route: float  # straight-line distance from the route, miles
    price: float


def get_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                from fuelplanner.models import FuelStation

                rows = list(
                    FuelStation.objects.values(
                        "opis_id",
                        "name",
                        "address",
                        "city",
                        "state",
                        "retail_price",
                        "latitude",
                        "longitude",
                    )
                )
                _index = StationIndex.from_records(rows)
    return _index


def reset_index() -> None:
    global _index
    with _lock:
        _index = None


def stations_along_route(
    index: StationIndex, route_lonlat: np.ndarray, corridor_miles: float
) -> tuple[list[Candidate], np.ndarray]:
    """Return stations within ``corridor_miles`` of the route and the route's cumulative miles."""
    lat, lon = route_lonlat[:, 1], route_lonlat[:, 0]
    cum = cumulative_miles(lat, lon)
    if len(index) == 0:
        return [], cum

    # Cheap pre-filter: bounding box of the route padded by the corridor.
    pad_lat = corridor_miles / 69.0
    pad_lon = corridor_miles / (69.0 * max(np.cos(np.radians(np.abs(lat).max())), 0.2))
    in_box = np.nonzero(
        (index.lat >= lat.min() - pad_lat)
        & (index.lat <= lat.max() + pad_lat)
        & (index.lon >= lon.min() - pad_lon)
        & (index.lon <= lon.max() + pad_lon)
    )[0]
    if in_box.size == 0:
        return [], cum

    tree = cKDTree(to_unit_xyz(lat, lon))
    dist, nearest = tree.query(
        index.xyz[in_box], k=1, distance_upper_bound=miles_to_chord(corridor_miles)
    )
    hit = np.isfinite(dist)
    candidates = [
        Candidate(index=int(i), mile=float(cum[v]), off_route=float(d), price=float(index.price[i]))
        for i, v, d in zip(in_box[hit], nearest[hit], chord_to_miles(dist[hit]))
    ]
    candidates.sort(key=lambda c: (c.mile, c.price))
    return candidates, cum
