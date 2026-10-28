"""Kiểm tra cấu hình triển khai offline (dùng trước khi bàn giao nhà máy)."""

import os
import socket

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Kiểm tra settings, CSDL, migration, thư mục ghi, broker MQTT và heartbeat."

    def add_arguments(self, parser):
        parser.add_argument("--skip-mqtt", action="store_true",
                            help="Bỏ qua kiểm tra kết nối TCP tới broker.")
        parser.add_argument("--strict-worker", action="store_true",
                            help="Coi worker không có heartbeat là lỗi.")

    def _check_mqtt(self) -> str:
        if self.skip_mqtt:
            return "SKIP"
        try:
            with socket.create_connection(
                (settings.MQTT_BROKER, int(settings.MQTT_PORT)), timeout=3
            ):
                return "OK"
        except OSError as exc:
            return f"FAIL ({exc})"

    def handle(self, *args, **options):
        self.skip_mqtt = options["skip_mqtt"]
        results: list[tuple[str, str]] = []

        results.append(("ACS_ENV", settings.ACS_ENV))
        results.append(("DEBUG", str(settings.DEBUG)))
        results.append(("DB engine", connection.vendor))

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            results.append(("Database", "OK"))
        except Exception as exc:
            results.append(("Database", f"FAIL ({exc})"))

        try:
            executor = MigrationExecutor(connection)
            unapplied = [f"{app}.{name}" for app, name in executor.migration_plan(
                executor.loader.graph.leaf_nodes()
            )]
            results.append(("Migration", "OK" if not unapplied else f"FAIL (missing {unapplied})"))
        except Exception as exc:
            results.append(("Migration", f"FAIL ({exc})"))

        for label, path in (("Log dir", settings.LOG_DIR), ("Media dir", settings.MEDIA_ROOT)):
            try:
                os.makedirs(path, exist_ok=True)
                probe = os.path.join(path, ".write-test")
                with open(probe, "w", encoding="utf-8") as handle:
                    handle.write("ok")
                os.remove(probe)
                results.append((label, f"OK ({path})"))
            except OSError as exc:
                results.append((label, f"FAIL ({exc})"))

        results.append(("MQTT broker", self._check_mqtt()))

        from workers.acs_system import worker_status

        workers = worker_status()
        if not workers:
            results.append(("Worker heartbeat", "WARN (no worker running)"))
        else:
            stale = [name for name, info in workers.items() if info["stale"]]
            results.append(
                ("Worker heartbeat", "OK" if not stale else f"WARN (stale: {stale})")
            )

        has_failure = False
        for label, value in results:
            if value.startswith("FAIL"):
                has_failure = True
            elif value.startswith("WARN") and options["strict_worker"]:
                has_failure = True
            style = self.style.ERROR if value.startswith("FAIL") else (
                self.style.WARNING if value.startswith("WARN") else self.style.SUCCESS
            )
            self.stdout.write(f"{label:<18}: {style(value)}")

        if has_failure:
            self.stderr.write(self.style.ERROR("Deployment check FAILED."))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("Deployment check passed."))
