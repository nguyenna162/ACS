"""Chạy toàn bộ pipeline đơn hàng (OrderMaster -> SubOrder -> MQTT ack).

Ví dụ triển khai không Docker::

    python manage.py run_acs_workers

Các worker: OrderMaster, SupplyPairing, SubOrderDispatcher, OrderSendMqtt,
SubOrderSentTimeoutChecker và (tùy chọn) AligoSync. Worker ghi nhịp sống vào
bảng core_system_heartbeat để /api/health/ready phát hiện tiến trình treo.
"""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from workers.schema_class import run_workers
from workers.acs_order_master import OrderMasterWorker
from workers.acs_order_send import (
    OrderSendMqtt,
    SubOrderDispatcher,
    SubOrderSentTimeoutChecker,
)
from workers.acs_supply_request_pairing import SupplyPairingWorker
from workers.aligo_sync import AligoSyncWorker

logger = logging.getLogger("workers")


class Command(BaseCommand):
    help = "Chạy các worker điều phối đơn hàng ACS (không cần Docker/Redis)."

    def add_arguments(self, parser):
        parser.add_argument("--no-aligo", action="store_true",
                            help="Không chạy worker đồng bộ Aligo dù ALIGO_ENABLED=true.")
        parser.add_argument("--no-timeout-checker", action="store_true",
                            help="Không chạy bộ kiểm tra SubOrder quá hạn.")

    def handle(self, *args, **options):
        workers = [
            OrderMasterWorker(),
            SupplyPairingWorker(),
            SubOrderDispatcher(),
            OrderSendMqtt(),
        ]
        if not options["no_timeout_checker"]:
            workers.append(SubOrderSentTimeoutChecker())
        if getattr(settings, "ALIGO_ENABLED", False) and not options["no_aligo"]:
            workers.append(AligoSyncWorker())

        self.stdout.write(
            self.style.SUCCESS(
                "Khởi động ACS workers: " + ", ".join(worker.worker_name for worker in workers)
            )
        )
        meta_provider = lambda: {
            "workers": {worker.worker_name: worker.cycles for worker in workers},
            "errors": sum(worker.errors for worker in workers),
        }
        return run_workers(workers, component="order_pipeline", meta_provider=meta_provider)
