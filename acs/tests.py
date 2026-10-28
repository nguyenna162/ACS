from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from amr.models import RobotProfile
from map.models import MapTopology, Node, Edge
from map.pathfinding import a_star_search, euclidean_distance
from map.traffic_lock import TrafficController
from buffer.models import BufferZone, BufferSlot
from buffer.allocator import BufferAllocator
from acs.models import Order, OrderNode, OrderAction
from acs.dispatcher import OrderDispatcher
from acs.vda5050 import build_instant_action_payload, build_order_payload

class VDA5050SystemTestCase(TestCase):
    def setUp(self):
        # 1. Topology
        self.topology = MapTopology.objects.create(
            map_id="MAP_TEST_01",
            map_name="Test Factory Floor",
            version="2.0.0",
            is_active=True
        )

        # 2. Nodes
        # Grid: N_01 to N_15 with intersection at N_10
        self.nodes = {}
        coords = {
            "N_01": (0.0, 0.0),
            "N_02": (10.0, 0.0),
            "N_03": (20.0, 0.0),
            "N_04": (30.0, 0.0),
            "N_05": (30.0, 10.0),
            "N_06": (0.0, 20.0),
            "N_07": (10.0, 20.0),
            "N_08": (20.0, 20.0),
            "N_09": (30.0, 20.0),
            "N_10": (40.0, 20.0),  # Intersection
            "N_11": (50.0, 20.0),
            "N_12": (10.0, 30.0),
            "N_13": (40.0, 10.0),
            "N_14": (40.0, 30.0),
            "N_15": (60.0, 20.0),
        }
        for nid, (x, y) in coords.items():
            self.nodes[nid] = Node.objects.create(
                node_id=nid,
                map_topology=self.topology,
                pos_x=x,
                pos_y=y,
                theta=0.0
            )

        # 3. Edges
        connections = [
            ("N_01", "N_02", 10.0, "BI"),
            ("N_02", "N_03", 10.0, "BI"),
            ("N_03", "N_04", 10.0, "BI"),
            ("N_04", "N_05", 10.0, "BI"),
            ("N_05", "N_13", 10.0, "BI"),
            ("N_13", "N_10", 10.0, "BI"),
            ("N_10", "N_14", 10.0, "BI"),
            ("N_06", "N_07", 10.0, "BI"),
            ("N_07", "N_08", 10.0, "BI"),
            ("N_08", "N_09", 10.0, "BI"),
            ("N_09", "N_10", 10.0, "BI"),
            ("N_10", "N_11", 10.0, "BI"),
            ("N_11", "N_15", 10.0, "BI"),
            ("N_07", "N_12", 10.0, "BI"),
            ("N_02", "N_07", 20.0, "BI"),
        ]
        for s, e, d, direction in connections:
            Edge.objects.create(
                edge_id=f"E_{s}_{e}",
                start_node=self.nodes[s],
                end_node=self.nodes[e],
                distance=d,
                direction=direction,
                max_speed=1.2
            )

        # 4. Buffer Zones & Slots
        self.zone_storage = BufferZone.objects.create(
            zone_id="ZONE_STORAGE", map_topology=self.topology, zone_name="Storage", zone_type="STORAGE"
        )
        self.zone_line = BufferZone.objects.create(
            zone_id="ZONE_LINE", map_topology=self.topology, zone_name="Line", zone_type="LINE"
        )

        self.slot_pick = BufferSlot.objects.create(
            slot_id="SLOT_PICK_01", zone=self.zone_storage, node=self.nodes["N_01"],
            slot_status="OCCUPIED", pallet_code="PALLET_A100"
        )
        self.slot_drop = BufferSlot.objects.create(
            slot_id="SLOT_DROP_01", zone=self.zone_line, node=self.nodes["N_15"],
            slot_status="EMPTY"
        )

        # 5. Robots
        # AMR_01: At N_01, 90% Battery, IDLE
        self.amr_01 = RobotProfile.objects.create(
            serial_number="AMR_01", current_node=self.nodes["N_01"],
            battery_level=90.0, operational_state="IDLE"
        )
        # AMR_02: At N_06, 85% Battery, IDLE
        self.amr_02 = RobotProfile.objects.create(
            serial_number="AMR_02", current_node=self.nodes["N_06"],
            battery_level=85.0, operational_state="IDLE"
        )
        # AMR_03: At N_12, 22% Battery (< 25%), IDLE
        self.amr_03 = RobotProfile.objects.create(
            serial_number="AMR_03", current_node=self.nodes["N_12"],
            battery_level=22.0, operational_state="IDLE"
        )

        self.client = APIClient()

    def test_pathfinding_a_star(self):
        path, dist = a_star_search("N_01", "N_15")
        self.assertIsNotNone(path)
        self.assertEqual(path[0], "N_01")
        self.assertEqual(path[-1], "N_15")
        self.assertGreater(dist, 0.0)

    def test_acceptance_scenario_1_dispatch_optimization(self):
        """
        Kịch bản 1:
        Gửi yêu cầu điều phối src_slot = "SLOT_PICK_01", dst_slot = "SLOT_DROP_01".
        - AMR_03 (pin 22% < 25%) phải bị loại bỏ.
        - AMR_01 (ở ngay N_01, pin 90%) phải được chọn.
        - SLOT_DROP_01 chuyển sang RESERVED.
        - Thứ tự sequenceId là số chẵn.
        - Có lệnh pick tại điểm lấy và drop tại điểm trả.
        """
        order = OrderDispatcher.create_and_dispatch_order(
            order_id="ORD_TEST_001",
            src_slot_id="SLOT_PICK_01",
            dst_slot_id="SLOT_DROP_01"
        )

        # Verify assigned robot is AMR_01
        self.assertEqual(order.robot.serial_number, "AMR_01")
        self.assertEqual(order.order_status, "EXECUTING")

        # Verify destination slot reserved
        self.slot_drop.refresh_from_db()
        self.assertEqual(self.slot_drop.slot_status, "RESERVED")

        # Verify order nodes and sequenceIds
        order_nodes = list(order.order_nodes.all())
        self.assertGreaterEqual(len(order_nodes), 2)
        for on in order_nodes:
            self.assertEqual(on.sequence_id % 2, 0, "VDA 5050 sequenceId must be even for nodes")

        # Verify pick and drop actions
        first_node_actions = list(order_nodes[0].actions.all())
        last_node_actions = list(order_nodes[-1].actions.all())
        self.assertTrue(any(a.action_type == 'pick' for a in first_node_actions))
        self.assertTrue(any(a.action_type == 'drop' for a in last_node_actions))

        # Verify sliding horizon k=3 released
        released_nodes = [on for on in order_nodes if on.released]
        self.assertLessEqual(len(released_nodes), 3)
        self.assertTrue(order_nodes[0].released)

    def test_acceptance_scenario_2_intersection_traffic_locking(self):
        """
        Kịch bản 2:
        Chặn xung đột nút giao N_10.
        Khi robot 1 giữ khóa nút N_10, robot 2 tiến tới N_10 sẽ bị từ chối cấp khóa (released=False)
        và chuyển sang trạng thái BLOCKED.
        """
        # Manually lock N_10 for AMR_01
        node_10 = self.nodes["N_10"]
        node_10.is_occupied = True
        node_10.locked_by = self.amr_01
        node_10.save()

        # Create an order for AMR_02 going through N_08 -> N_09 -> N_10 -> N_11
        order_amr2 = Order.objects.create(
            order_id="ORD_CONFLICT_02",
            robot=self.amr_02,
            src_slot=self.slot_pick,
            dst_slot=self.slot_drop,
            order_status='EXECUTING'
        )
        on_08 = OrderNode.objects.create(order=order_amr2, node=self.nodes["N_08"], sequence_id=0, released=False)
        on_09 = OrderNode.objects.create(order=order_amr2, node=self.nodes["N_09"], sequence_id=2, released=False)
        on_10 = OrderNode.objects.create(order=order_amr2, node=self.nodes["N_10"], sequence_id=4, released=False)
        on_11 = OrderNode.objects.create(order=order_amr2, node=self.nodes["N_11"], sequence_id=6, released=False)

        # Update sliding window for AMR_02 at N_08 (window looks at N_08, N_09, N_10)
        TrafficController.update_sliding_window(order_amr2, "N_08")

        on_08.refresh_from_db()
        on_09.refresh_from_db()
        on_10.refresh_from_db()
        self.amr_02.refresh_from_db()

        # N_08 and N_09 should be released
        self.assertTrue(on_08.released)
        self.assertTrue(on_09.released)
        # N_10 is occupied by AMR_01, so on_10 must NOT be released
        self.assertFalse(on_10.released)
        # Robot operational state should be BLOCKED
        self.assertEqual(self.amr_02.operational_state, "BLOCKED")

    def test_acceptance_scenario_3_instant_actions(self):
        """
        Kịch bản 3:
        Kiểm tra sinh bản tin Instant Actions chuẩn VDA 5050 cho startPause, stopPause, cancelOrder.
        """
        payload_pause = build_instant_action_payload(self.amr_01, "startPause", "ACT_001")
        self.assertEqual(payload_pause["version"], "2.0.0")
        self.assertEqual(payload_pause["serialNumber"], "AMR_01")
        self.assertEqual(payload_pause["actions"][0]["actionType"], "startPause")
        self.assertEqual(payload_pause["actions"][0]["blockingType"], "HARD")

        # Test API endpoint
        response = self.client.post('/api/instant-actions/', {
            'serial_number': 'AMR_01',
            'action_type': 'startPause'
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "published")

    def test_vda5050_order_payload_structure(self):
        order = OrderDispatcher.create_and_dispatch_order(
            order_id="ORD_TEST_PAYLOAD",
            src_slot_id="SLOT_PICK_01",
            dst_slot_id="SLOT_DROP_01"
        )
        payload = build_order_payload(order)
        self.assertEqual(payload["version"], "2.0.0")
        self.assertEqual(payload["orderId"], "ORD_TEST_PAYLOAD")
        self.assertIn("nodes", payload)
        self.assertIn("edges", payload)
        # Verify node structure
        for n in payload["nodes"]:
            self.assertIn("nodeId", n)
            self.assertIn("sequenceId", n)
            self.assertIn("released", n)
            self.assertIn("nodePosition", n)
        # Verify edges sequenceIds are odd
        for e in payload["edges"]:
            self.assertEqual(e["sequenceId"] % 2, 1, "VDA 5050 edge sequenceId must be odd")

    def test_rest_api_order_dispatch(self):
        response = self.client.post('/api/orders/', {
            'src_slot': 'SLOT_PICK_01',
            'dst_slot': 'SLOT_DROP_01',
            'priority': 2
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['robot_serial'], 'AMR_01')
        self.assertEqual(response.data['order_status'], 'EXECUTING')
