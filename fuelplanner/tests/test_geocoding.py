from unittest import mock

from django.test import SimpleTestCase

from fuelplanner.services.geocoding import GeocodingError, lookup_city, resolve_location


class GeocodingTests(SimpleTestCase):
    def test_city_state_offline(self):
        loc, calls = resolve_location("Chicago, IL")
        self.assertEqual(calls, 0)
        self.assertAlmostEqual(loc.lat, 41.88, delta=0.3)
        self.assertAlmostEqual(loc.lon, -87.63, delta=0.3)

    def test_state_name_and_saint_abbreviation(self):
        a, _ = resolve_location("Saint Louis, Missouri")
        b, _ = resolve_location("St. Louis MO")
        self.assertAlmostEqual(a.lat, b.lat, places=4)

    def test_zip_and_coordinates(self):
        loc, calls = resolve_location("80202")
        self.assertEqual((loc.source, calls), ("zip", 0))
        loc, calls = resolve_location("39.7392, -104.9903")
        self.assertEqual((loc.source, calls), ("coordinates", 0))

    def test_rejects_coordinates_outside_usa(self):
        with self.assertRaises(GeocodingError):
            resolve_location("48.8566, 2.3522")  # Paris

    def test_station_city_with_space_variant(self):
        self.assertIsNotNone(lookup_city("De Forest", "WI"))

    @mock.patch("fuelplanner.services.geocoding.requests.get")
    def test_free_form_address_falls_back_to_nominatim(self, get):
        get.return_value.json.return_value = [
            {"lat": "38.8977", "lon": "-77.0365", "display_name": "White House"}
        ]
        get.return_value.raise_for_status.return_value = None
        loc, calls = resolve_location("1600 Pennsylvania Avenue NW")
        self.assertEqual((loc.source, calls), ("nominatim", 1))

    def test_unknown_offline_only(self):
        with self.assertRaises(GeocodingError):
            resolve_location("Nowhereville, ZZ", allow_remote=False)
