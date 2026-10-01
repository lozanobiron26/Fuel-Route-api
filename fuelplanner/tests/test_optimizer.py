import random

from django.test import SimpleTestCase

from fuelplanner.services.optimizer import NoFuelPlanError, plan_fuel_stops

from .reference import greedy_cost


def plan_cost(stops):
    return sum(s.billed_miles() * s.price for s in stops)


class OptimizerTests(SimpleTestCase):
    def test_matches_provably_optimal_greedy_on_random_routes(self):
        rng = random.Random(42)
        checked = 0
        for _ in range(1500):
            total = rng.uniform(50, 3000)
            miles = sorted([0.0] + [rng.uniform(0, total) for _ in range(rng.randint(1, 50))])
            prices = [round(rng.uniform(2.5, 4.0), 3) for _ in miles]
            reference = greedy_cost(miles, prices, total, 500)
            if reference is None:
                with self.assertRaises(NoFuelPlanError):
                    plan_fuel_stops(miles, prices, total, max_range=500, start_window_miles=0)
                continue
            stops = plan_fuel_stops(miles, prices, total, max_range=500, start_window_miles=0)
            self.assertAlmostEqual(plan_cost(stops), reference, places=6)
            checked += 1
        self.assertGreater(checked, 500)

    def test_plan_respects_range_and_buys_exactly_the_fuel_needed(self):
        rng = random.Random(7)
        miles = sorted(rng.uniform(0, 2500) for _ in range(300))
        prices = [rng.uniform(2.8, 3.9) for _ in miles]
        stops = plan_fuel_stops(miles, prices, 2500, max_range=500, stop_penalty=5)
        self.assertAlmostEqual(sum(s.billed_miles() for s in stops), 2500, places=6)
        positions = [0.0] + [s.mile for s in stops] + [2500]
        self.assertTrue(all(b - a <= 500 + 1e-6 for a, b in zip(positions, positions[1:])))
        self.assertTrue(all(s.miles_of_fuel <= 500 + 1e-6 for s in stops))

    def test_prefers_cheap_station_and_skips_expensive_one(self):
        # Fill at the cheap station near the start, skip the pricey one, top up at the cheap one.
        stops = plan_fuel_stops([0, 300, 450], [3.0, 5.0, 2.5], 800, max_range=500)
        self.assertEqual([s.mile for s in stops], [0, 450])
        self.assertAlmostEqual(stops[0].miles_of_fuel, 450)
        self.assertAlmostEqual(stops[1].miles_of_fuel, 350)

    def test_first_stop_bills_fuel_used_to_reach_it(self):
        stops = plan_fuel_stops([5, 50], [3.5, 3.0], 120, max_range=500)
        # 5 miles to reach the first stop + 45 to the cheaper one, then 70 to the end.
        self.assertEqual(
            [(s.mile, s.start_fuel_miles, s.miles_of_fuel) for s in stops],
            [(5, 5, 45), (50, 0, 70)],
        )

    def test_short_trip_is_a_single_stop(self):
        stops = plan_fuel_stops([3, 60], [3.1, 3.0], 120, max_range=500, stop_penalty=5)
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0].billed_miles(), 120)

    def test_stop_penalty_reduces_tiny_stops(self):
        rng = random.Random(3)
        miles = sorted(rng.uniform(0, 2800) for _ in range(400))
        prices = [rng.uniform(2.8, 3.9) for _ in miles]
        strict = plan_fuel_stops(miles, prices, 2800, max_range=500, stop_penalty=0)
        practical = plan_fuel_stops(miles, prices, 2800, max_range=500, stop_penalty=10)
        self.assertLessEqual(len(practical), len(strict))
        self.assertGreaterEqual(plan_cost(practical) + 1e-9, plan_cost(strict))

    def test_gap_longer_than_range_raises(self):
        with self.assertRaisesMessage(NoFuelPlanError, "No fuel station between"):
            plan_fuel_stops([0, 100, 700], [3, 3, 3], 900, max_range=500)

    def test_no_stations(self):
        with self.assertRaises(NoFuelPlanError):
            plan_fuel_stops([], [], 300, max_range=500)
