"""Cost-optimal refuelling along a fixed route (the "gas station problem").

Uses the dynamic programme from Khuller, Malekian & Mestre, *To fill or not to fill:
the gas station problem* (ACM TALG 2011). For a fixed route there is always an optimal
plan in which, at every stop, the driver either

* buys just enough fuel to reach the next stop (when that stop is not more expensive), or
* fills the tank (when the next stop is more expensive),

so the fuel level when arriving at a station is either 0 or ``range - dist(prev_stop, here)``.
That bounds the states per station to the stations within one tank-range behind it, and the
DP is solved backwards with numpy vectorised over those fuel levels.

A small optional ``stop_penalty`` (USD, used only while choosing, never billed) stops the
optimiser from making extra stops that save only a few cents, which a pure price minimiser
does all the time. ``stop_penalty=0`` gives the strictly cheapest plan.

Billing assumptions (see README):
* Range/fuel is tracked in miles: 500-mile range at 10 MPG = 50-gallon tank.
* The vehicle leaves the origin with only enough fuel to reach its first stop; that fuel is
  billed at the first stop's price, so the total is the cost of *all* fuel burned
  (``total_miles / MPG`` gallons). The first stop must be within ``start_window_miles``
  of the origin when any station is.
* The vehicle arrives at the destination with an empty tank.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-6
INF = float("inf")


class NoFuelPlanError(ValueError):
    """The route has a stretch longer than the vehicle range with no fuel station."""


@dataclass
class Stop:
    candidate_pos: int  # index into the candidate lists passed in
    mile: float
    price: float
    miles_of_fuel: float  # fuel put in the tank here, in miles of range (<= tank size)
    start_fuel_miles: float = 0.0  # first stop only: fuel burned getting here, billed here

    def gallons(self, mpg: float) -> float:
        return self.miles_of_fuel / mpg

    def billed_miles(self) -> float:
        return self.miles_of_fuel + self.start_fuel_miles


def _dedupe(miles, prices, total_miles, same_place_miles=1e-9):
    """Keep only the cheapest of stations sharing a mile marker (same city centroid)."""
    order = sorted(
        (k for k in range(len(miles)) if miles[k] <= total_miles + EPS),
        key=lambda k: (miles[k], prices[k]),
    )
    kept: list[int] = []
    for k in order:
        if kept and miles[k] - miles[kept[-1]] <= same_place_miles:
            if prices[k] < prices[kept[-1]]:
                kept[-1] = k
            continue
        kept.append(k)
    return kept


def _gap_message(d: np.ndarray, total: float, max_range: float) -> str:
    points = np.concatenate(([0.0], d, [total]))
    gaps = np.diff(points)
    k = int(np.argmax(gaps))
    return (
        f"No fuel station between mile {points[k]:.0f} and mile {points[k + 1]:.0f} "
        f"({gaps[k]:.0f} miles exceeds the {max_range:.0f}-mile range). "
        f"Try a wider corridor_miles."
    )


def plan_fuel_stops(
    miles: list[float],
    prices: list[float],
    total_miles: float,
    *,
    max_range: float,
    start_window_miles: float = 25.0,
    stop_penalty: float = 0.0,
    mpg: float = 10.0,
) -> list[Stop]:
    """Return the purchase plan. ``miles``/``prices`` describe candidate stations (any order),
    ``prices`` are $/gallon and ``stop_penalty`` is in dollars."""
    # Internally fuel is in miles of range, so cost = miles * price / mpg; scale the penalty
    # instead of every price (identical optimum, cheaper arithmetic).
    stop_penalty = stop_penalty * mpg
    kept = _dedupe(miles, prices, total_miles)
    if not kept:
        raise NoFuelPlanError("No fuel stations were found along this route.")

    d = np.array([miles[k] for k in kept], dtype=float)
    p = np.array([prices[k] for k in kept], dtype=float)
    n, cap, T = len(d), max_range, total_miles

    # lo[j]: first station index within one tank-range behind station j.
    lo = np.searchsorted(d, d - cap - EPS, side="left")
    # Arrival fuel levels at j: index 0 -> empty, index 1 + (i - lo[j]) -> filled up at i.
    levels = [np.concatenate(([0.0], cap - (d[j] - d[lo[j] : j]))) for j in range(n)]

    hi = np.searchsorted(d, d + cap + EPS, side="right")  # stations reachable on a full tank
    cost: list[np.ndarray] = [None] * n  # cost-to-go per arrival level, aligned with levels[j]
    empty_cost = np.full(n, INF)  # cost[j][0], kept as a vector for fast gathers

    def options(i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        """Vectorised view of every move out of station i."""
        js = np.arange(i + 1, hi[i])
        dij = d[js] - d[i]
        cheap = p[js] <= p[i]
        cheap_js, cheap_d = js[cheap], dij[cheap]
        # Move "buy just enough to reach cheaper j, arrive empty": p_i*(dij - g) + C_j(0)
        cheap_val = p[i] * cheap_d + empty_cost[cheap_js]
        # Move "fill up, drive to pricier j": p_i*(cap - g) + C_j(filled at i)
        fill_best = INF
        if T - d[i] > cap + EPS:  # filling up is pointless when the destination is in range
            for j in js[~cheap]:
                fill_best = min(fill_best, cost[j][1 + i - lo[j]])
        return cheap_js, cheap_d, cheap_val, fill_best

    for i in range(n - 1, -1, -1):
        g = levels[i]
        best = np.full(g.shape, INF)
        d_to_end = T - d[i]
        if d_to_end <= cap + EPS:
            best = np.where(g <= d_to_end + EPS, p[i] * np.maximum(d_to_end - g, 0.0), INF)

        cheap_js, cheap_d, cheap_val, fill_best = options(i)
        if cheap_js.size:
            # Only stations at least g miles away can be reached "empty": suffix minimum.
            suffix_min = np.append(np.minimum.accumulate(cheap_val[::-1])[::-1], INF)
            first_ok = np.searchsorted(cheap_d, g - EPS, side="left")
            best = np.minimum(best, suffix_min[first_ok] - p[i] * g)
        if fill_best < INF:
            best = np.minimum(best, p[i] * (cap - g) + fill_best)

        cost[i] = best + stop_penalty
        empty_cost[i] = cost[i][0]

    # Choose the first stop (reached on the fuel already in the tank, billed there).
    window = np.nonzero(d <= start_window_miles + EPS)[0]
    starts = window if window.size else np.array([0])
    start_cost = np.array([p[s] * d[s] + cost[s][0] if d[s] <= cap + EPS else INF for s in starts])
    if not np.isfinite(start_cost).any():
        raise NoFuelPlanError(_gap_message(d, T, cap))
    i = int(starts[int(np.argmin(start_cost))])

    # Reconstruct: at each stop re-evaluate the moves and take the one achieving cost[i][state].
    purchases: list[tuple[int, float, float]] = []  # (station, bought, start fuel billed)
    state, reserve = 0, d[i]
    while True:
        g = levels[i][state]
        target = cost[i][state] - stop_penalty
        moves: list[tuple[float, int, int, float]] = []  # (cost, next_station, next_state, bought)
        d_to_end = T - d[i]
        if d_to_end <= cap + EPS and g <= d_to_end + EPS:
            moves.append((p[i] * max(d_to_end - g, 0.0), -1, 0, d_to_end - g))
        cheap_js, cheap_d, cheap_val, _ = options(i)
        for j, dij, val in zip(cheap_js, cheap_d, cheap_val):
            if dij >= g - EPS:
                moves.append((val - p[i] * g, int(j), 0, dij - g))
        if T - d[i] > cap + EPS:
            for j in range(i + 1, hi[i]):
                if p[j] > p[i]:
                    k = 1 + i - lo[j]
                    moves.append((p[i] * (cap - g) + cost[j][k], j, k, cap - g))
        c, j, k, bought = min(moves, key=lambda m: (m[0], m[1] == -1))
        assert abs(c - target) <= 1e-6 * max(1.0, abs(target)), "DP reconstruction mismatch"
        purchases.append((i, max(bought, 0.0), reserve))
        reserve = 0.0
        if j == -1:
            break
        i, state = j, k

    return [
        Stop(kept[i], float(d[i]), float(p[i]), float(amt), float(res))
        for i, amt, res in purchases
        if amt + res > EPS
    ]
