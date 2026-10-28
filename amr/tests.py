"""Test ingest trạng thái AMR VDA 5050 và giám sát heartbeat."""

from django.test import TestCase
from django.utils import timezone

from map.models import MapTopology, Node
from amr import services as robot_services
from amr.models import RobotProfile, RobotTelemetry
from amr.services import RobotService


class RobotStateTests(TestCase):
    def setUp(self):
        robot_services._last_telemetry.clear()
        robot_services._last_broadcast.clear()
        self.topology = MapTopology.objects.create(
            map_id="MAP_R", map_name="Robot Map", version="1.0.0", is_active=True
        )
        self.node = Node.objects.create(
            node_id="N_01", map_topology=self.topology, pos_x=0.0, pos_y=0.0
        )
        self.robot = RobotProfile.objects.create(
            serial_number="AMR_01",
            manufacturer="SEVT_Robotics",
            current_node=self.node,
            operational_state="IDLE",
            is_online=True,
        )

    def _payload(self, **overrides):
        payload = {
            "headerId": 1,
            "manufacturer": "SEVT_Robotics",
            "serialNumber": "AMR_01",
            "orderId": "",
            "orderUpdateId": 0,
            "lastNodeId": "N_01",
            "driving": False,
            "paused": False,
            "newBaseRequest": True,
            "operatingMode": "AUTOMATIC",
            "zoneSetId": "ZS_01",
            "agvPosition": {"x": 1.5, "y": 2.5, "theta": 0.3, "mapId": "MAP_R"},
            "velocity": {"vx": 0.5},
            "batteryState": {"batteryCharge": 77.0},
            "safetyState": {"eStop": "NONE"},
            "errors": [],
            "information": [],
            "nodeStates": [{"nodeId": "N_01", "sequenceId": 0, "released": True}],
            "edgeStates": [],
            "actionStates": [],
            "loads": [],
        }
        payload.update(overrides)
        return payload

    def test_apply_state_updates_profile_and_telemetry(self):
        robot, changed = RobotService.apply_state(
            "SEVT_Robotics", "AMR_01", self._payload()
        )
        self.assertTrue(changed)
        robot.refresh_from_db()
        self.assertEqual(robot.battery_level, 77.0)
        self.assertAlmostEqual(robot.pos_x, 1.5)
        self.assertEqual(robot.current_node_id, "N_01")
        self.assertEqual(robot.zone_set_id, "ZS_01")
        self.assertTrue(robot.new_base_request)
        self.assertTrue(robot.is_online)
        self.assertEqual(robot.state_received_at is not None, True)
        self.assertEqual(RobotTelemetry.objects.filter(robot=robot).count(), 1)

    def test_older_state_is_ignored(self):
        RobotService.apply_state(
            "SEVT_Robotics",
            "AMR_01",
            self._payload(headerId=10, batteryState={"batteryCharge": 55.0}),
        )
        robot, changed = RobotService.apply_state(
            "SEVT_Robotics",
            "AMR_01",
            self._payload(headerId=9, batteryState={"batteryCharge": 11.0}),
        )
        self.assertFalse(changed)
        robot.refresh_from_db()
        self.assertEqual(robot.battery_level, 55.0)

    def test_fatal_error_sets_error_state(self):
        robot, _changed = RobotService.apply_state(
            "SEVT_Robotics",
            "AMR_01",
            self._payload(errors=[{"errorType": "hardware", "errorLevel": "FATAL"}]),
        )
        self.assertEqual(robot.operational_state, "ERROR")

    def test_unknown_robot_is_ignored_when_auto_register_disabled(self):
        robot, changed = RobotService.apply_state(
            "SEVT_Robotics", "AMR_NEW", self._payload(serialNumber="AMR_NEW"),
            allow_register=False,
        )
        self.assertIsNone(robot)
        self.assertFalse(changed)
        self.assertFalse(RobotProfile.objects.filter(pk="AMR_NEW").exists())

    def test_heartbeat_timeout_marks_offline(self):
        RobotProfile.objects.filter(pk="AMR_01").update(
            last_heartbeat=timezone.now() - timezone.timedelta(seconds=60)
        )
        changed = RobotService.monitor_heartbeats()
        self.assertGreaterEqual(changed, 1)
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.operational_state, "OFFLINE")
        self.assertFalse(self.robot.is_online)

    def test_connection_online_resets_offline_robot(self):
        RobotProfile.objects.filter(pk="AMR_01").update(
            operational_state="OFFLINE", is_online=False
        )
        robot = RobotService.handle_connection(
            "SEVT_Robotics", "AMR_01", {"connectionState": "ONLINE"}
        )
        self.assertEqual(robot.operational_state, "IDLE")
        self.assertTrue(robot.is_online)
