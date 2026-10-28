import json
import time
import math
import threading
import logging
import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from amr.models import RobotProfile
from map.models import Node

logger = logging.getLogger(__name__)

class VirtualRobot:
    def __init__(self, serial_number: str, manufacturer: str, mqtt_client: mqtt.Client):
        self.serial = serial_number
        self.manufacturer = manufacturer
        self.mqtt_client = mqtt_client

        robot_record = RobotProfile.objects.filter(pk=serial_number).first()
        self.pos_x = robot_record.pos_x if robot_record else 0.0
        self.pos_y = robot_record.pos_y if robot_record else 0.0
        self.theta = robot_record.theta if robot_record else 0.0
        self.battery = robot_record.battery_level if robot_record else 100.0
        self.current_node_id = robot_record.current_node_id if robot_record else None
        self.state = 'IDLE'

        self.current_order = None
        self.pending_nodes = []
        self.paused = False
        self.step_delay = 1.5

    def on_order(self, order_msg: dict):
        order_id = order_msg.get('orderId')
        nodes = order_msg.get('nodes', [])
        self.current_order = order_id
        # Extract released nodes in order
        self.pending_nodes = [n for n in nodes if n.get('released') is True]
        if self.state != 'PAUSED':
            self.state = 'RUNNING'

    def on_instant_action(self, action_msg: dict):
        for act in action_msg.get('actions', []):
            atype = act.get('actionType')
            if atype == 'startPause':
                self.paused = True
                self.state = 'PAUSED'
            elif atype == 'stopPause':
                self.paused = False
                self.state = 'RUNNING' if self.pending_nodes else 'IDLE'
            elif atype == 'cancelOrder':
                self.current_order = None
                self.pending_nodes = []
                self.state = 'IDLE'

    def step(self):
        if not self.paused and self.pending_nodes:
            # Advance to the first target node in released list
            next_target = self.pending_nodes[0]
            target_nid = next_target.get('nodeId')
            pos = next_target.get('nodePosition', {})
            tx = float(pos.get('x', self.pos_x))
            ty = float(pos.get('y', self.pos_y))

            dx = tx - self.pos_x
            dy = ty - self.pos_y
            dist = math.hypot(dx, dy)

            # Move towards target
            if dist < 0.5:
                # Reached node
                self.pos_x = tx
                self.pos_y = ty
                self.current_node_id = target_nid
                self.pending_nodes.pop(0)
                if not self.pending_nodes:
                    self.state = 'IDLE'
            else:
                self.pos_x += (dx / dist) * min(dist, 5.0)
                self.pos_y += (dy / dist) * min(dist, 5.0)
                self.theta = math.atan2(dy, dx)
                self.battery = max(5.0, self.battery - 0.05)

        self.publish_state()

    def publish_state(self):
        state_topic = f"vda5050/v2/{self.manufacturer}/{self.serial}/state"
        payload = {
            "headerId": 1,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": "2.0.0",
            "manufacturer": self.manufacturer,
            "serialNumber": self.serial,
            "orderId": self.current_order or "",
            "orderUpdateId": 0,
            "lastNodeId": self.current_node_id or "",
            "operatingMode": self.state,
            "batteryState": {
                "batteryCharge": round(self.battery, 1),
                "charging": False
            },
            "agvPosition": {
                "x": round(self.pos_x, 2),
                "y": round(self.pos_y, 2),
                "theta": round(self.theta, 2),
                "mapId": "MAP_FACTORY_01",
                "positionInitialized": True
            },
            "velocity": {
                "vx": 1.0 if self.state == 'RUNNING' else 0.0
            },
            "safetyState": {
                "eStop": "NONE",
                "fieldViolation": False
            },
            "errors": []
        }
        self.mqtt_client.publish(state_topic, json.dumps(payload), qos=1)

class Command(BaseCommand):
    help = "Mô phỏng đội xe ảo (AMR_01, AMR_02, AMR_03) gửi và nhận bản tin VDA 5050 qua MQTT"

    def add_arguments(self, parser):
        parser.add_argument('--host', type=str, default=settings.MQTT_BROKER)
        parser.add_argument('--port', type=int, default=settings.MQTT_PORT)

    def handle(self, *args, **options):
        broker_host = options['host']
        broker_port = options['port']

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="AMR_FLEET_SIMULATOR")
        virtual_robots = {}

        def on_connect(c, userdata, flags, reason_code, properties):
            self.stdout.write(self.style.SUCCESS(f"Simulator connected to MQTT broker at {broker_host}:{broker_port}"))
            for serial in ["AMR_01", "AMR_02", "AMR_03"]:
                c.subscribe(f"vda5050/v2/SEVT_Robotics/{serial}/order")
                c.subscribe(f"vda5050/v2/SEVT_Robotics/{serial}/instantActions")
                # Publish online connection message
                c.publish(
                    f"vda5050/v2/SEVT_Robotics/{serial}/connection",
                    json.dumps({"connectionState": "ONLINE", "serialNumber": serial}),
                    qos=1, retain=True
                )

        def on_message(c, userdata, msg):
            parts = msg.topic.split("/")
            if len(parts) < 5:
                return
            serial, topic_type = parts[3], parts[4]
            robot = virtual_robots.get(serial)
            if not robot:
                return
            payload = json.loads(msg.payload.decode("utf-8"))
            if topic_type == "order":
                robot.on_order(payload)
            elif topic_type == "instantActions":
                robot.on_instant_action(payload)

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            client.connect(broker_host, broker_port, 60)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Cannot connect to broker ({e}). Make sure MQTT broker is running."))
            return

        client.loop_start()

        for serial in ["AMR_01", "AMR_02", "AMR_03"]:
            virtual_robots[serial] = VirtualRobot(serial, "SEVT_Robotics", client)

        self.stdout.write(self.style.SUCCESS("Virtual fleet simulation for AMR_01, AMR_02, AMR_03 started."))

        try:
            while True:
                for robot in virtual_robots.values():
                    robot.step()
                time.sleep(1.0)
        except KeyboardInterrupt:
            client.loop_stop()
            self.stdout.write(self.style.SUCCESS("Simulator stopped."))
