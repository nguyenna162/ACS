"""Worker FIFO quản lý OrderMaster: lập kế hoạch đơn và đồng bộ hoàn thành."""

import logging

from acs.models import OrderMaster, SubOrder
from acs.services import OrderMasterService
from workers.schema_class import WorkerThread

logger = logging.getLogger("workers.order_master")


class OrderMasterWorker(WorkerThread):
    def __init__(self, thread_name="acs_order_master", loop_sleep_s=1.0):
        super().__init__(thread_name=thread_name, worker_name=thread_name,
                         loop_sleep_s=loop_sleep_s)

    def task(self) -> None:
        planned = OrderMasterService.plan_pending_masters()
        synced = OrderMasterService.sync_sub_orders()
        completed = self._refresh_masters()
        if planned or synced or completed:
            self.logger.info(
                "OrderMaster cycle: planned=%s synced=%s completed=%s",
                planned, synced, completed,
            )

    def _refresh_masters(self) -> int:
        completed = 0
        masters = OrderMaster.objects.filter(
            status__in=["PLANNING", "PROCESSING"]
        ).order_by("created_at")[:20]
        for master in masters:
            previous = master.status
            status = master.refresh_status()
            if status == "COMPLETED" and previous != "COMPLETED":
                request = master.supply_request
                if request is not None and request.status != "COMPLETED":
                    request.status = "COMPLETED"
                    request.save(update_fields=["status", "updated_at"])
                SubOrder.objects.filter(order_master=master).exclude(
                    status="COMPLETED"
                ).update(status="COMPLETED")
                completed += 1
                self.logger.info("OrderMaster %s hoàn thành", master.master_id)
        return completed
