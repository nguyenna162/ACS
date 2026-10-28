# SRS ACS - Tổng hợp đặc tả từ tài liệu dự án (Docs_img) và hiện trạng triển khai

Tài liệu này hợp nhất 11 ảnh đặc tả trong `Docs_img/` (SRS High-Level, danh sách
model, thuật toán, mã nguồn worker mẫu) với kiến trúc thực tế của repository.
Mỗi mục ghi rõ **đặc tả gốc** và **cách đã hiện thực** để đội vận hành/kiểm thử
đối chiếu.

> Ngày lập tài liệu gốc trong ảnh: 16/10/2026. Khu vực vận hành: CNC Prod 2G,
> chuẩn giao thức VDA 5050 qua MQTT, tích hợp Aligo MES.

---

## 1. Tổng quan hệ thống

ACS là backend trung tâm quản lý và điều khiển đội AMR/AGV:

- Nhận yêu cầu vận chuyển, tìm lộ trình tối ưu, điều phối giao thông (tránh va
  chạm, chống tắc nghẽn), giám sát trạng thái thời gian thực, ghi log toàn bộ.
- Tích hợp Aligo MES để nhận yêu cầu cấp vật tư và đồng bộ trạng thái robot/line.
- Đối tượng dùng: vận hành (HMI), quản trị (Django Admin/API), hệ thống ngoài
  (Aligo/ERP qua REST).

**Ca sử dụng chính**

1. Vận hành tạo đơn vận chuyển giữa 2 điểm/2 line hoặc theo danh sách node.
2. MES gửi lệnh cấp vật tư → hệ thống ghép cặp AMR gần nhất còn pin phù hợp.
3. Robot báo trạng thái VDA 5050 (1 Hz) → cập nhật HMI, tiến độ đơn, khóa giao thông.
4. Sự cố: mất heartbeat, kẹt khóa, deadlock, quá hạn xác nhận đơn → cảnh báo + tự xử lý.

---

## 2. Kiến trúc công nghệ

| Hạng mục | Đặc tả trong ảnh | Hiện thực trong repo |
|---|---|---|
| Backend | Python, Django 6.x, DRF, drf-spectacular | **Django 5.0.4 + DRF 3.15** (môi trường offline không cài được spectacular; dùng Browsable API ở dev) |
| Realtime | Django Channels/ASGI, Daphne, uvicorn, WebSocket | **Channels 4.1 + Daphne**, `InMemoryChannelLayer`, không Redis |
| Database | PostgreSQL (psycopg2), ~30 bảng chia nhóm `acs_`, `amr_`, `buffer_`, `map_` | **PostgreSQL + psycopg 3**, bảng `acs_*`, `amr_topic_control`, `robot_*`, `map_*`, `buffer_*`, `core_*` (SQLite chỉ dev/test) |
| MQTT | paho-mqtt, broker EMQX | **paho 2.0**, broker vendor-neutral: EMQX (prod), Mosquitto, hoặc `amqtt` nội bộ (`manage.py run_broker`) |
| Thuật toán | SciPy `linear_sum_assignment`, Dijkstra, heapq | **Hungarian thuần Python** (`traffic/hungarian.py`), tự dùng SciPy nếu có; Dijkstra + cache TTL 60s |
| Tích hợp | Aligo External API (REST) | `integrations/aligo.py` (urllib, offline-safe, mặc định tắt) |
| Frontend | Qt6/C++, web Bootstrap 5 + Leaflet + WASM | HMI web tự dựng **offline không CDN**: canvas bản đồ (pan/zoom, AMR real-time), dark/light mode, panel đơn/cấp vật tư/cảnh báo (`templates/dashboard.html` + `static/`) |
| Vận hành | `acs_start.sh` / `runacs.bat`, worker nền, log theo phân hệ, pytest | **`acs_start.sh` / `runacs.bat` + worker threads + log xoay vòng + Django test** (35 test) |

**Khác biệt có chủ đích** (do ràng buộc offline, không Docker/Redis):

- Bỏ Redis: realtime dùng **Event Outbox DB** (`core_realtime_event`), gửi MQTT dùng
  **Transactional Outbox** (`core_mqtt_outbox`).
- Không dò cổng/không fallback: chọn profile bằng `ACS_ENV=dev|prod|test`, DB bằng
  `ACS_DB_ENGINE=postgresql|sqlite`.
- Ảnh layer bản đồ dùng `FileField` (môi trường không cài Pillow) thay `ImageField`.

---

## 3. Cấu trúc module

| Module trong ảnh | Vai trò | Vị trí trong repo |
|---|---|---|
| `acs/models` (Order, OrderMaster, SubOrder, SupplyRequest, Log) | Nghiệp vụ đơn hàng | `acs/models.py` (+ `SystemLog` = `Log`, `ActionDispatch`) |
| `acs/vda5050` (order, action) | Định nghĩa bản tin VDA 5050 | `acs/vda5050/order.py`, `acs/vda5050/action.py` + `workers/mqtt.py` (topic) |
| `amr/` (AmrVehicle, TopicControl, AmrStateLog) | Quản lý đội xe & trạng thái | `amr/` (`RobotProfile`, `TopicControl`, `RobotTelemetry`/`AmrStateLog`) |
| `buffer/` (Buffer, Schedule, ScheduleStop, StopAction, ActionOption) | Bộ đệm & lịch trình | `buffer/` (`BufferSlot` mở rộng + các bảng lịch trình) |
| `map/` (MapBase, WorldMapLayer, GridMapLayer, ZoneSet, TrafficZone, Zone, Node, Edge, Action*) | Bản đồ & vùng giao thông | `map/models.py` (giữ tương thích `MapTopology`/`Node`/`Edge` cũ) |
| `traffic/` (FleetTask, FleetAssignment, FleetScheduleResult, RobotPriority, ReservationStatus) | Lập lịch & đặt chỗ | `traffic/` (không phải Django app, chỉ thuật toán) |
| `integrations/` | Aligo client | `integrations/aligo.py` |
| `workers/` (`acs_order_master`, `acs_supply_request_pairing`, `acs_order_send`) | Tiến trình nền + hạ tầng gộp | `workers/` (gồm `acs_system.py` = EventBus/Outbox/health/mqtt/locks/logging/middleware/checks + `SystemWorker`) + `workers/schema_class.py` + `manage.py run_acs_workers` |
| `templates/`, `static/`, `media/maps`, `wasm/` | HMI | `templates/`, `static/`, `media/maps` (WASM nằm ở client) |

---

## 4. Chức năng lớn

1. **Quản lý đơn vận chuyển** - đơn lẻ (Order) và đơn tổng (OrderMaster) gồm nhiều
   chặng (SubOrder); ưu tiên, SLA, huỷ, gửi lại, timeout.
2. **Quản lý đội AMR** - hồ sơ `AmrVehicle`, topic MQTT theo từng xe, log trạng thái,
   heartbeat 5s/10s, tự đăng ký (chỉ dev).
3. **Điều phối giao thông & lập lịch** - Hungarian (deadhead distance + pin), phân
   tầng ưu tiên, đặt chỗ node/edge có TTL, phát hiện & gỡ deadlock, cửa sổ trượt k=3.
4. **Quản lý bản đồ** - MapBase + layer ảnh nền/grid, ZoneSet/TrafficZone/Zone,
   Node/Edge đầy đủ thuộc tính VDA 5050, Action trên node/edge.
5. **Quản lý buffer & lịch trình** - buffer điểm nhận (RSSI, product model, process,
   role), Schedule/ScheduleStop/StopAction/ActionOption.
6. **Tích hợp Aligo MES** - polling lệnh cấp vật tư, đẩy trạng thái robot/line,
   ACK lệnh; tắt hoàn toàn khi `ALIGO_ENABLED=false`.
7. **Giám sát & log** - REST tra cứu, SystemLog theo phân hệ, log JSON xoay vòng,
   `/api/health/live|ready|metrics`, WebSocket snapshot 1s + event outbox.

---

## 5. Mô hình dữ liệu

### 5.1. `acs` - đơn hàng

- **Order** (VDA order thực thi): `order_id` PK, `order_type`, `order_master` FK,
  `robot` FK, `src_slot`/`dst_slot`, `order_update_id`, `order_status`
  (CREATED/ASSIGNED/EXECUTING/COMPLETED/CANCELLED/FAILED), `priority`,
  `queued_at`, `started_at`, `failure_reason`, `created_at`, `completed_at`.
- **OrderNode**: node theo `sequence_id` chẵn; `released`, `node_status`.
- **OrderAction**: action trên node; `action_id`, `action_type`, `blocking_type`, `params`.
- **SupplyRequest**: `request_id` unique, `source` (ALIGO/MANUAL/ERP), `line_id`,
  `process`, `product_model`, `quantity`, `priority`, `sla_deadline`, `status`
  (PENDING/PAIRED/IN_PROGRESS/COMPLETED/FAILED/CANCELLED), `src_slot`, `dst_slot`,
  `amr`, `order_master`, `payload`, `outcome`, timestamps.
- **OrderMaster**: `master_id` (MES-YYYYMMDD-NNNN, sinh bằng `MasterIdSequence`),
  `supply_request`, `status` (NEW/PLANNING/PROCESSING/COMPLETED/FAILED/CANCELLED),
  `priority`, `plan` JSON, `outcome`, timestamps.
- **SubOrder**: `sub_order_id` UUID, `order_master`, `order` (OneToOne), `amr`,
  `status` (PENDING/SENT/ACKED/EXECUTING/COMPLETED/FAILED/CANCELLED), `path` JSON
  (nodes/edges/zoneSetId), `anchor_seq`, `order_update_id`, `resend_count`,
  `outcome`, `sent_at`, `last_sent_at`, `acked_at`, `completed_at`.
- **ActionDispatch**: `action_id` PK, `sub_order`, `order`, `amr`, `action_type`,
  `blocking_type`, `status`, `parameters`, timestamps.
- **SystemLog** (ảnh gọi `Log`): `category`, `level`, `message`, `actor`, `robot`,
  `order`, `sub_order`, `payload`, `created_at`.

### 5.2. `amr` - đội xe

- **AmrVehicle** (`RobotProfile`): `serial_number` PK, `amr_model`, `interface`,
  `manufacturer`, `version`, `ip_address`, `port`, `last_node_id`, `battery_level`,
  `operational_state`, pose (`pos_x`, `pos_y`, `theta`), trạng thái VDA 5050
  (`order_id`, `order_update_id`, `node_states`, `edge_states`, `action_state`,
  `loads`, `errors`, `information`, `amr_position`, `velocity`, `battery_state`,
  `safety_state`, `driving`, `paused`, `new_base_request`, `operating_mode`,
  `zone_set_id`), vận hành (`is_online`, `is_idle`, `is_used_by_master`,
  `last_heartbeat`).
- **TopicControl**: `topic` unique, `topic_type`, `amr`, `manufacturer`, `version`,
  `header_id`, `last_call`.
- **AmrStateLog** (`RobotTelemetry`): snapshot trạng thái theo mẫu thời gian
  (`ACS_TELEMETRY_SAMPLE_SECONDS`, mặc định 5s) phục vụ audit/thống kê.

### 5.3. `map` - bản đồ

- **MapBase** (UUID): `topology` (OneToOne MapTopology), `name`, `part`, `version`,
  `status`, `description`, `created_by`, timestamps.
- **WorldMapLayer / GridMapLayer**: ảnh nền/grid, origin, resolution, resize, opacity.
- **ZoneSet**: `map`, `name`, `description`, `version`, `is_active`.
- **TrafficZone**: `zone_set`, `map`, `name`, `zone_type`, `max_occupancy`,
  `priority`, `max_speed`.
- **Zone** (legacy): `zone_id`, `zone_name`, toạ độ/kích thước, `aligo_metadata`.
- **Node**: `node_id` PK, `map_topology`, `map`, `name`, `node_type`, toạ độ,
  `allow_deviation_xy/theta`, `use_node`, `is_init`, `is_extended`,
  `traffic_zone`, `zone`, trạng thái khóa (`is_occupied`, `locked_by`, `locked_at`).
- **Edge**: `edge_id` PK, `map`, `start_node`, `end_node`, `distance`, `length`,
  `max_speed`, `orientation`, `orientation_type`, `max_rotation_speed`,
  `rotation_allowed`, `trajectory`, `traffic_zone`, `zone`, `direction`, khóa.
- **Action / ActionOnNode / ActionOnEdge**: hành động VDA 5050 gắn node/edge.

### 5.4. `buffer` - bộ đệm & lịch trình

- **BufferSlot** (ảnh gọi `Buffer`): `slot_id` PK, `zone`, `node` (1-1), `name`,
  `rssid`, `buffer_type`, `product_model`, `process`, `role_id`, `slot_status`,
  `enable`, `ip_address`, `pallet_code`, `handle_order`, `last_success_transfer`,
  `time_gap_btw_transfer`, `data`.
- **Schedule**: `name`, `description`, `map`, `number_of_amr_need`,
  `number_of_active_order`, `enable`.
- **ScheduleStop**: `schedule`, `buffer`, `role_id`, `sequence`.
- **ActionOption** / **StopAction**: lựa chọn hành động khi dừng và ánh xạ Action.

### 5.5. `core` - hạ tầng

- **RealtimeEvent**: `topic`, `payload`, `created_at` (event outbox cho WebSocket).
- **MqttOutboundMessage**: `topic`, `payload`, `qos`, `retain`, `state`,
  `attempts`, `next_attempt_at`, `sent_at`, `last_error` (MQTT outbox).
- **SystemHeartbeat**: `component`, `instance_id`, `last_seen`, `meta`
  (phát hiện worker treo cho readiness).

---

## 6. Thuật toán

| Thuật toán | Đặc tả | Hiện thực |
|---|---|---|
| Hungarian | `scipy.optimize.linear_sum_assignment`, tối thiểu tổng deadhead distance, fallback greedy | `traffic/hungarian.py` (JV O(n²m) thuần Python, API tương đương SciPy); `FleetScheduler` tự dùng SciPy nếu môi trường có |
| Dijkstra | heap + weighted adjacency, cạnh Euclid, cache graph TTL 60s | `traffic/graph_distance.py` (cache `ACS_TOPOLOGY_CACHE_SECONDS`) |
| Cost function | `W1·(distance/D_MAX_MAP) + W2·(1−battery/100)` | `FleetScheduler._cost` (`ACS_DISPATCH_W1_DISTANCE`, `ACS_DISPATCH_W2_BATTERY`, `ACS_D_MAX_MAP`) |
| State/resource reservation | `state_traffic_controller.py`, đặt chỗ node/edge theo thời gian | `traffic/state_reservation_manager.py` (UPDATE có điều kiện + TTL `ACS_RESERVATION_TTL_SECONDS`) |
| Deadlock | phát hiện chu trình wait-for, gỡ theo ưu tiên | `traffic/deadlock_controller.py` (+ `map.traffic_lock.TrafficController` cho sliding window) |
| Priority-based scheduling | xếp hạng theo mức ưu tiên robot + loại nhiệm vụ | `FleetScheduler.schedule` phân tầng `RobotPriority` (LOW 10/NORMAL 50/HIGH 80/URGENT 100) |
| Priority tiers + tiebreaker | ghép SupplyRequest theo ưu tiên, tie-break bằng graph distance + SLA | `SupplyService.pair_pending` (sort `-priority, sla_deadline, created_at`; chi phí Hungarian dùng Dijkstra) |
| FIFO queue processing | xử lý đơn PENDING theo `created_at`, giới hạn pool | `OrderMasterService.plan_pending_masters` (`ACS_ORDER_MASTER_POOL`) |

---

## 7. Pipeline worker (đối chiếu mã nguồn trong ảnh)

```
SupplyRequest(PENDING)
  └─ SupplyPairingWorker ── FleetScheduler ──> OrderMaster(PLANNING) + SubOrder(PENDING)
        └─ OrderMasterWorker ── OrderDispatcher ──> Order(VDA) + OrderNode/OrderAction
              └─ SubOrderDispatcher ── VDA5050Builder ──> MqttOutbox(PENDING)
                    └─ OutboxPublisherWorker ──> broker MQTT ──> AMR
                          └─ OrderSendMqtt: ACK (orderId/orderUpdateId) | resend 3s | fail 300s
                                └─ SubOrderSentTimeoutChecker: quét SENT quá hạn
```

Điểm khớp với ảnh:

- `SubOrderDispatcher(WorkerThread)` quét OrderMaster `PLANNING/PROCESSING`, chọn
  SubOrder `PENDING`; kiểm tra AMR sẵn sàng (`amr_ready_for_update`: đúng orderId,
  không OFFLINE/ERROR, chấp nhận `newBaseRequest`).
- `ActionDispatch.objects.get_or_create(action_id=...)` cho mọi action trong bản tin
  node (giống `_register_dispatched_actions`).
- Anchor: `resolve_anchor_seq` dùng `_sent_anchor_history` / `_last_sent_anchor_seq`
  (RAM, giới hạn `ACS_MAX_CACHE_SIZE`) + `SubOrder.anchor_seq` trong DB.
- `OrderSendMqtt`: trạng thái ACKED/DROP/PENDING như ảnh; resend mỗi
  `ACS_RESEND_INTERVAL_SECONDS`; quá `ACS_SENT_TIMEOUT_SECONDS` → `fail_order` lan
  truyền SubOrder/OrderMaster/SupplyRequest và giải phóng khóa.
- `headerId` cấp phát tăng dần qua `TopicControl.next_header_id()` (không hard-code 1).
- Mọi phát MQTT đi qua outbox; worker không gọi mạng trong transaction CSDL.

Lệnh chạy:

```bash
python manage.py run_acs_workers     # pipeline đơn hàng (OrderMaster/SubOrder/ACK)
python manage.py run_mqtt_worker     # ingest state/connection + outbox publisher + traffic monitor
```

---

## 8. REST API chính

| Nhóm | Endpoint |
|---|---|
| Sức khỏe | `GET /api/health/live/`, `GET /api/health/ready/`, `GET /api/health/metrics/`, `POST /api/health/heartbeat/` |
| Robot | `/api/robots/` (+ `fleet_summary`, `snapshot`, `topics`, `telemetry`, `trigger_heartbeat`), `/api/robot-topics/` |
| Bản đồ | `/api/topology/` (+ `full_map`, `deadlocks`, `path`), `/api/maps/`, `/api/map-world-layers/`, `/api/map-grid-layers/`, `/api/zone-sets/`, `/api/traffic-zones/`, `/api/zones/`, `/api/nodes/`, `/api/edges/`, `/api/actions/`, `/api/action-on-nodes/`, `/api/action-on-edges/` |
| Buffer | `/api/buffer-zones/` (+ `summary`), `/api/buffer-slots/` (+ `occupy`, `release`), `/api/schedules/`, `/api/schedule-stops/`, `/api/stop-actions/`, `/api/action-options/` |
| Đơn hàng | `/api/orders/` (+ `cancel`, `resend`), `/api/supply-requests/` (+ `pair`, `cancel`), `/api/order-masters/` (+ `plan`, `cancel`), `/api/sub-orders/` (+ `resend`), `/api/action-dispatches/`, `/api/system-logs/` |
| Tức thời | `POST /api/instant-actions/` (startPause/stopPause/cancelOrder) |
| Lập lịch | `POST /api/fleet/schedule/` (preview Hungarian, `strategy=tiered|global`) |

Xác thực: Token/Session (production bắt buộc `ACS_REQUIRE_AUTH=true`). Lệnh điều
khiển bị giới hạn bởi throttle scope `control`.

---

## 9. Vận hành & an toàn

- **Profile cấu hình**: `ACS_ENV=prod` bắt buộc `DJANGO_SECRET_KEY`,
  `DJANGO_ALLOWED_HOSTS`, `POSTGRES_PASSWORD`; system check `check --deploy` chặn
  DEBUG/SQLite/thiếu auth.
- **Không dò cổng**: sai cấu hình sẽ fail-fast thay vì âm thầm chạy sai.
- **Heartbeat**: 5s → WARNING, 10s → OFFLINE; worker treo > 20s làm readiness degraded
  (`ACS_WORKER_STALE_SECONDS`).
- **Khóa giao thông** có TTL 45s, tự giải phóng; deadlock được gỡ tự động và phát alert.
- **Lưu trữ**: telemetry 7 ngày, event 1 giờ, outbox 7 ngày, SystemLog 30 ngày
  (`manage.py prune_data`, chạy tự động mỗi 60s bởi SystemWorker).
- **Backup**: PostgreSQL + `media/maps` + file cấu hình/cert MQTT.

---

## 10. Giới hạn đã biết

- Mỗi `SupplyRequest` hiện tương ứng 1 chặng (1 SubOrder). Yêu cầu `quantity > 1`
  cần tách thành nhiều request hoặc mở rộng `_create_master_for`.
- Ma trận Hungarian dùng khoảng cách robot→điểm lấy hàng (deadhead), chưa cộng
  thời gian phục vụ tại buffer.
- `MapBase` là lớp mở rộng song song `MapTopology`; Node/Edge cũ vẫn dùng
  `map_topology` để tương thích dữ liệu hiện hữu.
- Ảnh dùng `ImageField`; repo dùng `FileField` do môi trường offline không có Pillow.
- Chưa tích hợp drf-spectacular/WASM (client ngoài phạm vi backend).

---

## 11. Kiểm thử

```bash
python manage.py test            # 35 test: VDA 5050, pipeline MES, traffic, outbox, health
python manage.py check           # system check
python manage.py check_deployment --skip-mqtt
python manage.py smoke_pipeline  # smoke SupplyRequest -> SubOrder -> outbox (tự rollback)
```

Bộ test bao phủ: Hungarian (vuông/chữ nhật/không khả thi), Dijkstra + cache, phân
tầng ưu tiên, reservation TTL/rollback, Event/MQTT outbox, health API, heartbeat,
ingest state (kể cả bản tin cũ), toàn bộ vòng đời SupplyRequest → hoàn thành, resend
và timeout.



