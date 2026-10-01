"""Orchestrates a trip plan: geocode -> 1 routing call -> corridor search -> optimise."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from django.conf import settings

from .geocoding import resolve_location
from .optimizer import plan_fuel_stops
from .routing import get_route
from .stations import get_index, stations_along_route

# The first stop is the cheapest station within this distance of the origin.
START_WINDOW_MILES = 25.0


@dataclass
class PlanOptions:
    corridor_miles: float | None = None
    stop_penalty_usd: float | None = None
    geometry_resolution_miles: float = 0.5


def _decimate(coords: np.ndarray, cum: np.ndarray, step_miles: float) -> list[list[float]]:
    """Keep roughly one vertex per ``step_miles`` (always keeping both ends)."""
    if len(coords) <= 2 or step_miles <= 0:
        return np.round(coords, 5).tolist()
    bucket = np.floor(cum / step_miles)
    keep = np.concatenate(([True], bucket[1:] != bucket[:-1]))
    keep[-1] = True
    return np.round(coords[keep], 5).tolist()


def build_trip_plan(
    start_query: str, finish_query: str, options: PlanOptions | None = None
) -> dict:
    t0 = time.perf_counter()
    options = options or PlanOptions()
    cfg = settings.FUEL_PLANNER
    mpg, max_range = cfg["MPG"], cfg["MAX_RANGE_MILES"]
    corridor = options.corridor_miles or cfg["DEFAULT_CORRIDOR_MILES"]
    stop_penalty = (
        cfg["STOP_PENALTY_USD"] if options.stop_penalty_usd is None else options.stop_penalty_usd
    )

    start, geo_calls_a = resolve_location(start_query)
    finish, geo_calls_b = resolve_location(finish_query)

    route, routing_calls, cached = get_route((start.lat, start.lon), (finish.lat, finish.lon))
    coords = np.asarray(route.coordinates, dtype=float)

    index = get_index()
    candidates, cum = stations_along_route(index, coords, corridor)

    # Align geometric mileage with the provider's road distance.
    scale = route.distance_miles / cum[-1] if cum[-1] > 0 else 1.0
    cum = cum * scale
    for c in candidates:
        c.mile *= scale

    stops = (
        []
        if route.distance_miles < 0.1
        else plan_fuel_stops(
            [c.mile for c in candidates],
            [c.price for c in candidates],
            route.distance_miles,
            max_range=max_range,
            start_window_miles=START_WINDOW_MILES,
            stop_penalty=stop_penalty,
            mpg=mpg,
        )
    )

    fuel_stops, total_cost, total_gallons = [], 0.0, 0.0
    start_gallons = stops[0].start_fuel_miles / mpg if stops else 0.0
    start_cost = start_gallons * stops[0].price if stops else 0.0
    total_cost, total_gallons = start_cost, start_gallons
    for n, stop in enumerate(stops, start=1):
        cand = candidates[stop.candidate_pos]
        rec = index.records[cand.index]
        gallons = stop.gallons(mpg)
        cost = gallons * stop.price
        total_cost += cost
        total_gallons += gallons
        fuel_stops.append(
            {
                "stop_number": n,
                "opis_id": rec["opis_id"],
                "name": rec["name"].strip(),
                "address": rec["address"].strip(),
                "city": rec["city"].strip(),
                "state": rec["state"].strip(),
                "lat": round(rec["latitude"], 6),
                "lon": round(rec["longitude"], 6),
                "price_per_gallon": round(stop.price, 3),
                "mile_marker": round(stop.mile, 1),
                "distance_from_route_miles": round(cand.off_route, 1),
                "gallons": round(gallons, 2),
                "cost": round(cost, 2),
            }
        )

    return {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "distance_miles": round(float(route.distance_miles), 1),
            "duration_hours": round(route.duration_hours, 2),
            "geometry": {
                "type": "LineString",
                "coordinates": _decimate(coords, cum, options.geometry_resolution_miles),
            },
        },
        "fuel_stops": fuel_stops,
        "summary": {
            "total_fuel_cost_usd": round(float(total_cost), 2),
            "total_gallons": round(float(total_gallons), 2),
            "number_of_stops": len(fuel_stops),
            # Fuel burned driving from the origin to stop 1, priced at stop 1 (see README).
            "fuel_to_first_stop": {
                "gallons": round(float(start_gallons), 2),
                "cost_usd": round(float(start_cost), 2),
            },
            "average_price_per_gallon": (
                round(float(total_cost / total_gallons), 3) if total_gallons else None
            ),
            "vehicle_mpg": mpg,
            "vehicle_max_range_miles": max_range,
            "stations_considered": len(candidates),
            "corridor_miles": corridor,
            "stop_penalty_usd": stop_penalty,
        },
        "meta": {
            "routing_api_calls": routing_calls,
            "geocoding_api_calls": geo_calls_a + geo_calls_b,
            "route_from_cache": cached,
            "routing_provider": "OSRM (OpenStreetMap)",
            "compute_time_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
