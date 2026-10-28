"""Smoke test pipeline MES trên dữ liệu demo (mặc định rollback, không đổi DB)."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from acs.models import OrderMaster, SubOrder, SupplyRequest
from acs.services import OrderMasterService, SupplyService
from acs.models import MqttOutboundMessage
from workers.acs_order_send import SubOrderDispatcher


class Command(BaseCommand):
    help = "Chạy thử SupplyRequest -> OrderMaster -> SubOrder -> Outbox rồi rollback."

    def add_arguments(self, parser):
        parser.add_argument("--src", default="SLOT_PICK_01")
        parser.add_argument("--dst", default="SLOT_DROP_01")
        parser.add_argument("--commit", action="store_true",
                            help="Giữ lại dữ liệu (mặc định rollback để an toàn).")

    def handle(self, *args, **options):
        request_id = f"SMOKE-{timezone.now():%Y%m%d%H%M%S}"
        try:
            with transaction.atomic():
                request = SupplyService.create_request(
                    request_id=request_id,
                    source="MANUAL",
                    src_slot_id=options["src"],
                    dst_slot_id=options["dst"],
                    priority=80,
                    auto_pair=True,
                )
                request.refresh_from_db()
                self.stdout.write(
                    f"SupplyRequest {request.request_id}: status={request.status} amr={request.amr_id}"
                )
                if request.order_master_id is None:
                    raise CommandError("Không ghép cặp được (kiểm tra seed_demo_data/robot/pin).")

                planned = OrderMasterService.plan_pending_masters()
                self.stdout.write(f"OrderMaster planned: {planned}")

                before = MqttOutboundMessage.objects.count()
                SubOrderDispatcher(loop_sleep_s=0.1).task()
                after = MqttOutboundMessage.objects.count()

                sub_order = SubOrder.objects.filter(
                    order_master_id=request.order_master_id
                ).select_related("order", "order_master").first()
                if sub_order is None:
                    raise CommandError("Không tạo được SubOrder.")
                self.stdout.write(
                    f"SubOrder {sub_order.sub_order_id}: status={sub_order.status} "
                    f"order={sub_order.order_id} anchor={sub_order.anchor_seq}"
                )
                self.stdout.write(
                    f"Outbox: +{after - before} bản tin (tổng {after})"
                )
                self.stdout.write(
                    f"OrderMaster {sub_order.order_master.master_id}: "
                    f"status={sub_order.order_master.status}"
                )

                if not options["commit"]:
                    transaction.set_rollback(True)
                    self.stdout.write(self.style.WARNING("Đã rollback (dùng --commit để giữ dữ liệu)."))
                else:
                    self.stdout.write(self.style.SUCCESS("Đã commit dữ liệu smoke test."))
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(f"Smoke test thất bại: {exc}") from exc
