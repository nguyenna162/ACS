# Triển khai ACS offline (không Docker, không Redis)

Hướng dẫn này dành cho máy chủ nhà máy (Linux) và máy trạm Windows, chạy trực
tiếp bằng Python + PostgreSQL + broker MQTT nội bộ.

## 1. Thành phần cần cài

| Thành phần | Phiên bản | Ghi chú |
|---|---|---|
| Python | 3.11 - 3.13 | Repo đang chạy 3.13 |
| PostgreSQL | 14+ | Bắt buộc cho production (50 AMR ghi đồng thời) |
| MQTT broker | EMQX 5.x **hoặc** Mosquitto 2.x | Mạng biệt lập; bật user/ACL, TLS nếu có |
| Python packages | `requirements.txt` | `pip install -r requirements.txt` |

Không cần: Docker, Redis, internet. Nếu chưa có broker, dev có thể dùng broker
Python nội bộ: `python manage.py run_broker --port 1883`.

## 2. Cài đặt trên Linux (máy chủ ACS)

```bash
sudo useradd -r -s /bin/bash -d /opt/acs acs
sudo mkdir -p /opt/acs /var/log/acs /var/lib/acs/media
sudo chown -R acs:acs /opt/acs /var/log/acs /var/lib/acs
sudo -u acs -H bash -lc '
  cd /opt/acs
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --no-index --find-links /opt/acs/wheels -r requirements.txt   # offline wheelhouse
'
```

Sao chép mã nguồn vào `/opt/acs`, sau đó:

```bash
cp .env.example .env && chmod 600 .env
# Sửa: ACS_ENV=prod, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, POSTGRES_*,
#      MQTT_BROKER/MQTT_CA_CERTS, ACS_LOG_DIR=/var/log/acs
set -a; . ./.env; set +a
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py check_deployment          # kiểm tra cấu hình/broker
```

Khởi chạy tất cả tiến trình:

```bash
./acs_start.sh
# hoặc tách riêng:
.venv/bin/python -m daphne -b 0.0.0.0 -p 8000 acs.asgi:application
.venv/bin/python manage.py run_acs_workers
.venv/bin/python manage.py run_mqtt_worker
```

## 3. systemd (khuyến nghị cho production)

`/etc/systemd/system/acs-web.service`

```ini
[Unit]
Description=ACS Web (Daphne ASGI)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=acs
WorkingDirectory=/opt/acs
EnvironmentFile=/opt/acs/.env
ExecStart=/opt/acs/.venv/bin/python -m daphne -b 0.0.0.0 -p 8000 acs.asgi:application
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/acs-workers.service`

```ini
[Unit]
Description=ACS Order Workers
After=network-online.target postgresql.service

[Service]
Type=simple
User=acs
WorkingDirectory=/opt/acs
EnvironmentFile=/opt/acs/.env
ExecStart=/opt/acs/.venv/bin/python manage.py run_acs_workers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/acs-mqtt.service`

```ini
[Unit]
Description=ACS MQTT Ingest + Outbox + Traffic Monitor
After=network-online.target postgresql.service

[Service]
Type=simple
User=acs
WorkingDirectory=/opt/acs
EnvironmentFile=/opt/acs/.env
ExecStart=/opt/acs/.venv/bin/python manage.py run_mqtt_worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now acs-web acs-workers acs-mqtt
```

## 4. Windows (máy trạm điều khiển/dự phòng)

1. Cài Python 3.11+ (Add to PATH) và PostgreSQL; tạo database `acs_db`.
2. `python -m venv .venv` → `.venv\Scripts\pip install -r requirements.txt`.
3. Copy `.env.example` → `.env`, sửa biến; nạp biến bằng PowerShell:
   `Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') } }`
4. `runacs.bat` (migrate + mở 2 cửa sổ worker + daphne cổng 8000).
5. Muốn chạy như service: dùng NSSM/Task Scheduler trỏ tới `runacs.bat`.

## 5. Kiểm tra sau triển khai

```bash
curl http://127.0.0.1:8000/api/health/live/     # {"status":"ok"}
curl http://127.0.0.1:8000/api/health/ready/    # workers + outbox + DB
curl http://127.0.0.1:8000/api/health/metrics/  # số AMR, đơn, tồn outbox
```

- `degraded` + `stale_workers` → tiến trình worker đã chết/treo: kiểm tra
  `journalctl -u acs-workers -u acs-mqtt`.
- `outbox_pending` tăng liên tục → broker MQTT mất kết nối/TLS sai.
- WebSocket HMI: `ws://<host>:8000/ws/robot/telemetry/` (snapshot 1s + sự kiện).

## 6. Vận hành định kỳ

| Tác vụ | Cách làm |
|---|---|
| Dọn dữ liệu | Tự động mỗi 60s (SystemWorker) hoặc `manage.py prune_data` |
| Kiểm tra triển khai | `manage.py check_deployment` (thêm `--strict-worker` cho CI) |
| Kiểm tra cấu hình prod | `ACS_ENV=prod manage.py check --deploy` |
| Backup | `pg_dump -Fc acs_db > acs_$(date +%F).dump` + `tar czf media_$(date +%F).tgz media/maps` |
| Phục hồi | `pg_restore -d acs_db --clean --if-exists acs_YYYY-MM-DD.dump` rồi restart services |
| Nâng cấp | Dừng `acs-workers`/`acs-mqtt` → `migrate` → `check_deployment` → start lại |

## 7. Xử lý sự cố thường gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `ImproperlyConfigured: Thiếu DJANGO_SECRET_KEY` | `.env` chưa nạp | `EnvironmentFile`/`source .env` trước khi chạy |
| Readiness 503 `database.ok=false` | PostgreSQL chưa chạy/sai mật khẩu | Kiểm tra `POSTGRES_*`, `psql -h ... -U ...` |
| `outbox_failed` tăng | Topic sai hoặc broker từ chối ACL | Kiểm tra `VDA5050_TOPIC_PREFIX`, quyền user MQTT |
| AMR `OFFLINE` liên tục | Không nhận `state`/`connection` | Broker, `MQTT_BROKER`, prefix topic, chứng chỉ TLS |
| Đơn kẹt `SENT` | Robot không ACK | Kiểm tra `orderId/orderUpdateId`, log `acs-worker.log`; API `sub-orders/{id}/resend/` |
| Node bị khóa mãi | Worker chết giữa chừng | Khóa tự hết hạn sau `ACS_RESERVATION_TTL_SECONDS`; hoặc restart `acs-mqtt` |
| Deadlock lặp lại | Bản đồ thiếu node tránh/zone quá hẹp | Xem `/api/topology/deadlocks/`, bổ sung edge/zone, giảm `ACS_HORIZON_WINDOW_K` |

## 8. Kiểm thử trước bàn giao

```bash
.venv/bin/python manage.py test                 # 35 test
.venv/bin/python manage.py seed_demo_data       # dữ liệu mẫu (map, buffer, 3 AMR, topic)
.venv/bin/python manage.py run_broker &         # nếu chưa có broker
.venv/bin/python manage.py simulate_robots &    # AMR ảo gửi state
.venv/bin/python manage.py run_mqtt_worker &
.venv/bin/python manage.py run_acs_workers &
```

Kiểm tra nhanh nghiệp vụ:

```bash
# Tạo yêu cầu cấp vật tư (tự ghép cặp AMR gần nhất)
curl -X POST http://127.0.0.1:8000/api/supply-requests/ \
  -H "Content-Type: application/json" \
  -d '{"src_slot":"SLOT_PICK_01","dst_slot":"SLOT_DROP_01","priority":80}'

# Theo dõi OrderMaster/SubOrder tương ứng
curl http://127.0.0.1:8000/api/order-masters/
curl http://127.0.0.1:8000/api/sub-orders/

# Xem trước phương án ghép cặp (không đổi dữ liệu)
curl -X POST http://127.0.0.1:8000/api/fleet/schedule/ -H "Content-Type: application/json" \
  -d '{"tasks":[{"task_id":"T1","start_node":"N_01","end_node":"N_15"}],
       "robots":[{"serial_number":"AMR_01","current_node":"N_01","battery_level":90}]}'
```

