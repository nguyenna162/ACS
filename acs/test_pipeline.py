"""Test end-to-end pipeline MES: SupplyRequest -> OrderMaster -> SubOrder -> Order."""

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from acs.models import SubOrder, SupplyRequest
from acs.services import OrderMasterService, SupplyService
from buffer.models import BufferSlot, BufferZone
from acs.models import MqttOutboundMessage
from map.models import Edge, MapTopology, Node
from amr import services as robot_services
from amr.models import RobotProfile
from amr.services import RobotService
from traffic.graph_distance import invalidate_cache
from workers.acs_order_send import SENT_TIMEOUT_SECONDS, OrderSendMqtt


class SupplyPipelineTests(TestCase):
    def setUp(self):
        invalidate_cache()
        robot_services._last_telemetry.clear()
        robot_services._last_broadcast.clear()
        self.client = APIClient()

        self.topology = MapTopology.objects.create(
            map_id="MAP_P", map_name="Pipeline Map", version="1.0.0", is_active=True
        )
        self.nodes = {
            node_id: Node.objects.create(
                node_id=node_id, map_topology=self.topology, pos_x=x, pos_y=0.0
            )
            for node_id, x in (("N_01", 0.0), ("N_02", 10.0), ("N_03", 20.0))
        }
        Edge.objects.create(
            edge_id="E_N_01_N_02", start_node=self.nodes["N_01"],
            end_node=self.nodes["N_02"], distance=10.0, direction="BI",
        )
        Edge.objects.create(
            edge_id="E_N_02_N_03", start_node=self.nodes["N_02"],
            end_node=self.nodes["N_03"], distance=10.0, direction="BI",
        )
        self.zone_src = BufferZone.objects.create(
            zone_id="ZONE_SRC", map_topology=self.topology,
            zone_name="Storage", zone_type="STORAGE",
        )
        self.zone_dst = BufferZone.objects.create(
            zone_id="ZONE_DST", map_topology=self.topology,
            zone_name="Line", zone_type="LINE",
        )
        self.src = BufferSlot.objects.create(
            slot_id="SRC", zone=self.zone_src, node=self.nodes["N_01"],
            slot_status="OCCUPIED", pallet_code="PALLET_P1",
            product_model="MODEL_X", buffer_type="STORAGE",
        )
        self.dst = BufferSlot.objects.create(
            slot_id="DST", zone=self.zone_dst, node=self.nodes["N_03"],
            slot_status="EMPTY", product_model="MODEL_X", buffer_type="LINE",
        )
        RobotProfile.objects.create(
            serial_number="AMR_01", manufacturer="SEVT_Robotics",
            current_node=self.nodes["N_01"], operational_state="IDLE",
            battery_level=90.0, is_online=True, is_idle=True,
        )
        invalidate_cache()

    def tearDown(self):
        invalidate_cache()

    def _create_paired_request(self, request_id="SR-T1"):
        request = SupplyService.create_request(
            request_id=request_id,
            src_slot_id="SRC",
            dst_slot_id="DST",
            priority=80,
            auto_pair=True,
        )
        request.refresh_from_db()
        return request

    def _planned_sub_order(self):
        request = self._create_paired_request()
        master = request.order_master
        result = OrderMasterService.plan_master(master)
        self.assertEqual(result["planned"], 1)
        return request, master, master.sub_orders.get()

    def test_supply_request_pairs_and_plans(self):
        request = self._create_paired_request()
        self.assertEqual(request.status, "PAIRED")
        self.assertIsNotNone(request.order_master_id)
        self.assertEqual(request.amr_id, "AMR_01")

        sub_order = request.order_master.sub_orders.get()
        self.assertEqual(sub_order.status, "PENDING")
        self.assertEqual(sub_order.amr_id, "AMR_01")
        self.assertTrue(sub_order.path.get("nodes"))

        result = OrderMasterService.plan_master(request.order_master)
        self.assertEqual(result["planned"], 1)
        sub_order.refresh_from_db()
        self.assertIsNotNone(sub_order.order_id)
        self.assertEqual(sub_order.order.order_status, "EXECUTING")
        request.order_master.refresh_from_db()
        self.assertEqual(request.order_master.status, "PROCESSING")

    def test_full_lifecycle_ack_and_completion(self):
        request, master, sub_order = self._planned_sub_order()
        order = sub_order.order

        RobotProfile.objects.filter(pk="AMR_01").update(
            order_id=order.order_id,
            order_update_id=sub_order.order_update_id,
            new_base_request=True,
            driving=True,
        )
        sub_order.refresh_from_db()
        sub_order = SubOrder.objects.select_related("amr", "order").get(pk=sub_order.pk)
        robot = RobotProfile.objects.get(pk="AMR_01")
        self.assertEqual(robot.order_id, order.order_id)
        self.assertEqual(sub_order.order_id, order.order_id)
        worker = OrderSendMqtt()
        self.assertEqual(worker._check_acked(sub_order), "ACKED")
        worker._process(sub_order)
        sub_order.refresh_from_db()
        self.assertEqual(sub_order.status, "ACKED")

        final_node = (
            order.order_nodes.order_by("-sequence_id").values_list("node_id", flat=True).first()
        )
        RobotService.apply_state(
            "SEVT_Robotics",
            "AMR_01",
            {
                "headerId": 1,
                "orderId": order.order_id,
                "orderUpdateId": 0,
                "lastNodeId": final_node,
                "driving": False,
                "agvPosition": {"x": 20.0, "y": 0.0, "theta": 0.0},
                "batteryState": {"batteryCharge": 80.0},
            },
        )

        order.refresh_from_db()
        master.refresh_from_db()
        request.refresh_from_db()
        sub_order.refresh_from_db()
        self.assertEqual(order.order_status, "COMPLETED")
        self.assertEqual(sub_order.status, "COMPLETED")
        self.assertEqual(master.status, "COMPLETED")
        self.assertEqual(request.status, "COMPLETED")
        self.assertEqual(BufferSlot.objects.get(pk="DST").slot_status, "OCCUPIED")
        self.assertEqual(BufferSlot.objects.get(pk="SRC").slot_status, "EMPTY")
        self.assertTrue(RobotProfile.objects.get(pk="AMR_01").is_idle)

    def test_resend_enqueues_outbox(self):
        _request, _master, sub_order = self._planned_sub_order()
        sub_order.mark_sent(anchor_seq=0)
        SubOrder.objects.filter(pk=sub_order.pk).update(
            sent_at=timezone.now() - timezone.timedelta(seconds=5),
            last_sent_at=timezone.now() - timezone.timedelta(seconds=10),
        )
        sub_order.refresh_from_db()
        sub_order = SubOrder.objects.select_related("amr", "order").get(pk=sub_order.pk)
        before = MqttOutboundMessage.objects.count()

        OrderSendMqtt()._process(sub_order)

        self.assertGreater(MqttOutboundMessage.objects.count(), before)
        sub_order.refresh_from_db()
        self.assertEqual(sub_order.resend_count, 1)
        self.assertEqual(sub_order.status, "SENT")

    def test_sent_timeout_fails_order_chain(self):
        request, master, sub_order = self._planned_sub_order()
        sub_order.mark_sent(anchor_seq=0)
        SubOrder.objects.filter(pk=sub_order.pk).update(
            sent_at=timezone.now() - timezone.timedelta(seconds=SENT_TIMEOUT_SECONDS + 5)
        )
        sub_order.refresh_from_db()
        sub_order = SubOrder.objects.select_related("amr", "order").get(pk=sub_order.pk)

        OrderSendMqtt()._process(sub_order)

        sub_order.refresh_from_db()
        master.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(sub_order.status, "FAILED")
        self.assertEqual(master.status, "FAILED")
        self.assertEqual(request.status, "FAILED")
        self.assertEqual(sub_order.order.order_status, "FAILED")

    def test_pairing_selects_slots_and_nearest_robot(self):
        request = SupplyService.create_request(
            request_id="SR-AUTO", product_model="MODEL_X", auto_pair=True
        )
        request.refresh_from_db()
        self.assertEqual(request.status, "PAIRED")
        self.assertEqual(request.src_slot_id, "SRC")
        self.assertEqual(request.dst_slot_id, "DST")
        self.assertEqual(request.amr_id, "AMR_01")

    def test_supply_request_api_and_fleet_schedule(self):
        response = self.client.post(
            "/api/supply-requests/",
            {"request_id": "SR-API", "src_slot": "SRC", "dst_slot": "DST", "priority": 90},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "PAIRED")
        self.assertEqual(SupplyRequest.objects.filter(request_id="SR-API").count(), 1)

        schedule = self.client.post(
            "/api/fleet/schedule/",
            {
                "tasks": [{"task_id": "T1", "start_node": "N_01", "end_node": "N_03"}],
                "robots": [
                    {"serial_number": "AMR_01", "current_node": "N_01", "battery_level": 90}
                ],
            },
            format="json",
        )
        self.assertEqual(schedule.status_code, 200, schedule.content)
        self.assertEqual(schedule.json()["assigned"], 1)

