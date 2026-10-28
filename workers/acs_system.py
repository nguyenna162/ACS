"""Hạ tầng vận hành ACS gộp thành 1 module (thư viện + worker chạy nền).

Gồm:
* Event Outbox (``EventBus``) - realtime không cần Redis.
* MQTT Outbox (``MqttOutbox``) - gửi bản tin bền vững, có retry/backoff.
* MQTT client/topic (``create_client``, ``*_topic``, ``next_header_id``).
* Khóa phân tán (``acquire_advisory_xact_lock``).
* Health/readiness/metrics (``readiness``, ``metrics``, ``worker_status``).
* ``JsonFormatter``, ``RequestContextMiddleware``, system checks.
* ``SystemWorker`` - worker chạy nền: heartbeat AMR, giải phóng khóa hết hạn,
  gỡ deadlock, dọn dữ liệu định kỳ và theo dõi outbox.
"""

from __future__ import annotations

import datetime
import json
import logging
import ssl
import time
import uuid

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.utils import timezone

from workers.schema_class import WorkerThread

logger_events = logging.getLogger("acs.events")
logger_outbox = logging.getLogger("acs.outbox")
logger_locks = logging.getLogger("acs.locks")
logger_request = logging.getLogger("acs.request")
logger_system = logging.getLogger("workers.system")

TOPIC_ORDER = "order"
TOPIC_ALERT = "alert"


# Lazy truy cập model: acs_system.py được import rất sớm (cấu hình logging) trước
# khi app registry sẵn sàng, nên không import model ở top-level.
def _realtime_event_model():
    from acs.models import RealtimeEvent

    return RealtimeEvent


def _outbox_message_model():
    from acs.models import MqttOutboundMessage

    return MqttOutboundMessage


def _heartbeat_model():
    from acs.models import SystemHeartbeat

    return SystemHeartbeat


# ---------------------------------------------------------------------------
# Event Outbox
# ---------------------------------------------------------------------------
class EventBus:
    """API ghi/đọc sự kiện. Mọi lỗi realtime đều không được làm hỏng nghiệp vụ."""

    @staticmethod
    def publish(topic: str, payload: dict | None = None) -> RealtimeEvent | None:
        try:
            return _realtime_event_model().objects.create(topic=topic, payload=payload or {})
        except DatabaseError:
            logger_events.exception("Không ghi được realtime event topic=%s", topic)
            return None

    @staticmethod
    def publish_many(events: list[tuple[str, dict]]) -> int:
        if not events:
            return 0
        try:
            with transaction.atomic():
                _realtime_event_model().objects.bulk_create(
                    [_realtime_event_model()(topic=topic, payload=payload or {}) for topic, payload in events],
                    batch_size=200,
                )
            return len(events)
        except DatabaseError:
            logger_events.exception("Không ghi được lô realtime event (%s sự kiện)", len(events))
            return 0

    @staticmethod
    def latest_id() -> int:
        try:
            return (
                _realtime_event_model().objects.order_by("-id").values_list("id", flat=True).first() or 0
            )
        except DatabaseError:
            return 0

    @staticmethod
    def fetch_after(last_id: int, limit: int = 100) -> list[RealtimeEvent]:
        try:
            return list(
                _realtime_event_model().objects.filter(id__gt=last_id).order_by("id")[: max(1, limit)]
            )
        except DatabaseError:
            logger_events.exception("Không đọc được realtime event sau id=%s", last_id)
            return []

    @staticmethod
    def prune(retention_seconds: int) -> int:
        if retention_seconds <= 0:
            return 0
        cutoff = timezone.now() - timezone.timedelta(seconds=retention_seconds)
        try:
            deleted, _ = _realtime_event_model().objects.filter(created_at__lt=cutoff).delete()
            return deleted
        except DatabaseError:
            logger_events.exception("Không dọn được realtime event")
            return 0


# ---------------------------------------------------------------------------
# MQTT Outbox
# ---------------------------------------------------------------------------
DEFAULT_QOS = 1
LEASE_SECONDS = 15


class MqttOutbox:
    @staticmethod
    def enqueue(topic: str, payload: dict, qos: int = DEFAULT_QOS, retain: bool = False):
        try:
            return _outbox_message_model().objects.create(
                topic=topic,
                payload=payload or {},
                qos=qos,
                retain=retain,
                next_attempt_at=timezone.now(),
            )
        except DatabaseError:
            logger_outbox.exception("Không ghi được MQTT outbox topic=%s", topic)
            return None

    @staticmethod
    def enqueue_many(items: list[dict]) -> int:
        """items: [{'topic':..., 'payload':..., 'qos':1, 'retain':False}, ...]."""
        if not items:
            return 0
        now = timezone.now()
        objects = [
            _outbox_message_model()(
                topic=item["topic"],
                payload=item.get("payload") or {},
                qos=int(item.get("qos", DEFAULT_QOS)),
                retain=bool(item.get("retain", False)),
                next_attempt_at=now,
            )
            for item in items
        ]
        try:
            with transaction.atomic():
                _outbox_message_model().objects.bulk_create(objects, batch_size=200)
            return len(objects)
        except DatabaseError:
            logger_outbox.exception("Không ghi được lô MQTT outbox (%s bản tin)", len(items))
            return 0

    @staticmethod
    def claim(limit: int = 50, lease_seconds: int = LEASE_SECONDS):
        """Nhận lô bản tin cần gửi, đẩy next_attempt_at để tránh trùng lease."""
        now = timezone.now()
        try:
            ids = list(
                _outbox_message_model().objects.filter(
                    state=_outbox_message_model().STATE_PENDING, next_attempt_at__lte=now
                )
                .order_by("id")
                .values_list("id", flat=True)[: max(1, limit)]
            )
            if not ids:
                return []
            _outbox_message_model().objects.filter(id__in=ids).update(
                attempts=F("attempts") + 1,
                next_attempt_at=now + timezone.timedelta(seconds=lease_seconds),
            )
            return list(_outbox_message_model().objects.filter(id__in=ids).order_by("id"))
        except DatabaseError:
            logger_outbox.exception("Không claim được MQTT outbox")
            return []

    @staticmethod
    def mark_sent(ids: list[int]) -> int:
        if not ids:
            return 0
        return _outbox_message_model().objects.filter(id__in=ids).update(
            state=_outbox_message_model().STATE_SENT,
            sent_at=timezone.now(),
            last_error="",
        )

    @staticmethod
    def mark_failure(message_ids: list[int], error: str, max_attempts: int = 20) -> int:
        if not message_ids:
            return 0
        now = timezone.now()
        updated = 0
        for message in _outbox_message_model().objects.filter(id__in=message_ids):
            message.last_error = (error or "")[:2000]
            if message.attempts >= max_attempts:
                message.state = _outbox_message_model().STATE_FAILED
                message.next_attempt_at = now
            else:
                backoff = min(60, 2 ** max(1, message.attempts))
                message.next_attempt_at = now + timezone.timedelta(seconds=backoff)
            message.save(update_fields=["state", "next_attempt_at", "last_error"])
            updated += 1
        return updated

    @staticmethod
    def pending_count() -> int:
        try:
            return _outbox_message_model().objects.filter(
                state=_outbox_message_model().STATE_PENDING
            ).count()
        except DatabaseError:
            return 0

    @staticmethod
    def failed_count() -> int:
        try:
            return _outbox_message_model().objects.filter(
                state=_outbox_message_model().STATE_FAILED
            ).count()
        except DatabaseError:
            return 0

    @staticmethod
    def prune(days: int) -> int:
        if days <= 0:
            return 0
        cutoff = timezone.now() - timezone.timedelta(days=days)
        try:
            deleted, _ = _outbox_message_model().objects.filter(
                created_at__lt=cutoff,
                state__in=[
                    _outbox_message_model().STATE_SENT,
                    _outbox_message_model().STATE_FAILED,
                ],
            ).delete()
            return deleted
        except DatabaseError:
            logger_outbox.exception("Không dọn được MQTT outbox")
            return 0


# ---------------------------------------------------------------------------
# MQTT client + topic VDA 5050
# ---------------------------------------------------------------------------
def _prefix() -> str:
    return str(settings.VDA5050_TOPIC_PREFIX).strip("/")


def state_topic(manufacturer: str, serial_number: str) -> str:
    return f"{_prefix()}/{manufacturer}/{serial_number}/state"


def connection_topic(manufacturer: str, serial_number: str) -> str:
    return f"{_prefix()}/{manufacturer}/{serial_number}/connection"


def order_topic(manufacturer: str, serial_number: str) -> str:
    return f"{_prefix()}/{manufacturer}/{serial_number}/order"


def instant_actions_topic(manufacturer: str, serial_number: str) -> str:
    return f"{_prefix()}/{manufacturer}/{serial_number}/instantActions"


def factsheet_topic(manufacturer: str, serial_number: str) -> str:
    return f"{_prefix()}/{manufacturer}/{serial_number}/factsheet"


def state_wildcard_topic() -> str:
    return f"{_prefix()}/+/+/state"


def connection_wildcard_topic() -> str:
    return f"{_prefix()}/+/+/connection"


def controller_status_topic() -> str:
    instance = str(settings.ACS_INSTANCE_NAME).replace(" ", "_")
    return f"{_prefix()}/acs/{instance}/status"


def parse_topic(topic: str) -> dict | None:
    """Tách topic VDA 5050: prefix/{manufacturer}/{serial}/{channel}."""
    parts = topic.strip("/").split("/")
    if len(parts) < 4:
        return None
    return {
        "prefix": "/".join(parts[:-3]),
        "manufacturer": parts[-3],
        "serial_number": parts[-2],
        "channel": parts[-1],
    }


def create_client(
    suffix: str = "worker",
    clean_session: bool = False,
    with_last_will: bool = True,
) -> mqtt.Client:
    client_id = f"{settings.MQTT_CLIENT_ID_PREFIX}-{suffix}"
    if len(client_id) > 120:  # giới hạn an toàn của broker
        client_id = client_id[:120]
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=clean_session,
        transport=str(settings.MQTT_TRANSPORT),
        protocol=mqtt.MQTTv311,
    )
    if settings.MQTT_USERNAME:
        client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD or None)
    if settings.MQTT_CA_CERTS:
        client.tls_set(
            ca_certs=settings.MQTT_CA_CERTS,
            certfile=settings.MQTT_CLIENT_CERT or None,
            keyfile=settings.MQTT_CLIENT_KEY or None,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        client.tls_insecure_set(bool(settings.MQTT_TLS_INSECURE))
    if with_last_will:
        client.will_set(
            controller_status_topic(),
            payload=json.dumps({"state": "OFFLINE", "instance": settings.ACS_INSTANCE_NAME}),
            qos=1,
            retain=True,
        )
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


def next_header_id(topic: str, topic_type: str = "ORDER") -> int:
    """Cấp headerId tăng dần cho topic (VDA 5050 yêu cầu tăng mỗi bản tin)."""
    from amr.models import TopicControl

    control, _created = TopicControl.objects.get_or_create(
        topic=topic, defaults={"topic_type": topic_type}
    )
    return control.next_header_id()


# ---------------------------------------------------------------------------
# Khóa phân tán
# ---------------------------------------------------------------------------
DISPATCH_LOCK_KEY = 918_505_050  # khóa cố định cho nghiệp vụ điều phối
PAIRING_LOCK_KEY = 918_505_051


def acquire_advisory_xact_lock(key: int = DISPATCH_LOCK_KEY) -> bool:
    """Lấy khóa trong transaction hiện tại; tự nhả khi COMMIT/ROLLBACK."""
    if connection.vendor != "postgresql":
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [int(key)])
        return True
    except Exception:  # pragma: no cover - phụ thuộc DB runtime
        logger_locks.exception("Không lấy được advisory lock %s", key)
        return False


# ---------------------------------------------------------------------------
# Health / readiness / metrics
# ---------------------------------------------------------------------------
WORKER_COMPONENTS = ("mqtt_worker", "order_pipeline", "aligo_sync")


def _db_check() -> tuple[bool, str]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, "ok"
    except Exception as exc:  # pragma: no cover - phụ thuộc runtime
        return False, str(exc)[:300]


def worker_status(now=None) -> dict:
    now = now or timezone.now()
    stale_after = float(settings.ACS_WORKER_STALE_SECONDS)
    result = {}
    for heartbeat in _heartbeat_model().objects.all():
        age = heartbeat.age_seconds(now)
        result[heartbeat.component] = {
            "last_seen": heartbeat.last_seen.isoformat(),
            "age_seconds": round(age, 1),
            "stale": age > stale_after,
            "instance_id": heartbeat.instance_id,
            "meta": heartbeat.meta,
        }
    return result


def readiness() -> tuple[bool, dict]:
    db_ok, db_detail = _db_check()
    workers = worker_status()
    stale_workers = [name for name, info in workers.items() if info["stale"]]
    pending = _outbox_message_model().objects.filter(
        state=_outbox_message_model().STATE_PENDING
    ).count()
    payload = {
        "status": "ok",
        "instance": settings.ACS_INSTANCE_NAME,
        "env": settings.ACS_ENV,
        "database": {"ok": db_ok, "vendor": connection.vendor, "detail": db_detail},
        "workers": workers,
        "stale_workers": stale_workers,
        "outbox_pending": pending,
        "time": timezone.now().isoformat(),
    }
    strict = bool(getattr(settings, "ACS_READINESS_STRICT", settings.IS_PROD))
    healthy = db_ok and (not strict or (not stale_workers and pending < 5000))
    if not healthy:
        payload["status"] = "unavailable" if not db_ok else "degraded"
    return healthy, payload


def metrics() -> dict:
    from acs.models import Order, OrderMaster, SubOrder, SupplyRequest
    from buffer.models import BufferSlot
    from amr.models import RobotProfile

    robots_by_state: dict[str, int] = {}
    for row in RobotProfile.objects.values("operational_state"):
        state = row["operational_state"]
        robots_by_state[state] = robots_by_state.get(state, 0) + 1

    orders_by_status: dict[str, int] = {}
    for row in Order.objects.values("order_status"):
        status = row["order_status"]
        orders_by_status[status] = orders_by_status.get(status, 0) + 1

    slots_by_status: dict[str, int] = {}
    for row in BufferSlot.objects.values("slot_status"):
        status = row["slot_status"]
        slots_by_status[status] = slots_by_status.get(status, 0) + 1

    return {
        "instance": settings.ACS_INSTANCE_NAME,
        "time": timezone.now().isoformat(),
        "robots": {
            "total": sum(robots_by_state.values()),
            "by_state": robots_by_state,
        },
        "orders": {
            "in_flight": orders_by_status.get("EXECUTING", 0),
            "queued": orders_by_status.get("CREATED", 0),
            "failed": orders_by_status.get("FAILED", 0),
            "by_status": orders_by_status,
            "masters_active": OrderMaster.objects.filter(
                status__in=["NEW", "PROCESSING"]
            ).count(),
            "sub_orders_pending": SubOrder.objects.filter(status="PENDING").count(),
            "supply_requests_pending": SupplyRequest.objects.filter(status="PENDING").count(),
        },
        "buffers": {"total": sum(slots_by_status.values()), "by_status": slots_by_status},
        "mqtt": {
            "outbox_pending": _outbox_message_model().objects.filter(
                state=_outbox_message_model().STATE_PENDING
            ).count(),
            "outbox_failed": _outbox_message_model().objects.filter(
                state=_outbox_message_model().STATE_FAILED
            ).count(),
        },
        "workers": worker_status(),
        "uptime_seconds": round(time.monotonic(), 1),
    }


# ---------------------------------------------------------------------------
# Logging / middleware / system checks
# ---------------------------------------------------------------------------
EXTRA_FIELDS = (
    "robot",
    "order",
    "sub_order",
    "order_master",
    "topic",
    "worker",
    "instance",
    "request_id",
    "duration_ms",
    "cycle",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - phòng thủ
            return json.dumps({"level": record.levelname, "message": record.getMessage()})


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        started = time.monotonic()
        response = self.get_response(request)
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        response["X-Request-ID"] = request.request_id
        response["X-Response-Time-ms"] = str(duration_ms)
        if not request.path.startswith(("/api/health", "/static/", "/media/")):
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            logger_request.log(
                level,
                "%s %s -> %s",
                request.method,
                request.path,
                response.status_code,
                extra={"request_id": request.request_id, "duration_ms": duration_ms},
            )
        return response


@register(Tags.security, deploy=True)
def production_hardening_check(app_configs, **kwargs):
    errors = []
    if not settings.IS_PROD:
        return errors
    if settings.DEBUG:
        errors.append(Error("DEBUG phải tắt ở production.", id="ACS.E001"))
    if settings.ACS_DB_ENGINE == "sqlite":
        errors.append(
            Error(
                "Production phải dùng PostgreSQL (ACS_DB_ENGINE=postgresql); SQLite "
                "không đáp ứng 50 AMR ghi đồng thời.",
                id="ACS.E002",
            )
        )
    if not settings.ACS_REQUIRE_AUTH:
        errors.append(Error("ACS_REQUIRE_AUTH phải bật ở production.", id="ACS.E003"))
    if not settings.MQTT_CA_CERTS:
        errors.append(
            Warning(
                "MQTT chưa cấu hình TLS (MQTT_CA_CERTS). Chỉ chấp nhận trong mạng "
                "công nghiệp biệt lập có ACL tại broker.",
                id="ACS.W001",
            )
        )
    if settings.ACS_CORS_ALLOW_ALL:
        errors.append(Warning("Nên giới hạn CORS bằng ACS_CORS_ALLOWED_ORIGINS.", id="ACS.W002"))
    return errors


# ---------------------------------------------------------------------------
# Worker chạy nền: heartbeat, khóa hết hạn, deadlock, dọn dữ liệu, theo dõi outbox
# ---------------------------------------------------------------------------
class SystemWorker(WorkerThread):
    def __init__(self, thread_name: str = "acs_system", loop_sleep_s: float = 1.0):
        super().__init__(
            thread_name=thread_name,
            worker_name=thread_name,
            loop_sleep_s=loop_sleep_s,
        )
        self._next_prune = 0.0

    def task(self) -> None:
        # Import lười để tránh vòng import: acs_system <-> amr.services/traffic.
        from amr.services import RobotService
        from traffic.deadlock_controller import resolve_deadlock
        from traffic.state_reservation_manager import ReservationManager

        released = ReservationManager().release_expired()
        actions = resolve_deadlock()
        heartbeats = RobotService.monitor_heartbeats()
        if released or actions or heartbeats:
            logger_system.info(
                "System: released=%s deadlock_actions=%s robot_heartbeats=%s",
                released, len(actions), heartbeats,
            )

        # Theo dõi outbox (cảnh báo khi tồn đọng hoặc lỗi)
        pending = MqttOutbox.pending_count()
        failed = MqttOutbox.failed_count()
        if pending > 500 or failed:
            logger_system.warning("Outbox tồn: pending=%s failed=%s", pending, failed)

        # Dọn dữ liệu định kỳ
        interval = float(getattr(settings, "ACS_PRUNE_INTERVAL_SECONDS", 60.0))
        now = time.monotonic()
        if now >= self._next_prune:
            self._next_prune = now + interval
            try:
                call_command("prune_data", verbosity=0)
            except Exception:
                logger_system.exception("Lỗi dọn dữ liệu định kỳ")




