import threading

from django.apps import AppConfig


class FuelplannerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fuelplanner"

    def ready(self):
        # Build the offline gazetteer in the background so the first request is fast.
        from .services.geocoding import _gazetteer

        threading.Thread(target=_gazetteer, daemon=True, name="gazetteer-warmup").start()
