# Fuel Route API

A Django 6.1 + DRF API that takes a start and finish location in the USA and returns:

- the driving route (GeoJSON line, plus an interactive map page),
- the cost-optimal places to refuel along it for a vehicle with a **500-mile range**,
- the **total fuel cost** at **10 MPG**, using the provided OPIS truck-stop price list.

Each new trip makes **one** call to the routing API. Repeat trips and the map page come from cache and make **zero** calls. Once the route is in hand, planning takes about 20–180 ms.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.12+
pip install -r requirements.txt
python manage.py migrate
python manage.py import_fuel_prices                    # ~3 s, geocodes 6.6k stations offline
python manage.py runserver
```

```bash
curl "http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA"
```

Open `map_url` from the response (or http://127.0.0.1:8000/) to see the map.
You can import the Postman collection from `docs/fuel-route-api.postman_collection.json`.

Run the tests with `python manage.py test`.

## API

### `GET /api/route/` or `POST /api/route/`

| param | required | description |
|---|---|---|
| `start`, `finish` | yes | `"City, ST"`, `"City, State"`, a ZIP code, `"lat,lon"`, or a street address |
| `corridor_miles` | no | how far a station may be from the route (default 10, max 50) |
| `stop_penalty_usd` | no | planning-only cost per extra stop (default 5). Use `0` for the strictly cheapest plan |
| `include_geometry` | no | `false` leaves the route line out of the response |

Example response, trimmed (the numbers are only illustrative):

```json
{
  "start":  {"label": "New York, NY", "lat": 40.7571, "lon": -73.9778, "resolved_by": "gazetteer"},
  "finish": {"label": "Los Angeles, CA", "lat": 34.0522, "lon": -118.2509, "resolved_by": "gazetteer"},
  "route":  {"distance_miles": 2719.6, "duration_hours": 45.33,
             "geometry": {"type": "LineString", "coordinates": [[-73.97, 40.75], "..."]}},
  "fuel_stops": [
    {"stop_number": 1, "name": "BOLLA MARKET", "address": "I-95, EXIT 13", "city": "Elizabeth",
     "state": "NJ", "lat": 40.66, "lon": -74.21, "price_per_gallon": 3.099, "mile_marker": 14.8,
     "distance_from_route_miles": 3.6, "gallons": 50.0, "cost": 154.95}
  ],
  "summary": {"total_fuel_cost_usd": 839.98, "total_gallons": 271.96, "number_of_stops": 8,
              "fuel_to_first_stop": {"gallons": 1.48, "cost_usd": 4.59},
              "average_price_per_gallon": 3.089, "vehicle_mpg": 10, "vehicle_max_range_miles": 500},
  "meta": {"routing_api_calls": 1, "geocoding_api_calls": 0, "route_from_cache": false,
           "compute_time_ms": 177.2},
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA"
}
```

Errors: `400` for bad input or a location outside the USA, `422` when part of the route has no station within 500 miles, `502` when the routing provider fails.

### Other endpoints

- `GET /api/route/map/?start=…&finish=…` shows an interactive Leaflet map of the route, the numbered fuel stops and a cost summary. It reuses the cached route.
- `GET /api/health/` returns the service status and how many stations are loaded.

## How it works

```
start/finish ──► offline geocoder ──► OSRM (1 call, cached) ──► corridor search ──► optimiser ──► JSON / map
                 (ZIP gazetteer)       full route geometry       KD-tree, ~5 ms      DP, ~10–40 ms
```

1. **Geocoding, usually with no API call.** The price file has no coordinates, so `import_fuel_prices` geocodes each station to its city centroid. It uses the USPS ZIP centroids bundled in the `zipcodes` package and needs no network. All 6,626 unique US stations resolve. Rows from Canada are skipped, and duplicate OPIS IDs keep their lowest price. User input such as `City, ST`, a ZIP code or `lat,lon` goes through the same gazetteer. Only a free-form street address falls back to one Nominatim call.
2. **Routing, with one call.** The app calls the free public [OSRM](https://project-osrm.org/) server (`overview=full`, GeoJSON) once per trip. The result is stored in Django's cache under the rounded coordinates.
3. **Corridor search.** The app builds a KD-tree over the route's points, using 3-D unit vectors so distances are correct on a sphere. It then queries all stations against it in one vectorised call. Each station within `corridor_miles` gets a mile marker along the route.
4. **Optimisation.** This is the "gas station problem" (Khuller, Malekian & Mestre, *To fill or not to fill*, 2011). An optimal plan always either fills the tank or buys just enough to reach the next stop, so the fuel level at each station has only a few possible values. A backwards dynamic programme, vectorised with numpy, finds the cheapest plan exactly. With `stop_penalty_usd=0`, a test confirms the result against the provably optimal greedy algorithm on 1,500 random routes. The default $5 per-stop penalty only affects which plan is chosen and is never billed. It stops the planner from adding a detour to save a few cents.

## Assumptions

- The tank holds 500 miles of range, which is 50 gallons at 10 MPG.
- The vehicle leaves the origin with only enough fuel to reach its first stop. That fuel is priced at the first stop and shown as `fuel_to_first_stop`. The first stop is chosen from stations within 25 miles of the origin when any exist.
- The total cost covers all fuel burned on the trip (`distance / 10` gallons), and the vehicle arrives with an empty tank.
- Each station is placed at its city's centroid, so `distance_from_route_miles` is approximate and the extra miles of the detour are not charged.
- The routing provider can be changed with `OSRM_BASE_URL`. The public OSRM demo server is fine for a demo, but production should use a self-hosted or paid router.

## Project layout

```
config/                      settings & urls
fuelplanner/
  models.py                  FuelStation
  management/commands/       import_fuel_prices
  services/geocoding.py      offline gazetteer + Nominatim fallback
  services/routing.py        OSRM client (1 call, cached)
  services/stations.py       in-memory station index + KD-tree corridor search
  services/optimizer.py      fuel-stop DP
  services/planner.py        orchestration, response shape
  views.py / serializers.py  DRF API + map view
  templates/fuelplanner/     Leaflet map (Leaflet bundled under static/vendor)
  tests/                     optimiser, geocoding, API tests (routing mocked)
data/                        fuel-prices-for-be-assessment.csv
docs/                        Postman collection
```
