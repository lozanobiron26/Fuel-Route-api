"""Geocoding of user input and of fuel stations.

Resolution order for a user-supplied location (cheapest first):

1. ``"lat,lon"`` coordinates                    -> no API call
2. 5-digit ZIP code                             -> offline gazetteer, no API call
3. ``"City, ST"`` / ``"City, State"``          -> offline gazetteer, no API call
4. anything else (street address, landmark)     -> one Nominatim call (fallback only)

The offline gazetteer is built from the ``zipcodes`` package (USPS ZIP centroids bundled
with the package), so the common case costs zero external requests.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import requests
import zipcodes
from django.conf import settings

logger = logging.getLogger(__name__)

STATE_NAMES = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
}
US_STATES = set(STATE_NAMES.values())

# Rough bounding boxes (lat_min, lat_max, lon_min, lon_max): lower 48, Alaska, Hawaii.
US_BOXES = [
    (24.0, 49.6, -125.0, -66.5),
    (51.0, 71.6, -180.0, -129.0),
    (18.8, 22.4, -160.6, -154.6),
]

_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


class GeocodingError(ValueError):
    """The location could not be resolved to a point inside the USA."""


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    lat: float
    lon: float
    source: str  # coordinates | zip | gazetteer | nominatim

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "label": self.label,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "resolved_by": self.source,
        }


def in_usa(lat: float, lon: float) -> bool:
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in US_BOXES)


def normalize_city(name: str) -> str:
    s = re.sub(r"[.'`]", "", name.upper())
    s = re.sub(r"[\s\-]+", " ", s).strip()
    s = re.sub(r"^SAINT ", "ST ", s)
    s = re.sub(r"^SAINTE ", "STE ", s)
    s = re.sub(r"^FORT ", "FT ", s)
    s = re.sub(r"^MOUNT ", "MT ", s)
    return s


def _compact(norm: str) -> str:
    return norm.replace(" ", "")


@lru_cache(maxsize=1)
def _gazetteer() -> tuple[dict, dict, dict]:
    """Build {(CITY, ST): (lat, lon, display)} and {zip: (...)} from bundled ZIP data."""
    primary: dict[tuple[str, str], list] = defaultdict(list)
    alternate: dict[tuple[str, str], list] = defaultdict(list)
    by_zip: dict[str, tuple[float, float, str]] = {}
    display: dict[tuple[str, str], str] = {}

    for z in zipcodes.list_all():
        if z.get("country") != "US" or not z.get("lat") or z.get("state") not in US_STATES:
            continue
        lat, lon, st = float(z["lat"]), float(z["long"]), z["state"]
        if lat == 0 and lon == 0:
            continue
        by_zip[z["zip_code"]] = (lat, lon, f"{z['city']}, {st}")
        key = (normalize_city(z["city"]), st)
        primary[key].append((lat, lon))
        display.setdefault(key, f"{z['city'].title()}, {st}")
        for alt in z.get("acceptable_cities") or []:
            alternate[(normalize_city(alt), st)].append((lat, lon))

    def collapse(groups):
        # Median of ZIP centroids is robust to outlying PO-box ZIPs.
        return {
            k: (statistics.median(p[0] for p in v), statistics.median(p[1] for p in v))
            for k, v in groups.items()
        }

    cities = collapse(alternate)
    cities.update(collapse(primary))  # primary city names win over "acceptable" aliases
    for (name, st), coords in list(cities.items()):
        cities.setdefault((_compact(name), st), coords)  # "DE FOREST" == "DEFOREST"
    return cities, by_zip, display


def lookup_city(city: str, state: str) -> tuple[float, float] | None:
    cities, _, _ = _gazetteer()
    state = state.strip().upper()
    state = STATE_NAMES.get(state, state)
    norm = normalize_city(city)
    return cities.get((norm, state)) or cities.get((_compact(norm), state))


def _parse_city_state(text: str) -> tuple[str, str] | None:
    text = _ZIP_RE.sub("", text).strip().rstrip(",")
    text = re.sub(r",?\s*(USA|US|UNITED STATES)$", "", text, flags=re.I).strip()
    if "," in text:
        city, _, state = text.rpartition(",")
        return city.strip(), state.strip()
    parts = text.split()
    if len(parts) >= 2 and parts[-1].upper() in US_STATES:
        return " ".join(parts[:-1]), parts[-1]
    return None


def resolve_location(query: str, *, allow_remote: bool = True) -> tuple[Location, int]:
    """Return (Location, number_of_external_api_calls_made)."""
    query = (query or "").strip()
    if not query:
        raise GeocodingError("Location is empty.")

    if m := _COORD_RE.match(query):
        lat, lon = float(m.group(1)), float(m.group(2))
        if not in_usa(lat, lon):
            raise GeocodingError(
                f"Coordinates {lat},{lon} are outside the USA (expected 'lat,lon')."
            )
        return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates"), 0

    cities, by_zip, display = _gazetteer()

    if (pair := _parse_city_state(query)) is not None:
        city, state = pair
        if coords := lookup_city(city, state):
            st = STATE_NAMES.get(state.upper(), state.upper())
            label = display.get((normalize_city(city), st), f"{city.title()}, {st}")
            return Location(query, label, coords[0], coords[1], "gazetteer"), 0

    if (m := _ZIP_RE.search(query)) and m.group(1) in by_zip:
        lat, lon, label = by_zip[m.group(1)]
        return Location(query, f"{label} {m.group(1)}", lat, lon, "zip"), 0

    if not allow_remote:
        raise GeocodingError(
            f"Could not resolve '{query}'. Use 'City, ST', a ZIP code or 'lat,lon'."
        )
    return _nominatim(query), 1


def _nominatim(query: str) -> Location:
    cfg = settings.FUEL_PLANNER
    try:
        resp = requests.get(
            cfg["NOMINATIM_URL"],
            params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": cfg["HTTP_USER_AGENT"]},
            timeout=cfg["HTTP_TIMEOUT_SECONDS"],
        )
        resp.raise_for_status()
        results = resp.json()
    except requests.RequestException as exc:
        logger.warning("Nominatim lookup failed for %r: %s", query, exc)
        raise GeocodingError(
            f"Could not geocode '{query}' (geocoder unavailable). "
            "Try 'City, ST', a ZIP code or 'lat,lon'."
        ) from exc
    if not results:
        raise GeocodingError(f"No location in the USA found for '{query}'.")
    hit = results[0]
    lat, lon = float(hit["lat"]), float(hit["lon"])
    if not in_usa(lat, lon):
        raise GeocodingError(f"'{query}' resolved outside the USA.")
    return Location(query, hit.get("display_name", query), lat, lon, "nominatim")
