"""Worker ghép yêu cầu cấp vật tư với AMR (priority tiers + Dijkstra + SLA)."""

import logging

from django.conf import settings

from acs.services import SupplyService
from workers.schema_class import WorkerThread

logger = logging.getLogger("workers.supply_pairing")


class SupplyPairingWorker(WorkerThread):
    def __init__(self, thread_name="acs_supply_pairing", loop_sleep_s=1.0):
        super().__init__(thread_name=thread_name, worker_name=thread_name,
                         loop_sleep_s=loop_sleep_s)

    def task(self) -> None:
        result = SupplyService.pair_pending(
            limit=int(getattr(settings, "ACS_PAIRING_BATCH_SIZE", 50))
        )
        if result.get("paired") or result.get("failed"):
            self.logger.info("Ghép cặp: %s", result)
