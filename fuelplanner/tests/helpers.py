from unittest import mock

import numpy as np

from fuelplanner.services.stations import cumulative_miles


def straight_route_payload(start, end, n=2000, detour=1.1):
    """Fake OSRM response: a dense straight line between (lat, lon) points."""
    lat = np.linspace(start[0], end[0], n)
    lon = np.linspace(start[1], end[1], n)
    miles = cumulative_miles(lat, lon)[-1] * detour
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": miles * 1609.344,
                "duration": miles / 60 * 3600,
                "geometry": {
                    "type": "LineString",
                    "coordinates": np.column_stack((lon, lat)).tolist(),
                },
            }
        ],
    }


def mock_osrm():
    """Patch requests.get in the routing client to answer like OSRM, parsing the URL."""

    def fake_get(url, params=None, headers=None, timeout=None):
        a, b = url.rsplit("/", 1)[1].split(";")
        lon1, lat1 = map(float, a.split(","))
        lon2, lat2 = map(float, b.split(","))
        resp = mock.Mock(status_code=200)
        resp.json.return_value = straight_route_payload((lat1, lon1), (lat2, lon2))
        return resp

    return mock.patch("fuelplanner.services.routing.requests.get", side_effect=fake_get)
