import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuelplanner.models import FuelStation
from fuelplanner.services.geocoding import US_STATES, lookup_city
from fuelplanner.services.stations import reset_index


class Command(BaseCommand):
    help = (
        "Load the OPIS fuel price CSV, de-duplicate stations (keeping the lowest price "
        "per OPIS ID) and geocode them offline to their city centroid."
    )

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(settings.FUEL_PLANNER["FUEL_CSV_PATH"]))

    def handle(self, *args, **opts):
        path = Path(opts["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        best: dict[int, dict] = {}
        stats = {"rows": 0, "non_us": 0, "bad_rows": 0, "not_geocoded": 0}
        unresolved: set[str] = set()

        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                stats["rows"] += 1
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    price = Decimal(row["Retail Price"].strip())
                except (KeyError, ValueError, InvalidOperation):
                    stats["bad_rows"] += 1
                    continue
                state = row["State"].strip().upper()
                if state not in US_STATES:
                    stats["non_us"] += 1
                    continue
                current = best.get(opis_id)
                if current is None or price < current["retail_price"]:
                    rack = row.get("Rack ID", "").strip()
                    best[opis_id] = {
                        "opis_id": opis_id,
                        "name": row["Truckstop Name"].strip(),
                        "address": row["Address"].strip(),
                        "city": row["City"].strip(),
                        "state": state,
                        "rack_id": int(rack) if rack.isdigit() else None,
                        "retail_price": price,
                    }

        stations = []
        for rec in best.values():
            coords = lookup_city(rec["city"], rec["state"])
            if coords is None:
                stats["not_geocoded"] += 1
                unresolved.add(f"{rec['city']}, {rec['state']}")
                continue
            stations.append(FuelStation(latitude=coords[0], longitude=coords[1], **rec))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)
        reset_index()

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(stations)} unique US stations from {stats['rows']} rows "
                f"(skipped: {stats['non_us']} non-US rows, {stats['bad_rows']} malformed, "
                f"{stats['not_geocoded']} stations not geocoded)."
            )
        )
        if unresolved:
            self.stdout.write(f"Unresolved cities: {', '.join(sorted(unresolved))}")
