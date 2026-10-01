import json
import logging
from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FuelStation
from .serializers import RoutePlanRequestSerializer
from .services.geocoding import GeocodingError
from .services.optimizer import NoFuelPlanError
from .services.planner import PlanOptions, build_trip_plan
from .services.routing import RoutingError

logger = logging.getLogger(__name__)

ERROR_STATUS = {
    GeocodingError: status.HTTP_400_BAD_REQUEST,
    NoFuelPlanError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    RoutingError: status.HTTP_502_BAD_GATEWAY,
}


def _run_plan(data: dict) -> tuple[dict | None, dict | None, int]:
    """Returns (plan, error_body, http_status)."""
    try:
        plan = build_trip_plan(
            data["start"],
            data["finish"],
            PlanOptions(
                corridor_miles=data.get("corridor_miles"),
                stop_penalty_usd=data.get("stop_penalty_usd"),
            ),
        )
        return plan, None, status.HTTP_200_OK
    except tuple(ERROR_STATUS) as exc:
        return None, {"error": str(exc)}, ERROR_STATUS[type(exc)]


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops for a trip between two US locations.

    GET  /api/route/?start=Chicago, IL&finish=Denver, CO
    POST /api/route/  {"start": "Chicago, IL", "finish": "Denver, CO"}
    """

    def get(self, request):
        return self._handle(request, request.query_params)

    def post(self, request):
        return self._handle(request, request.data)

    def _handle(self, request, raw):
        serializer = RoutePlanRequestSerializer(data=raw)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        plan, error, http_status = _run_plan(data)
        if error:
            return Response(error, status=http_status)

        params = {"start": data["start"], "finish": data["finish"]}
        for optional in ("corridor_miles", "stop_penalty_usd"):
            if data.get(optional) is not None:
                params[optional] = data[optional]
        plan["map_url"] = request.build_absolute_uri(f"{reverse('route-map')}?{urlencode(params)}")
        if not data["include_geometry"]:
            plan["route"].pop("geometry")
        return Response(plan)


def route_map_view(request):
    """Interactive Leaflet map of the route and fuel stops (reuses the cached route)."""
    serializer = RoutePlanRequestSerializer(data=request.GET)
    if not serializer.is_valid():
        return render(
            request, "fuelplanner/map.html", {"error": json.dumps(serializer.errors)}, status=400
        )
    plan, error, http_status = _run_plan(serializer.validated_data)
    if error:
        return render(
            request, "fuelplanner/map.html", {"error": error["error"]}, status=http_status
        )
    return render(request, "fuelplanner/map.html", {"plan": plan})


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok", "fuel_stations_loaded": FuelStation.objects.count()})
