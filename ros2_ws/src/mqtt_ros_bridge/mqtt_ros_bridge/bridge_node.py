import json
import math
import paho.mqtt.client as mqtt

try:
    import rclpy
    from rclpy.node import Node as RosNode
    from rclpy.action import ActionClient
    from geometry_msgs.msg import PoseStamped, Twist
    from nav2_msgs.action import NavigateThroughPoses
    HAVE_ROS2 = True
except ImportError:
    HAVE_ROS2 = False
    class RosNode:
        def __init__(self, name):
            self._name = name
        def declare_parameter(self, name, default_value):
            setattr(self, f"_{name}", default_value)
        def get_parameter(self, name):
            class Param:
                def __init__(self, val):
                    self.value = val
            val = getattr(self, f"_{name}", 'AMR_01' if 'serial' in name else ('SEVT_Robotics' if 'manufacturer' in name else '127.0.0.1'))
            return Param(val)
        def create_publisher(self, msg_type, topic, qos):
            class DummyPublisher:
                def publish(self, msg):
                    pass
            return DummyPublisher()
        def get_clock(self):
            class Clock:
                def now(self):
                    class Now:
                        def to_msg(self):
                            return None
                    return Now()
            return Clock()
        def get_logger(self):
            import logging
            return logging.getLogger('mqtt_ros_bridge')

class MqttRos2Bridge(RosNode):
    def __init__(self):
        super().__init__('mqtt_ros_bridge')
        self.declare_parameter('serial_number', 'AMR_01')
        self.declare_parameter('manufacturer', 'SEVT_Robotics')
        self.declare_parameter('mqtt_host', '127.0.0.1')

        self.serial = self.get_parameter('serial_number').value
        self.manufacturer = self.get_parameter('manufacturer').value
        mqtt_host = self.get_parameter('mqtt_host').value

        # ROS 2 Action Client (Nav2) & Publisher khóa vận tốc khẩn cấp
        if HAVE_ROS2:
            self.nav_client = ActionClient(self, NavigateThroughPoses, 'navigate_through_poses')
            self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        else:
            self.nav_client = None
            self.cmd_vel_pub = self.create_publisher(None, '/cmd_vel', 10)

        self.current_goal_handle = None

        # Khởi tạo MQTT Client kèm cấu hình Last Will and Testament (LWT)
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"BRIDGE_{self.serial}")
        lwt_topic = f"vda5050/v2/{self.manufacturer}/{self.serial}/connection"
        lwt_payload = json.dumps({"connectionState": "CONNECTIONBROKEN", "serialNumber": self.serial})
        self.mqtt_client.will_set(lwt_topic, payload=lwt_payload, qos=1, retain=True)

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        try:
            self.mqtt_client.connect(mqtt_host, 1883, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            self.get_logger().warn(f"Chưa thể kết nối tới MQTT Broker tại {mqtt_host}:1883 ({e})")

    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        base_topic = f"vda5050/v2/{self.manufacturer}/{self.serial}"
        client.subscribe(f"{base_topic}/order")
        client.subscribe(f"{base_topic}/instantActions")
        client.publish(
            f"{base_topic}/connection",
            json.dumps({"connectionState": "ONLINE", "serialNumber": self.serial}),
            qos=1, retain=True
        )

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            if msg.topic.endswith('/order'):
                self.process_vda5050_order(payload)
            elif msg.topic.endswith('/instantActions'):
                self.process_instant_actions(payload)
        except Exception as e:
            self.get_logger().error(f"Lỗi phân tích bản tin MQTT {msg.topic}: {e}")

    def process_vda5050_order(self, order_msg: dict):
        """
        Lọc danh sách các nút có cờ released == True (Vùng Base) và gửi Goal tới Nav2.
        Các nút có released == False (Vùng Horizon) được giữ lại chờ cập nhật.
        """
        released_nodes = [n for n in order_msg.get('nodes', []) if n.get('released') is True]
        if not released_nodes:
            return

        if not HAVE_ROS2:
            self.get_logger().info(f"Đã nhận Order {order_msg.get('orderId')} với {len(released_nodes)} nút Base đã mở khóa.")
            return

        goal_msg = NavigateThroughPoses.Goal()
        for node_data in released_nodes:
            pos = node_data['nodePosition']
            pose = PoseStamped()
            pose.header.frame_id = 'map'
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.position.x = float(pos['x'])
            pose.pose.position.y = float(pos['y'])

            yaw = float(pos.get('theta', 0.0))
            pose.pose.orientation.z = math.sin(yaw / 2.0)
            pose.pose.orientation.w = math.cos(yaw / 2.0)
            goal_msg.poses.append(pose)

        self.nav_client.wait_for_server()
        send_goal_future = self.nav_client.send_goal_async(goal_msg)
        send_goal_future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if goal_handle and goal_handle.accepted:
            self.current_goal_handle = goal_handle

    def process_instant_actions(self, action_msg: dict):
        """
        Xử lý lệnh khẩn cấp startPause / cancelOrder: Hủy Goal Nav2 và khóa vận tốc về 0 m/s.
        """
        for act in action_msg.get('actions', []):
            if act.get('actionType') in ('startPause', 'cancelOrder'):
                if self.current_goal_handle is not None and HAVE_ROS2:
                    self.current_goal_handle.cancel_goal_async()
                if HAVE_ROS2:
                    stop_twist = Twist()
                    self.cmd_vel_pub.publish(stop_twist)
                self.get_logger().warn(f"Đã thực thi lệnh khẩn cấp {act.get('actionType')}: Khóa vận tốc 0 m/s.")

def main(args=None):
    if HAVE_ROS2:
        rclpy.init(args=args)
        bridge = MqttRos2Bridge()
        rclpy.spin(bridge)
        bridge.destroy_node()
        rclpy.shutdown()
    else:
        import time
        bridge = MqttRos2Bridge()
        print(f"[mqtt_ros_bridge] Node đã khởi động cho robot {bridge.serial} ({bridge.manufacturer}).")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[mqtt_ros_bridge] Tắt node.")

if __name__ == '__main__':
    main()
