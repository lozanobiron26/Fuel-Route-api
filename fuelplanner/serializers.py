from rest_framework import serializers


class RoutePlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200, help_text="'City, ST', ZIP code, 'lat,lon' or a street address in the USA"
    )
    finish = serializers.CharField(max_length=200, help_text="Same formats as start")
    corridor_miles = serializers.FloatField(
        required=False,
        min_value=1,
        max_value=50,
        help_text="Max distance of a station from the route (default 10)",
    )
    include_geometry = serializers.BooleanField(
        required=False, default=True, help_text="Include the route GeoJSON line in the response"
    )
    stop_penalty_usd = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=200,
        help_text="Planning-only cost per extra stop (default 5). 0 = strictly cheapest plan",
    )
