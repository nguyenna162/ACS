# TÀI LIỆU HƯỚNG DẪN TRIỂN KHAI KỸ THUẬT HỆ THỐNG ĐIỀU KHIỂN ĐỘI XE TỰ HÀNH ACS (VDA 5050)

Tài liệu này cung cấp hướng dẫn triển khai toàn diện hệ thống **ACS (AGV/AMR Control System)** theo kiến trúc **Layered Monolith** kết nối tầng **Cyber** (Django 5.0, PostgreSQL 16, Redis 7.2, React 18) và tầng **Physical** (ROS 2 Humble, Nav2) qua giao thức công nghiệp **VDA 5050 v2.0 (MQTT)**.

---

## ⚠️ CẬP NHẬT KIẾN TRÚC v2 (2026) - OFFLINE, KHÔNG DOCKER/REDIS

Phiên bản hiện tại đã được refactor để chạy **trực tiếp trên máy chủ nhà máy**,
không Docker, không Redis, phục vụ **50 AMR**. Các mục Docker/Redis phía dưới chỉ
còn giá trị tham khảo lịch sử.

**Thay đổi chính**

- Realtime: bỏ `channels-redis`; dùng `InMemoryChannelLayer` + **Event Outbox DB**
  (`core_realtime_event`). WebSocket gửi snapshot đội xe 1s/lần + sự kiện nghiệp vụ.
- MQTT: mọi bản tin ACS → AMR đi qua **Transactional Outbox** (`core_mqtt_outbox`),
  retry/backoff, không mất tin khi broker gián đoạn. Broker: EMQX/Mosquitto/amqtt.
- Nghiệp vụ MES: `SupplyRequest → OrderMaster → SubOrder → Order` với ghép cặp
  **Hungarian + Dijkstra + phân tầng ưu tiên + SLA**; worker gửi đơn có
  ack/resend/timeout/anchor theo tài liệu dự án.
- An toàn: heartbeat 5s/10s, khóa node/edge TTL 45s, phát hiện & gỡ deadlock,
  giới hạn hàng đợi đơn, throttle lệnh điều khiển.
- Vận hành: `/api/health/live|ready|metrics`, log JSON xoay vòng, dọn dữ liệu tự động,
  system check chặn cấu hình production nguy hiểm.

**Cấu trúc thư mục hiện tại (v2, khớp ảnh tài liệu)**

```text
acs/                 # Django project + app nghiệp vụ (settings, urls, models, vda5050/order+action)
amr/                 # đội xe AMR (AmrVehicle, TopicControl, AmrStateLog)
buffer/              # bộ đệm + lịch trình
map/                 # bản đồ + vùng giao thông
traffic/             # thuật toán lập lịch/đặt chỗ (không phải Django app)
workers/             # worker pipeline (acs_order_master, acs_order_send, ...) + schema_class.py + acs_system.py (hạ tầng gộp + SystemWorker)
integrations/        # Aligo MES client
templates/ static/ media/ docs/
manage.py
```

> Các mục 2-7 dưới đây mô tả cấu trúc cũ (`config/` + `apps/`) và chỉ còn giá trị
> tham khảo lịch sử. Mã nguồn v2 đã dùng cấu trúc phẳng ở trên.

**Bắt đầu nhanh (native)**

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\
cp .env.example .env            # sửa PostgreSQL/MQTT/SECRET_KEY
set -a; . ./.env; set +a        # Windows: dùng runacs.bat
python manage.py migrate
python manage.py seed_demo_data
python manage.py test           # 35 test
./acs_start.sh                  # web + acs workers + mqtt worker
```

Tài liệu chi tiết:

- [`docs/SRS_ACS.md`](docs/SRS_ACS.md) - đặc tả tổng hợp từ `Docs_img/` và mapping
  module/model/thuật toán/worker với mã nguồn.
- [`docs/OFFLINE_DEPLOY.md`](docs/OFFLINE_DEPLOY.md) - cài đặt Linux/Windows,
  systemd, backup, xử lý sự cố, kiểm thử bàn giao.
- [`.env.example`](.env.example) - toàn bộ biến môi trường production.

> Các mục 2-7 dưới đây mô tả phiên bản cũ (Docker Compose + Redis + kiến trúc
> tham chiếu ROS 2). Với v2, thay `docker compose up -d` bằng PostgreSQL/MQTT cài
> native và dùng `./acs_start.sh` hoặc `runacs.bat`.


---

## 1. KIẾN TRÚC TRIỂN KHAI VÀ CẤU TRÚC THƯ MỤC

Hệ thống được đóng gói thành một khối Monolith gồm 04 ứng dụng nghiệp vụ (`robot`, `map`, `buffer`, `acs`) cùng chia sẻ cơ sở dữ liệu PostgreSQL để tận dụng cơ chế khóa dòng nguyên tử (`SELECT FOR UPDATE`), kết hợp tiến trình nền `mqtt_worker` và cầu nối `mqtt_ros_bridge`.

```text
acs_monolith_project/
├── docker-compose.yml                  # Hạ tầng PostgreSQL 16, Redis 7.2, Mosquitto 2.0
├── requirements.txt                    # Danh sách thư viện Python
├── manage.py
├── config/                             # Cấu hình máy chủ Django & ASGI WebSocket
│   ├── settings.py
│   ├── asgi.py
│   └── urls.py
├── apps/
│   ├── robot/                          # Phân hệ Quản lý đội xe (Fleet Manager)
│   │   ├── models.py                   # Bảng robot_profile, robot_telemetry
│   │   ├── services.py                 # Xử lý bản tin state, LWT & giám sát Heartbeat
│   │   └── consumers.py                # WebSocket đẩy tọa độ thời gian thực lên React
│   ├── map/                            # Phân hệ Bản đồ & Điều phối giao thông (Traffic Controller)
│   │   ├── models.py                   # Bảng map_topology, map_node, map_edge
│   │   ├── pathfinding.py              # Thuật toán tìm đường ngắn nhất A* (A-Star)
│   │   └── traffic_lock.py             # Khóa cửa sổ trượt (Sliding Horizon k=3) & gỡ Deadlock
│   ├── buffer/                         # Phân hệ Quản lý trạm đệm & Bãi đỗ (Buffer Manager)
│   │   ├── models.py                   # Bảng buffer_zone, buffer_slot
│   │   └── allocator.py                # Đặt chỗ (Reserve), giải phóng ô & điều xe về trạm sạc
│   └── acs/                            # Phân hệ Điều phối đơn hàng & Giao thức VDA 5050
│       ├── models.py                   # Bảng acs_order, acs_order_node, acs_order_action
│       ├── dispatcher.py               # Hàm mục tiêu chi phí gán xe & quản lý vòng đời đơn
│       ├── vda5050_builder.py          # Đóng gói JSON payload chuẩn VDA 5050 v2.0
│       └── management/commands/
│           └── run_mqtt_worker.py      # Daemon lắng nghe và xuất bản bản tin MQTT
└── ros2_ws/                            # Workspace tại tầng Physical (Robot AMR)
    └── src/mqtt_ros_bridge/
        └── mqtt_ros_bridge/
            └── bridge_node.py          # Node biên dịch hai chiều MQTT VDA 5050 <-> ROS 2 Nav2
```

---

## 2. THIẾT LẬP HẠ TẦNG DỊCH VỤ (DOCKER & CẤU HÌNH MÔI TRƯỜNG)

### 2.1. Khởi tạo `docker-compose.yml`
Cấu hình các dịch vụ cơ sở dữ liệu PostgreSQL 16, bộ nhớ đệm Redis 7.2 và trung gian truyền thông Eclipse Mosquitto 2.0.

```yaml
version: '3.9'

services:
  postgres:
    image: postgres:16-alpine
    container_name: acs_postgres
    environment:
      POSTGRES_DB: acs_db
      POSTGRES_USER: acs_user
      POSTGRES_PASSWORD: acs_password
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    command: postgres -c max_connections=200 -c deadlock_timeout=500ms

  redis:
    image: redis:7.2-alpine
    container_name: acs_redis
    ports:
      - "6379:6379"

  mosquitto:
    image: eclipse-mosquitto:2.0
    container_name: acs_mosquitto
    ports:
      - "1883:1883"
      - "9001:9001"
    volumes:
      - ./mosquitto/config/mosquitto.conf:/mosquitto/config/mosquitto.conf

volumes:
  pgdata:
```

### 2.2. Cấu hình `mosquitto.conf`
Hỗ trợ giao thức MQTT v5.0 và cơ chế Last Will and Testament (LWT):

```conf
listener 1883 0.0.0.0
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest stdout
max_keepalive 60
```

### 2.3. Thư viện cài đặt `requirements.txt`

```text
Django==5.0.4
djangorestframework==3.15.1
channels==4.1.0
channels-redis==4.2.0
daphne==4.1.2
psycopg[binary]==3.1.18
paho-mqtt==2.0.0
```

---

## 3. HIỆN THỰC HÓA CƠ SỞ DỮ LIỆU QUAN HỆ (DJANGO ORM - 3NF)

### 3.1. Phân hệ Quản lý đội xe (`apps/robot/models.py`)

```python
from django.db import models

class RobotProfile(models.Model):
    STATE_CHOICES = [
        ('INIT', 'Initializing'),
        ('IDLE', 'Idle'),
        ('RUNNING', 'Running'),
        ('WARNING', 'Warning'),
        ('BLOCKED', 'Blocked by Traffic'),
        ('ERROR', 'Hardware Error'),
        ('OFFLINE', 'Offline / Connection Broken'),
    ]

    serial_number = models.CharField(max_length=64, primary_key=True)
    manufacturer = models.CharField(max_length=64, default='SEVT_Robotics')
    current_node = models.ForeignKey(
        'map.Node', on_delete=models.SET_NULL, null=True, blank=True, related_name='robots_at_node'
    )
    battery_level = models.FloatField(default=100.0)
    operational_state = models.CharField(max_length=20, choices=STATE_CHOICES, default='INIT')
    pos_x = models.FloatField(default=0.0)
    pos_y = models.FloatField(default=0.0)
    theta = models.FloatField(default=0.0)
    last_heartbeat = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'robot_profile'


class RobotTelemetry(models.Model):
    id = models.BigAutoField(primary_key=True)
    robot = models.ForeignKey(RobotProfile, on_delete=models.CASCADE, related_name='telemetries')
    velocity_vx = models.FloatField(default=0.0)
    safety_state = models.JSONField(default=dict)
    error_codes = models.JSONField(default=list)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'robot_telemetry'
        indexes = [models.Index(fields=['robot', '-recorded_at'])]
```

### 3.2. Phân hệ Bản đồ & Giao thông (`apps/map/models.py`)

```python
from django.db import models

class MapTopology(models.Model):
    map_id = models.CharField(max_length=64, primary_key=True)
    version = models.CharField(max_length=32, default='1.0.0')
    map_name = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'map_topology'


class Node(models.Model):
    node_id = models.CharField(max_length=64, primary_key=True)
    map_topology = models.ForeignKey(MapTopology, on_delete=models.CASCADE, related_name='nodes')
    pos_x = models.FloatField()
    pos_y = models.FloatField()
    theta = models.FloatField(default=0.0)
    is_occupied = models.BooleanField(default=False)
    locked_by = models.ForeignKey(
        'robot.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='locked_nodes'
    )
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'map_node'


class Edge(models.Model):
    DIRECTION_CHOICES = [('UNI', 'Unidirectional'), ('BI', 'Bidirectional')]

    edge_id = models.CharField(max_length=64, primary_key=True)
    start_node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='outgoing_edges')
    end_node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name='incoming_edges')
    distance = models.FloatField()
    max_speed = models.FloatField(default=1.2)
    is_locked = models.BooleanField(default=False)
    locked_by = models.ForeignKey(
        'robot.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='locked_edges'
    )
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='UNI')

    class Meta:
        db_table = 'map_edge'
```

### 3.3. Phân hệ Trạm đệm (`apps/buffer/models.py`)

```python
from django.db import models

class BufferZone(models.Model):
    ZONE_TYPES = [
        ('STORAGE', 'Material Storage'),
        ('LINE', 'Assembly Line'),
        ('PARKING', 'Parking Area'),
        ('CHARGING', 'Charging Station'),
    ]
    zone_id = models.CharField(max_length=64, primary_key=True)
    map_topology = models.ForeignKey('map.MapTopology', on_delete=models.CASCADE)
    zone_name = models.CharField(max_length=128)
    zone_type = models.CharField(max_length=32, choices=ZONE_TYPES)
    capacity = models.IntegerField(default=1)

    class Meta:
        db_table = 'buffer_zone'


class BufferSlot(models.Model):
    SLOT_STATUS = [
        ('EMPTY', 'Empty'),
        ('RESERVED', 'Reserved by Order'),
        ('OCCUPIED', 'Occupied'),
    ]
    slot_id = models.CharField(max_length=64, primary_key=True)
    zone = models.ForeignKey(BufferZone, on_delete=models.CASCADE, related_name='slots')
    node = models.OneToOneField('map.Node', on_delete=models.CASCADE, related_name='buffer_slot')
    slot_status = models.CharField(max_length=16, choices=SLOT_STATUS, default='EMPTY')
    pallet_code = models.CharField(max_length=64, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buffer_slot'
```

### 3.4. Phân hệ Điều phối đơn hàng (`apps/acs/models.py`)

```python
from django.db import models

class Order(models.Model):
    ORDER_STATUS = [
        ('CREATED', 'Created'),
        ('ASSIGNED', 'Assigned to Robot'),
        ('EXECUTING', 'Executing'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('FAILED', 'Failed'),
    ]
    order_id = models.CharField(max_length=64, primary_key=True)
    robot = models.ForeignKey('robot.RobotProfile', on_delete=models.SET_NULL, null=True, blank=True)
    src_slot = models.ForeignKey('buffer.BufferSlot', on_delete=models.PROTECT, related_name='source_orders')
    dst_slot = models.ForeignKey('buffer.BufferSlot', on_delete=models.PROTECT, related_name='dest_orders')
    order_update_id = models.IntegerField(default=0)
    order_status = models.CharField(max_length=20, choices=ORDER_STATUS, default='CREATED')
    priority = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'acs_order'


class OrderNode(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='order_nodes')
    node = models.ForeignKey('map.Node', on_delete=models.PROTECT)
    sequence_id = models.IntegerField()  # Số chẵn: 0, 2, 4...
    released = models.BooleanField(default=False)
    node_status = models.CharField(max_length=20, default='PENDING')  # PENDING, PASSED

    class Meta:
        db_table = 'acs_order_node'
        ordering = ['sequence_id']


class OrderAction(models.Model):
    ACTION_TYPES = [('pick', 'Pick Pallet'), ('drop', 'Drop Pallet'), ('charge', 'Charge Battery')]
    BLOCKING_TYPES = [('NONE', 'None'), ('SOFT', 'Soft'), ('HARD', 'Hard')]

    action_id = models.CharField(max_length=64, primary_key=True)
    order_node = models.ForeignKey(OrderNode, on_delete=models.CASCADE, related_name='actions')
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    blocking_type = models.CharField(max_length=10, choices=BLOCKING_TYPES, default='HARD')
    action_status = models.CharField(max_length=20, default='WAITING')
    params = models.JSONField(default=list)

    class Meta:
        db_table = 'acs_order_action'
```

---

## 4. HIỆN THỰC HÓA CÁC THUẬT TOÁN ĐIỀU PHỐI CỐT LÕI

### 4.1. Thuật toán Tìm đường ngắn nhất $A^*$ (`apps/map/pathfinding.py`)
Sử dụng hàm đánh giá $f(n) = g(n) + h(n)$ với hàm Heuristic khoảng cách Euclid:

```python
import heapq
import math
from typing import List, Tuple, Optional
from apps.map.models import Node, Edge

def euclidean_distance(n1: Node, n2: Node) -> float:
    return math.hypot(n1.pos_x - n2.pos_x, n1.pos_y - n2.pos_y)

def a_star_search(start_node_id: str, goal_node_id: str, blocked_nodes: set = None) -> Tuple[Optional[List[str]], float]:
    """
    Trả về (Danh sách node_id theo lộ trình ngắn nhất, tổng khoảng cách)
    """
    blocked_nodes = blocked_nodes or set()
    nodes = {n.node_id: n for n in Node.objects.all()}
    if start_node_id not in nodes or goal_node_id not in nodes:
        return None, float('inf')

    adj = {n_id: [] for n_id in nodes}
    for edge in Edge.objects.select_related('start_node', 'end_node').all():
        adj[edge.start_node_id].append((edge.end_node_id, edge.distance))
        if edge.direction == 'BI':
            adj[edge.end_node_id].append((edge.start_node_id, edge.distance))

    open_set = [(0.0, start_node_id)]
    came_from = {}
    g_score = {n_id: float('inf') for n_id in nodes}
    g_score[start_node_id] = 0.0

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal_node_id:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path, g_score[goal_node_id]

        for neighbor, weight in adj[current]:
            if neighbor in blocked_nodes and neighbor != goal_node_id:
                continue
            tentative_g = g_score[current] + weight
            if tentative_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + euclidean_distance(nodes[neighbor], nodes[goal_node_id])
                heapq.heappush(open_set, (f_score, neighbor))

    return None, float('inf')
```

### 4.2. Thuật toán Khóa đồ thị Cửa sổ trượt & Gỡ Deadlock (`apps/map/traffic_lock.py`)
Áp dụng cơ chế khóa dòng bi quan (`select_for_update()`) trên PostgreSQL với kích thước cửa sổ trượt $k = 3$:

```python
from django.db import transaction
from django.utils import timezone
from apps.map.models import Node, Edge
from apps.robot.models import RobotProfile
from apps.acs.models import Order, OrderNode

HORIZON_WINDOW_K = 3

class TrafficController:

    @staticmethod
    @transaction.atomic
    def update_sliding_window(order: Order, current_node_id: str) -> bool:
        """
        1. Giải phóng các nút robot đã đi qua (trước current_node_id).
        2. Cấp phát khóa tối đa k=3 nút tiếp theo nếu không bị xe khác chiếm giữ.
        Trả về True nếu có sự thay đổi cờ released để phát bản tin cập nhật order.
        """
        robot = RobotProfile.objects.select_for_update().get(pk=order.robot_id)
        order_nodes = list(OrderNode.objects.select_for_update().filter(order=order).order_by('sequence_id'))

        # Xác định vị trí hiện tại trong chuỗi lộ trình
        current_idx = 0
        for idx, onode in enumerate(order_nodes):
            if onode.node_id == current_node_id:
                current_idx = idx
                break

        # Bước 1: Mở khóa các nút đã đi qua
        passed_node_ids = [on.node_id for on in order_nodes[:current_idx]]
        if passed_node_ids:
            Node.objects.select_for_update().filter(
                node_id__in=passed_node_ids, locked_by=robot
            ).update(is_occupied=False, locked_by=None, locked_at=None)
            OrderNode.objects.filter(
                id__in=[on.id for on in order_nodes[:current_idx]]
            ).update(node_status='PASSED')

        # Bước 2: Xét cửa sổ k=3 nút tiếp theo từ current_idx
        window_candidates = order_nodes[current_idx : current_idx + HORIZON_WINDOW_K]
        candidate_ids = [on.node_id for on in window_candidates]

        # Khóa dòng theo thứ tự bảng chữ cái của node_id để tránh DB-level deadlock
        db_nodes = {
            n.node_id: n
            for n in Node.objects.select_for_update().filter(node_id__in=sorted(candidate_ids)).order_by('node_id')
        }

        released_changed = False
        blocked = False

        for onode in window_candidates:
            target_node = db_nodes[onode.node_id]
            if not blocked and (not target_node.is_occupied or target_node.locked_by_id == robot.serial_number):
                # Cấp khóa nút
                target_node.is_occupied = True
                target_node.locked_by = robot
                target_node.locked_at = timezone.now()
                target_node.save(update_fields=['is_occupied', 'locked_by', 'locked_at'])

                if not onode.released:
                    onode.released = True
                    onode.save(update_fields=['released'])
                    released_changed = True
            else:
                # Phát hiện nút giao N_x đã bị robot khác khóa -> Dừng cấp phát từ N_x trở đi
                blocked = True
                if onode.released:
                    onode.released = False
                    onode.save(update_fields=['released'])
                    released_changed = True

        # Cập nhật trạng thái vận hành của Robot
        new_state = 'BLOCKED' if blocked else 'RUNNING'
        if robot.operational_state != new_state:
            robot.operational_state = new_state
            robot.save(update_fields=['operational_state'])

        if released_changed:
            order.order_update_id += 1
            order.save(update_fields=['order_update_id'])

        return released_changed

    @staticmethod
    def detect_circular_wait() -> list:
        """
        Xây dựng Đồ thị cấp phát tài nguyên (Wait-For Graph) và dùng DFS tìm chu trình Deadlock.
        """
        wait_for = {}
        blocked_orders = Order.objects.filter(order_status='EXECUTING', robot__operational_state='BLOCKED')

        for order in blocked_orders:
            next_unreleased = order.order_nodes.filter(released=False, node_status='PENDING').first()
            if next_unreleased and next_unreleased.node.is_occupied:
                holder_robot_id = next_unreleased.node.locked_by_id
                if holder_robot_id and holder_robot_id != order.robot_id:
                    wait_for[order.robot_id] = holder_robot_id

        visited, rec_stack, cycles = set(), set(), []

        def dfs(robot_id, path):
            visited.add(robot_id)
            rec_stack.add(robot_id)
            next_r = wait_for.get(robot_id)
            if next_r:
                if next_r not in visited:
                    dfs(next_r, path + [next_r])
                elif next_r in rec_stack:
                    cycles.append(path[path.index(next_r):])
            rec_stack.remove(robot_id)

        for r_id in list(wait_for.keys()):
            if r_id not in visited:
                dfs(r_id, [r_id])

        return cycles
```

### 4.3. Hàm mục tiêu chi phí Gán xe & Quản lý Buffer (`apps/acs/dispatcher.py`)
Tính toán hàm mục tiêu tối thiểu hóa chi phí kết hợp khoảng cách đồ thị $A^*$ và dung lượng pin:

$$C_{total} = w_1 \cdot \frac{D(N_{robot}, N_{src})}{D_{max}} + w_2 \cdot \left(1 - \frac{B_{level}}{100}\right)$$

Với $w_1 = 0.7$, $w_2 = 0.3$ và điều kiện ràng buộc $B_{level} \ge 25\%$.

```python
from django.db import transaction
from apps.robot.models import RobotProfile
from apps.buffer.models import BufferSlot
from apps.acs.models import Order, OrderNode, OrderAction
from apps.map.pathfinding import a_star_search
from apps.map.traffic_lock import TrafficController

W1_DISTANCE = 0.7
W2_BATTERY = 0.3
MIN_BATTERY_THRESHOLD = 25.0
D_MAX_MAP = 100.0  # Chiều dài chuẩn hóa tối đa của bản đồ nhà xưởng (m)

class OrderDispatcher:

    @staticmethod
    @transaction.atomic
    def create_and_dispatch_order(order_id: str, src_slot_id: str, dst_slot_id: str, priority: int = 1) -> Order:
        # 1. Kiểm tra tính hợp lệ và giữ chỗ tại App buffer
        src_slot = BufferSlot.objects.select_for_update().get(pk=src_slot_id)
        dst_slot = BufferSlot.objects.select_for_update().get(pk=dst_slot_id)

        if src_slot.slot_status != 'OCCUPIED':
            raise ValueError(f"Ô nguồn {src_slot_id} không có hàng (Trạng thái: {src_slot.slot_status}).")
        if dst_slot.slot_status != 'EMPTY':
            raise ValueError(f"Ô đích {dst_slot_id} không trống (Trạng thái: {dst_slot.slot_status}).")

        dst_slot.slot_status = 'RESERVED'
        dst_slot.save(update_fields=['slot_status'])

        # 2. Lọc ứng viên Robot IDLE và mức pin >= 25%
        candidates = RobotProfile.objects.select_for_update().filter(
            operational_state='IDLE',
            battery_level__gte=MIN_BATTERY_THRESHOLD
        )

        best_robot = None
        min_cost = float('inf')
        best_path_to_src = []

        for robot in candidates:
            path_to_src, dist = a_star_search(robot.current_node_id, src_slot.node_id)
            if path_to_src is None:
                continue

            cost = W1_DISTANCE * (dist / D_MAX_MAP) + W2_BATTERY * (1.0 - (robot.battery_level / 100.0))
            if cost < min_cost:
                min_cost = cost
                best_robot = robot
                best_path_to_src = path_to_src

        if not best_robot:
            raise RuntimeError("Không tìm thấy robot khả dụng thỏa mãn điều kiện pin và lộ trình.")

        # 3. Tìm lộ trình từ điểm lấy hàng (src_slot) tới điểm trả hàng (dst_slot)
        path_src_to_dst, _ = a_star_search(src_slot.node_id, dst_slot.node_id)
        if not path_src_to_dst:
            raise RuntimeError("Không tìm thấy đường đi hợp lệ từ điểm lấy hàng tới điểm trả hàng.")

        full_node_path = best_path_to_src[:-1] + path_src_to_dst

        # 4. Khởi tạo bản ghi Order và chuỗi OrderNode + OrderAction
        order = Order.objects.create(
            order_id=order_id,
            robot=best_robot,
            src_slot=src_slot,
            dst_slot=dst_slot,
            order_status='EXECUTING',
            priority=priority
        )

        pick_assigned = False
        for idx, n_id in enumerate(full_node_path):
            onode = OrderNode.objects.create(
                order=order,
                node_id=n_id,
                sequence_id=idx * 2,  # Số chẵn theo chuẩn VDA 5050
                released=False
            )
            if n_id == src_slot.node_id and not pick_assigned:
                OrderAction.objects.create(
                    action_id=f"ACT_PICK_{order_id}",
                    order_node=onode,
                    action_type='pick',
                    blocking_type='HARD',
                    params=[{"key": "stationType", "value": src_slot.slot_id}]
                )
                pick_assigned = True
            elif idx == len(full_node_path) - 1 and n_id == dst_slot.node_id:
                OrderAction.objects.create(
                    action_id=f"ACT_DROP_{order_id}",
                    order_node=onode,
                    action_type='drop',
                    blocking_type='HARD',
                    params=[{"key": "stationType", "value": dst_slot.slot_id}]
                )

        # 5. Kích hoạt khóa cửa sổ trượt k=3 nút đầu tiên
        TrafficController.update_sliding_window(order, best_robot.current_node_id)
        return order
```

### 4.4. Bộ đóng gói bản tin JSON chuẩn VDA 5050 (`apps/acs/vda5050_builder.py`)

```python
from datetime import datetime, timezone
from apps.acs.models import Order
from apps.map.models import Edge

class VDA5050Builder:

    @staticmethod
    def build_order_payload(order: Order, header_id: int = 1) -> dict:
        robot = order.robot
        order_nodes = list(order.order_nodes.select_related('node').prefetch_related('actions').all())

        nodes_payload = []
        edges_payload = []

        for idx, onode in enumerate(order_nodes):
            n = onode.node
            actions_list = [
                {
                    "actionId": act.action_id,
                    "actionType": act.action_type,
                    "blockingType": act.blocking_type,
                    "actionParameters": act.params
                }
                for act in onode.actions.all()
            ]
            nodes_payload.append({
                "nodeId": n.node_id,
                "sequenceId": onode.sequence_id,
                "released": onode.released,
                "nodePosition": {
                    "x": n.pos_x,
                    "y": n.pos_y,
                    "theta": n.theta,
                    "mapId": n.map_topology_id
                },
                "actions": actions_list
            })

            # Sinh cạnh nối (Edge) mang sequenceId lẻ giữa nút hiện tại và nút kế tiếp
            if idx < len(order_nodes) - 1:
                next_onode = order_nodes[idx + 1]
                edge_obj = Edge.objects.filter(
                    start_node_id=n.node_id, end_node_id=next_onode.node_id
                ).first()
                max_speed = edge_obj.max_speed if edge_obj else 1.0
                edge_id = edge_obj.edge_id if edge_obj else f"E_{n.node_id}_{next_onode.node_id}"

                edges_payload.append({
                    "edgeId": edge_id,
                    "sequenceId": onode.sequence_id + 1,
                    "released": bool(onode.released and next_onode.released),
                    "startNodeId": n.node_id,
                    "endNodeId": next_onode.node_id,
                    "maxSpeed": max_speed
                })

        return {
            "headerId": header_id,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z",
            "version": "2.0.0",
            "manufacturer": robot.manufacturer,
            "serialNumber": robot.serial_number,
            "orderId": order.order_id,
            "orderUpdateId": order.order_update_id,
            "nodes": nodes_payload,
            "edges": edges_payload
        }

    @staticmethod
    def build_instant_action_payload(robot, action_type: str, action_id: str) -> dict:
        """
        action_type: 'startPause' | 'stopPause' | 'cancelOrder'
        """
        return {
            "headerId": 1,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z",
            "version": "2.0.0",
            "manufacturer": robot.manufacturer,
            "serialNumber": robot.serial_number,
            "actions": [
                {
                    "actionId": action_id,
                    "actionType": action_type,
                    "blockingType": "HARD"
                }
            ]
        }
```

---

## 5. TIẾN TRÌNH NỀN MQTT WORKER & GIÁM SÁT NHỊP TIM HEARTBEAT

Tạo Management Command tại `apps/acs/management/commands/run_mqtt_worker.py` để duy trì kết nối MQTT hai chiều và chạy luồng kiểm tra nhịp tim mỗi 1 giây ($T_{now} - T_{last\_heartbeat} > 5\text{ s}$ chuyển sang `WARNING`; $> 10\text{ s}$ chuyển sang `OFFLINE` và khóa vùng an toàn):

```python
import json
import time
import threading
import paho.mqtt.client as mqtt
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.robot.models import RobotProfile, RobotTelemetry
from apps.acs.models import Order
from apps.acs.vda5050_builder import VDA5050Builder
from apps.map.traffic_lock import TrafficController
from apps.map.models import Node

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

class Command(BaseCommand):
    help = "Chạy tiến trình nền MQTT Worker chuẩn VDA 5050 và bộ giám sát Heartbeat"

    def handle(self, *args, **options):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ACS_MASTER_CONTROLLER")
        client.on_connect = self.on_connect
        client.on_message = self.on_message

        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)

        # Khởi chạy luồng giám sát Heartbeat định kỳ mỗi 1 giây
        hb_thread = threading.Thread(target=self.heartbeat_monitor_loop, daemon=True)
        hb_thread.start()

        self.stdout.write(self.style.SUCCESS("ACS MQTT Worker đã khởi động..."))
        client.loop_forever()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        client.subscribe("vda5050/v2/+/+/state")
        client.subscribe("vda5050/v2/+/+/connection")

    def on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) < 5:
            return
        manufacturer, serial_number, topic_type = parts[2], parts[3], parts[4]
        payload = json.loads(msg.payload.decode("utf-8"))

        if topic_type == "connection":
            self.handle_connection(serial_number, payload)
        elif topic_type == "state":
            self.handle_state(client, manufacturer, serial_number, payload)

    def handle_connection(self, serial_number: str, payload: dict):
        conn_state = payload.get("connectionState", "OFFLINE")
        robot = RobotProfile.objects.filter(pk=serial_number).first()
        if not robot:
            return
        if conn_state in ("OFFLINE", "CONNECTIONBROKEN"):
            robot.operational_state = "OFFLINE"
            robot.save(update_fields=["operational_state"])
            if robot.current_node_id:
                Node.objects.filter(pk=robot.current_node_id).update(
                    is_occupied=True, locked_by=robot, locked_at=timezone.now()
                )

    def handle_state(self, client, manufacturer: str, serial_number: str, payload: dict):
        pos = payload.get("agvPosition", {})
        batt = payload.get("batteryState", {}).get("batteryCharge", 100.0)
        last_node_id = payload.get("lastNodeId")

        robot, _ = RobotProfile.objects.update_or_create(
            serial_number=serial_number,
            defaults={
                "manufacturer": manufacturer,
                "pos_x": pos.get("x", 0.0),
                "pos_y": pos.get("y", 0.0),
                "theta": pos.get("theta", 0.0),
                "battery_level": batt,
                "current_node_id": last_node_id if last_node_id else None,
                "last_heartbeat": timezone.now(),
            }
        )

        RobotTelemetry.objects.create(
            robot=robot,
            velocity_vx=payload.get("velocity", {}).get("vx", 0.0),
            safety_state=payload.get("safetyState", {}),
            error_codes=payload.get("errors", [])
        )

        # Kiểm tra đơn hàng đang chạy để trượt cửa sổ khóa (Sliding Window)
        active_order = Order.objects.filter(robot=robot, order_status="EXECUTING").first()
        if active_order and last_node_id:
            changed = TrafficController.update_sliding_window(active_order, last_node_id)
            if changed:
                order_json = VDA5050Builder.build_order_payload(active_order)
                order_topic = f"vda5050/v2/{manufacturer}/{serial_number}/order"
                client.publish(order_topic, json.dumps(order_json), qos=1)

    def heartbeat_monitor_loop(self):
        while True:
            now = timezone.now()
            for robot in RobotProfile.objects.exclude(operational_state="OFFLINE"):
                delta = (now - robot.last_heartbeat).total_seconds()
                if delta > 10.0:
                    robot.operational_state = "OFFLINE"
                    robot.save(update_fields=["operational_state"])
                    if robot.current_node_id:
                        Node.objects.filter(pk=robot.current_node_id).update(
                            is_occupied=True, locked_by=robot, locked_at=now
                        )
                elif delta > 5.0 and robot.operational_state not in ("WARNING", "ERROR"):
                    robot.operational_state = "WARNING"
                    robot.save(update_fields=["operational_state"])
            time.sleep(1.0)
```

---

## 6. HIỆN THỰC HÓA CẦU NỐI TẦNG PHYSICAL (`mqtt_ros_bridge` TRÊN ROS 2 HUMBLE)

Triển khai file `ros2_ws/src/mqtt_ros_bridge/mqtt_ros_bridge/bridge_node.py` trên từng robot vật lý hoặc trong môi trường mô phỏng Gazebo để biên dịch bản tin `order` và `instantActions` sang `NavigateThroughPoses` của ROS 2 Nav2:

```python
import json
import math
import rclpy
from rclpy.node import Node as RosNode
from rclpy.action import ActionClient
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateThroughPoses
import paho.mqtt.client as mqtt

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
        self.nav_client = ActionClient(self, NavigateThroughPoses, 'navigate_through_poses')
        self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.current_goal_handle = None

        # Khởi tạo MQTT Client kèm cấu hình Last Will and Testament (LWT)
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"BRIDGE_{self.serial}")
        lwt_topic = f"vda5050/v2/{self.manufacturer}/{self.serial}/connection"
        lwt_payload = json.dumps({"connectionState": "CONNECTIONBROKEN", "serialNumber": self.serial})
        self.mqtt_client.will_set(lwt_topic, payload=lwt_payload, qos=1, retain=True)

        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.connect(mqtt_host, 1883, 60)
        self.mqtt_client.loop_start()

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
        payload = json.loads(msg.payload.decode('utf-8'))
        if msg.topic.endswith('/order'):
            self.process_vda5050_order(payload)
        elif msg.topic.endswith('/instantActions'):
            self.process_instant_actions(payload)

    def process_vda5050_order(self, order_msg: dict):
        """
        Lọc danh sách các nút có cờ released == True (Vùng Base) và gửi Goal tới Nav2.
        Các nút có released == False (Vùng Horizon) được giữ lại chờ cập nhật.
        """
        released_nodes = [n for n in order_msg.get('nodes', []) if n.get('released') is True]
        if not released_nodes:
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
        if goal_handle.accepted:
            self.current_goal_handle = goal_handle

    def process_instant_actions(self, action_msg: dict):
        """
        Xử lý lệnh khẩn cấp startPause / cancelOrder: Hủy Goal Nav2 và khóa vận tốc về 0 m/s.
        """
        for act in action_msg.get('actions', []):
            if act.get('actionType') in ('startPause', 'cancelOrder'):
                if self.current_goal_handle is not None:
                    self.current_goal_handle.cancel_goal_async()
                stop_twist = Twist()
                self.cmd_vel_pub.publish(stop_twist)
                self.get_logger().warn(f"Đã thực thi lệnh khẩn cấp {act.get('actionType')}: Khóa vận tốc 0 m/s.")

def main(args=None):
    rclpy.init(args=args)
    bridge = MqttRos2Bridge()
    rclpy.spin(bridge)
    bridge.destroy_node()
    rclpy.shutdown()
```

---

## 7. QUY TRÌNH KHỞI CHẠY VÀ KIỂM THỬ THỰC NGHIỆM

### Bước 1: Khởi chạy hạ tầng cơ sở dữ liệu và MQTT Broker
```bash
docker compose up -d
```

### Bước 2: Khởi tạo cấu trúc bảng trên PostgreSQL và nạp dữ liệu bản đồ
```bash
python manage.py makemigrations robot map buffer acs
python manage.py migrate
```

### Bước 3: Khởi chạy máy chủ ASGI (API + WebSocket) và tiến trình MQTT Worker
```bash
# Terminal 1: Chạy máy chủ Django ASGI trên cổng 8000
daphne -b 0.0.0.0 -p 8000 config.asgi:application

# Terminal 2: Chạy tiến trình nền lắng nghe và điều phối VDA 5050
python manage.py run_mqtt_worker
```

### Bước 4: Khởi chạy mô phỏng 03 Robot trên ROS 2 Humble & Cầu nối
```bash
# Terminal 3: Khởi chạy node cầu nối cho AMR_01, AMR_02, AMR_03
ros2 run mqtt_ros_bridge bridge_node --ros-args -p serial_number:=AMR_01
```

### Bước 5: Kiểm thử các kịch bản nghiệm thu
1. **Kịch bản điều phối đơn lẻ:** Gửi request `POST /api/orders/` từ giao diện React với `src_slot = "SLOT_PICK_01"`, `dst_slot = "SLOT_DROP_01"`. Kiểm tra trạng thái `SLOT_DROP_01` chuyển sang `RESERVED`, hệ thống loại bỏ `AMR_03` (pin $22\% < 25\%$) và gán đơn cho `AMR_01`.
2. **Kịch bản chặn xung đột nút giao (`N_10`):** Phát đồng thời 02 đơn hàng vuông góc đi qua `N_10`. Quan sát bản tin MQTT: `AMR_01` nhận `released: true` tại `N_10`, trong khi `AMR_02` nhận `released: false` tại `N_10`, tự động dừng chờ tại `N_09` và chuyển trạng thái sang `BLOCKED` cho đến khi `AMR_01` đi qua `N_10`.
3. **Kịch bản phát lệnh khẩn cấp (`instantActions`):** Kích hoạt lệnh `startPause` trên giao diện điều hành, kiểm tra `mqtt_ros_bridge` hủy Goal trên Nav2 và khóa vận tốc `/cmd_vel` về `0.0 m/s` trong thời gian $\le 100\text{ ms}$.