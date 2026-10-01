"""Thin client for the free OSRM routing API (exactly one HTTP request per route).

Results are cached by rounded coordinates, so asking for the same trip again (or opening
its map) costs zero routing calls.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


class RoutingError(RuntimeError):
    """The routing provider failed or found no drivable route."""


@dataclass
class Route:
    coordinates: list[list[float]]  # [[lon, lat], ...] (GeoJSON order)
    distance_miles: float
    duration_hours: float


def _cache_key(start: tuple[float, float], end: tuple[float, float]) -> str:
    raw = f"{start[0]:.5f},{start[1]:.5f};{end[0]:.5f},{end[1]:.5f}"
    return "osrm-route:" + hashlib.sha1(raw.encode()).hexdigest()


def get_route(start: tuple[float, float], end: tuple[float, float]) -> tuple[Route, int, bool]:
    """Return (route, api_calls_made, served_from_cache). Points are (lat, lon)."""
    key = _cache_key(start, end)
    if (cached := cache.get(key)) is not None:
        return Route(**cached), 0, True

    cfg = settings.FUEL_PLANNER
    url = (
        f"{cfg['OSRM_BASE_URL'].rstrip('/')}/route/v1/driving/"
        f"{start[1]},{start[0]};{end[1]},{end[0]}"
    )
    try:
        resp = requests.get(
            url,
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
                "alternatives": "false",
            },
            headers={"User-Agent": cfg["HTTP_USER_AGENT"]},
            timeout=cfg["HTTP_TIMEOUT_SECONDS"],
        )
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Routing request failed: %s", exc)
        raise RoutingError("Routing service is unavailable, please try again shortly.") from exc

    if resp.status_code != 200 or payload.get("code") != "Ok" or not payload.get("routes"):
        message = payload.get("message") or payload.get("code") or f"HTTP {resp.status_code}"
        raise RoutingError(f"No drivable route found between these locations ({message}).")

    best = payload["routes"][0]
    route = Route(
        coordinates=best["geometry"]["coordinates"],
        distance_miles=best["distance"] / METERS_PER_MILE,
        duration_hours=best["duration"] / 3600.0,
    )
    cache.set(key, route.__dict__)
    return route, 1, False
