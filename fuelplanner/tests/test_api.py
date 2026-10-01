import io
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from fuelplanner.models import FuelStation
from fuelplanner.services.stations import reset_index

from .helpers import mock_osrm

CSV = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
1,CHI STOP,"I-90, EXIT 1",Chicago,IL,1,3.50
1,CHI STOP,"I-90, EXIT 1",Chicago,IL,1,3.10
2,IOWA CHEAP,"I-80, EXIT 2",Iowa City,IA,2,2.90
3,OMAHA PRICEY,"I-80, EXIT 3",Omaha,NE,3,4.10
4,KEARNEY,"I-80, EXIT 4",Kearney,NE,4,3.20
5,STERLING,"I-76, EXIT 5",Sterling,CO,5,3.30
6,DENVER,"I-70, EXIT 6",Denver,CO,6,3.40
7,TORONTO,HWY 401,Toronto,ON,7,1.00
"""

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class RoutePlanApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.csv"
            path.write_text(CSV)
            call_command("import_fuel_prices", path=str(path), stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        reset_index()

    def test_import_dedupes_keeps_lowest_price_and_skips_non_us(self):
        self.assertEqual(FuelStation.objects.count(), 6)
        self.assertEqual(str(FuelStation.objects.get(opis_id=1).retail_price), "3.1000")

    def test_plan_route_get(self):
        with mock_osrm() as osrm:
            resp = self.client.get(
                reverse("route-plan"),
                {"start": "Chicago, IL", "finish": "Denver, CO", "corridor_miles": 30},
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(osrm.call_count, 1)
        self.assertEqual(body["meta"]["routing_api_calls"], 1)
        self.assertEqual(body["meta"]["geocoding_api_calls"], 0)
        names = [s["name"] for s in body["fuel_stops"]]
        self.assertNotIn("OMAHA PRICEY", names)
        self.assertIn("IOWA CHEAP", names)
        total = sum(s["cost"] for s in body["fuel_stops"])
        total += body["summary"]["fuel_to_first_stop"]["cost_usd"]
        self.assertTrue(all(s["gallons"] <= 50.001 for s in body["fuel_stops"]))
        self.assertAlmostEqual(body["summary"]["total_fuel_cost_usd"], total, delta=0.05)
        self.assertAlmostEqual(
            body["summary"]["total_gallons"], body["route"]["distance_miles"] / 10, delta=0.05
        )
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")
        self.assertIn("/api/route/map/", body["map_url"])

    def test_post_and_cache_means_no_second_routing_call(self):
        payload = {
            "start": "Chicago, IL",
            "finish": "Denver, CO",
            "corridor_miles": 30,
            "include_geometry": False,
        }
        with mock_osrm() as osrm:
            first = self.client.post(
                reverse("route-plan"), payload, content_type="application/json"
            )
            second = self.client.post(
                reverse("route-plan"), payload, content_type="application/json"
            )
            map_page = self.client.get(reverse("route-map"), payload)
        self.assertEqual(first.status_code, 200)
        self.assertNotIn("geometry", first.json()["route"])
        self.assertTrue(second.json()["meta"]["route_from_cache"])
        self.assertEqual(map_page.status_code, 200)
        self.assertContains(map_page, "vendor/leaflet/leaflet.js")
        self.assertEqual(osrm.call_count, 1)

    def test_validation_errors(self):
        resp = self.client.get(reverse("route-plan"), {"start": "Chicago, IL"})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get(
            reverse("route-plan"), {"start": "48.85,2.35", "finish": "Denver, CO"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("outside the USA", resp.json()["error"])

    def test_no_coverage_returns_422(self):
        with mock_osrm():
            resp = self.client.get(
                reverse("route-plan"), {"start": "Miami, FL", "finish": "Seattle, WA"}
            )
        self.assertEqual(resp.status_code, 422)

    def test_health(self):
        self.assertEqual(self.client.get(reverse("health")).json()["fuel_stations_loaded"], 6)
