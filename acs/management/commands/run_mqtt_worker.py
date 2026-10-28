"""Tiến trình MQTT của ACS: nhận trạng thái AMR, phát outbox, giám sát giao thông.

Chạy offline (không Docker/Redis)::

    python manage.py run_mqtt_worker

Tiến trình này kết hợp 3 worker:
* ``MqttIngestWorker``      - subscribe ``vda5050/v2/+/+/state|connection``.
* ``OutboxPublisherWorker`` - gửi mọi bản tin ACS -> AMR từ Transactional Outbox.
* ``SystemWorker`` (workers/acs_system.py) - heartbeat, khóa hết hạn, deadlock, dọn dữ liệu.

Worker ghi nhịp sống vào DB; ``/api/health/ready`` báo degraded nếu tiến trình
này treo quá ``ACS_WORKER_STALE_SECONDS``.
"""

import logging

from django.core.management.base import BaseCommand

from workers.schema_class import run_workers
from workers.ingest import MqttIngestWorker
from workers.mqtt_publisher import OutboxPublisherWorker
from workers.acs_system import SystemWorker

logger = logging.getLogger("workers")


class Command(BaseCommand):
    help = "Chạy MQTT ingest + outbox publisher + giám sát giao thông (không cần Docker)."

    def add_arguments(self, parser):
        parser.add_argument("--no-publisher", action="store_true",
                            help="Không gửi bản tin từ outbox (chỉ nhận trạng thái).")
        parser.add_argument("--no-traffic", action="store_true",
                            help="Không chạy giám sát giao thông/heartbeat.")

    def handle(self, *args, **options):
        ingest = MqttIngestWorker()
        workers = [ingest]
        if not options["no_publisher"]:
            workers.append(OutboxPublisherWorker())
        if not options["no_traffic"]:
            workers.append(SystemWorker())

        self.stdout.write(
            self.style.SUCCESS(
                "ACS MQTT worker: " + ", ".join(worker.worker_name for worker in workers)
            )
        )
        meta_provider = lambda: {
            "connected": ingest.connected,
            "messages": ingest.messages,
            "parse_errors": ingest.parse_errors,
            "cycles": {worker.worker_name: worker.cycles for worker in workers},
        }
        return run_workers(workers, component="mqtt_worker", meta_provider=meta_provider)
