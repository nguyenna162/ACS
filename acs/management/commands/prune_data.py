"""Dọn dữ liệu vận hành theo thời hạn lưu trữ (chạy định kỳ bằng scheduler)."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from workers.acs_system import EventBus
from workers.acs_system import MqttOutbox


class Command(BaseCommand):
    help = "Dọn event realtime, MQTT outbox, telemetry và log hệ thống quá hạn."

    def add_arguments(self, parser):
        parser.add_argument("--telemetry-days", type=int,
                            default=settings.ACS_TELEMETRY_RETENTION_DAYS)
        parser.add_argument("--outbox-days", type=int,
                            default=settings.ACS_OUTBOX_RETENTION_DAYS)
        parser.add_argument("--log-days", type=int,
                            default=settings.ACS_SYSTEM_LOG_RETENTION_DAYS)
        parser.add_argument("--events-seconds", type=int,
                            default=settings.ACS_EVENT_RETENTION_SECONDS)

    def handle(self, *args, **options):
        events = EventBus.prune(options["events_seconds"])
        outbox = MqttOutbox.prune(options["outbox_days"])

        from amr.models import AmrStateLog

        cutoff = timezone.now() - timezone.timedelta(days=options["telemetry_days"])
        telemetry, _ = AmrStateLog.objects.filter(recorded_at__lt=cutoff).delete()

        from acs.models import SystemLog

        log_cutoff = timezone.now() - timezone.timedelta(days=options["log_days"])
        system_logs, _ = SystemLog.objects.filter(created_at__lt=log_cutoff).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Đã dọn: event={events}, outbox={outbox}, telemetry={telemetry}, "
                f"system_log={system_logs}"
            )
        )
