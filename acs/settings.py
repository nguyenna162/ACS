"""
Cấu hình ACS - Automotive Control System (AMR/AGV, VDA 5050).

Tài liệu tham chiếu: docs/SRS_ACS.md (SRS tổng hợp từ tài liệu dự án) và
Readme.md. Cấu hình hướng tới môi trường nhà máy OFFLINE, không Docker và
không Redis:

* PostgreSQL là CSDL vận hành (SQLite chỉ dùng dev/test).
* Broker MQTT vendor-neutral (EMQX trong tài liệu; Mosquitto hoặc broker
  Python nội bộ cho dev). Không dò cổng tự động.
* Realtime dùng DB Event Outbox, không cần Redis channel layer.
* Mọi tham số nghiệp vụ đọc từ biến môi trường để tinh chỉnh theo nhà máy.

Chọn profile bằng ACS_ENV=dev|prod|test. Profile prod bắt buộc SECRET_KEY,
ALLOWED_HOSTS, mật khẩu PostgreSQL và bật xác thực API.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers đọc biến môi trường (không phụ thuộc thư viện ngoài)
# ---------------------------------------------------------------------------
def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} phải là số nguyên, nhận được: {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} phải là số thực, nhận được: {raw!r}") from exc


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
ACS_ENV = os.getenv("ACS_ENV", "dev").strip().lower()
if ACS_ENV not in ("dev", "prod", "test"):
    raise ImproperlyConfigured("ACS_ENV chỉ nhận giá trị dev | prod | test")
IS_PROD = ACS_ENV == "prod"
IS_TEST = ACS_ENV == "test"

DEBUG = env_bool("DJANGO_DEBUG", default=not IS_PROD)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if IS_PROD:
        raise ImproperlyConfigured(
            "Thiếu DJANGO_SECRET_KEY. Production phải cấu hình khóa bí mật "
            "(>= 50 ký tự) trước khi khởi động dịch vụ."
        )
    SECRET_KEY = "dev-only-insecure-secret-key-acs-vda5050-do-not-use-in-production"

ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    default="" if IS_PROD else "127.0.0.1,localhost,testserver",
)
if IS_PROD and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Thiếu DJANGO_ALLOWED_HOSTS (ví dụ: 10.20.30.11,acs.factory.local)."
    )

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "channels",
    "acs.apps.AcsConfig",
    "amr.apps.AmrConfig",
    "map.apps.MapConfig",
    "buffer.apps.BufferConfig",
    "workers.apps.WorkersConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "workers.acs_system.RequestContextMiddleware",
]

ROOT_URLCONF = "acs.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "acs.wsgi.application"
ASGI_APPLICATION = "acs.asgi.application"


# ---------------------------------------------------------------------------
# Cơ sở dữ liệu - lựa chọn TƯỜNG MINH, không dò cổng / không fallback
# ---------------------------------------------------------------------------
ACS_DB_ENGINE = os.getenv("ACS_DB_ENGINE", "postgresql" if IS_PROD else "sqlite").strip().lower()

if ACS_DB_ENGINE in ("postgres", "postgresql"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "acs_db"),
            "USER": os.getenv("POSTGRES_USER", "acs_user"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("ACS_DB_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "connect_timeout": env_int("ACS_DB_CONNECT_TIMEOUT", 5),
                # statement_timeout bảo vệ hệ thống khỏi truy vấn treo do khóa dòng.
                "options": f"-c statement_timeout={env_int('ACS_DB_STATEMENT_TIMEOUT_MS', 15000)}",
            },
        }
    }
    if IS_PROD and not DATABASES["default"]["PASSWORD"]:
        raise ImproperlyConfigured("Thiếu POSTGRES_PASSWORD cho ACS_DB_ENGINE=postgresql.")
elif ACS_DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("ACS_SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
            "OPTIONS": {"timeout": env_int("ACS_SQLITE_TIMEOUT", 20)},
        }
    }
else:
    raise ImproperlyConfigured("ACS_DB_ENGINE chỉ nhận postgresql | sqlite")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Realtime: Channels chạy trong cùng tiến trình ASGI (không Redis).
# WebSocket dùng Event Outbox trong DB nên vẫn đúng khi tách web/worker.
# ---------------------------------------------------------------------------
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


# ---------------------------------------------------------------------------
# Xác thực & API
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": env_int("ACS_PASSWORD_MIN_LENGTH", 10)},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Production bắt buộc xác thực; dev mở để lập trình giao diện.
ACS_REQUIRE_AUTH = env_bool("ACS_REQUIRE_AUTH", default=IS_PROD)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated"
        if ACS_REQUIRE_AUTH
        else "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_PARSER_CLASSES": (
        ["rest_framework.parsers.JSONParser"]
        if IS_PROD
        else [
            "rest_framework.parsers.JSONParser",
            "rest_framework.parsers.FormParser",
            "rest_framework.parsers.MultiPartParser",
        ]
    ),
    "DEFAULT_RENDERER_CLASSES": (
        ["rest_framework.renderers.JSONRenderer"]
        if IS_PROD
        else [
            "rest_framework.renderers.JSONRenderer",
            "rest_framework.renderers.BrowsableAPIRenderer",
        ]
    ),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.getenv("ACS_THROTTLE_ANON", "120/min"),
        "user": os.getenv("ACS_THROTTLE_USER", "1200/min"),
        # Lệnh điều khiển robot cần giới hạn chặt để tránh bão lệnh.
        "control": os.getenv("ACS_THROTTLE_CONTROL", "60/min"),
    },
    "DATETIME_FORMAT": "%Y-%m-%dT%H:%M:%S.%fZ",
    "COERCE_DECIMAL_TO_STRING": False,
}

# CORS chỉ mở cho HMI/thiết bị ngoài khi khai báo tường minh ở production.
CORS_ALLOWED_ORIGINS = env_list("ACS_CORS_ALLOWED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = env_bool("ACS_CORS_ALLOW_ALL", default=not IS_PROD)
CORS_ALLOW_CREDENTIALS = True


# ---------------------------------------------------------------------------
# Bảo mật
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # HMI đọc CSRF token để gọi API same-origin

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SESSION_COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", default=SECURE_SSL_REDIRECT)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
if env_bool("DJANGO_BEHIND_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# ---------------------------------------------------------------------------
# I18N / thời gian
# ---------------------------------------------------------------------------
LANGUAGE_CODE = os.getenv("DJANGO_LANGUAGE_CODE", "en-us")
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Asia/Ho_Chi_Minh")
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static / media (bản đồ upload theo tài liệu: media/maps)
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("ACS_MEDIA_ROOT", str(BASE_DIR / "media")))
MAP_IMAGE_ROOT = MEDIA_ROOT / "maps"


# ---------------------------------------------------------------------------
# Logging - ghi file luân chuyển, không phụ thuộc dịch vụ ngoài
# ---------------------------------------------------------------------------
LOG_DIR = Path(os.getenv("ACS_LOG_DIR", str(BASE_DIR / "Logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = os.getenv("ACS_LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = env_int("ACS_LOG_MAX_BYTES", 20 * 1024 * 1024)
LOG_BACKUP_COUNT = env_int("ACS_LOG_BACKUP_COUNT", 10)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # Định dạng DUY NHẤT cho toàn bộ log:
        #   [time] [LEVEL] [COMPONENT] [SUBJECT] message
        "bracket": {"()": "workers.acs_system.BracketFormatter"},
        # Giữ lại cho nhu cầu log có cấu trúc (parse máy) - không dùng mặc định.
        "json": {"()": "workers.acs_system.JsonFormatter"},
    },
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "bracket",
            "level": LOG_LEVEL,
        },
        "file_app": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "acs-app.log"),
            "maxBytes": LOG_MAX_BYTES,
            "backupCount": LOG_BACKUP_COUNT,
            "formatter": "bracket",
            "encoding": "utf-8",
            "level": LOG_LEVEL,
        },
        "file_worker": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "acs-worker.log"),
            "maxBytes": LOG_MAX_BYTES,
            "backupCount": LOG_BACKUP_COUNT,
            "formatter": "bracket",
            "encoding": "utf-8",
            "level": LOG_LEVEL,
        },
        "file_error": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "acs-error.log"),
            "maxBytes": LOG_MAX_BYTES,
            "backupCount": LOG_BACKUP_COUNT,
            "formatter": "bracket",
            "encoding": "utf-8",
            "level": "ERROR",
        },
        "mail_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "filters": ["require_debug_false"],
            "level": "ERROR",
        },
    },
    "root": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {
            "handlers": ["console", "file_app", "file_error"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.db.backends": {"level": "WARNING", "propagate": True},
        # Ép mọi logger Django/Daphne/Twisted đi qua format [bracket],
        # tránh dòng log thô trùng lặp từ DEFAULT_LOGGING của Django.
        "django": {"handlers": ["console", "file_app", "file_error", "mail_admins"], "level": LOG_LEVEL, "propagate": False},
        "django.server": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "daphne": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "twisted": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "acs": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "map": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "amr": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "traffic": {"handlers": ["console", "file_app", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "workers": {"handlers": ["console", "file_worker", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "worker": {"handlers": ["console", "file_worker", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "integrations": {"handlers": ["console", "file_worker", "file_error"], "level": LOG_LEVEL, "propagate": False},
        "paho": {"level": "WARNING", "propagate": False},
    },
}


# ---------------------------------------------------------------------------
# MQTT / VDA 5050
# Broker trong tài liệu: EMQX. Thiết kế vendor-neutral (EMQX/Mosquitto/amqtt).
# ---------------------------------------------------------------------------
MQTT_BROKER = os.getenv("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = env_int("MQTT_PORT", 1883)
MQTT_KEEPALIVE = env_int("MQTT_KEEPALIVE", 60)
MQTT_QOS = env_int("MQTT_QOS", 1)
MQTT_CLIENT_ID_PREFIX = os.getenv("MQTT_CLIENT_ID_PREFIX", "ACS_CONTROLLER")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_CA_CERTS = os.getenv("MQTT_CA_CERTS", "")
MQTT_CLIENT_CERT = os.getenv("MQTT_CLIENT_CERT", "")
MQTT_CLIENT_KEY = os.getenv("MQTT_CLIENT_KEY", "")
MQTT_TLS_INSECURE = env_bool("MQTT_TLS_INSECURE", default=False)
MQTT_TRANSPORT = os.getenv("MQTT_TRANSPORT", "tcp")

VDA5050_TOPIC_PREFIX = os.getenv("VDA5050_TOPIC_PREFIX", "vda5050/v2")
VDA5050_VERSION = os.getenv("VDA5050_VERSION", "2.0.0")

# Số nút tối đa được mở khóa trước (sliding horizon) theo tài liệu dự án.
ACS_HORIZON_WINDOW_K = env_int("ACS_HORIZON_WINDOW_K", 3)

# Điều phối: cost = W1 * (distance / D_MAX_MAP) + W2 * (1 - battery/100)
ACS_DISPATCH_W1_DISTANCE = env_float("ACS_DISPATCH_W1_DISTANCE", 0.7)
ACS_DISPATCH_W2_BATTERY = env_float("ACS_DISPATCH_W2_BATTERY", 0.3)
ACS_D_MAX_MAP = env_float("ACS_D_MAX_MAP", 100.0)
ACS_MIN_BATTERY_PERCENT = env_float("ACS_MIN_BATTERY_PERCENT", 25.0)

# Quản lý pin tự động
ACS_AUTO_CHARGE_ENABLED = env_bool("ACS_AUTO_CHARGE_ENABLED", default=True)
ACS_AUTO_CHARGE_BATTERY_PERCENT = env_float("ACS_AUTO_CHARGE_BATTERY_PERCENT", 20.0)
ACS_CHARGE_TARGET_PERCENT = env_float("ACS_CHARGE_TARGET_PERCENT", 90.0)

# Heartbeat (theo tài liệu: >5s WARNING, >10s OFFLINE)
ACS_HEARTBEAT_WARNING_SECONDS = env_float("ACS_HEARTBEAT_WARNING_SECONDS", 5.0)
ACS_HEARTBEAT_OFFLINE_SECONDS = env_float("ACS_HEARTBEAT_OFFLINE_SECONDS", 10.0)

# Khóa nút/edge giao thông
ACS_RESERVATION_TTL_SECONDS = env_float("ACS_RESERVATION_TTL_SECONDS", 45.0)

# Vòng lặp scheduler / ingest
ACS_SCHEDULER_INTERVAL_SECONDS = env_float("ACS_SCHEDULER_INTERVAL_SECONDS", 1.0)
ACS_STATE_FLUSH_INTERVAL_SECONDS = env_float("ACS_STATE_FLUSH_INTERVAL_SECONDS", 0.5)
ACS_STATE_BUFFER_MAX = env_int("ACS_STATE_BUFFER_MAX", 5000)
ACS_TELEMETRY_SAMPLE_SECONDS = env_float("ACS_TELEMETRY_SAMPLE_SECONDS", 5.0)
ACS_TELEMETRY_RETENTION_DAYS = env_int("ACS_TELEMETRY_RETENTION_DAYS", 7)
ACS_EVENT_RETENTION_SECONDS = env_int("ACS_EVENT_RETENTION_SECONDS", 3600)
ACS_OUTBOX_RETENTION_DAYS = env_int("ACS_OUTBOX_RETENTION_DAYS", 7)
ACS_OUTBOX_MAX_ATTEMPTS = env_int("ACS_OUTBOX_MAX_ATTEMPTS", 20)
ACS_OUTBOX_BATCH_SIZE = env_int("ACS_OUTBOX_BATCH_SIZE", 50)

# Đăng ký robot tự động: production TẮT (robot phải khai báo trước).
ACS_AUTO_REGISTER_ROBOTS = env_bool("ACS_AUTO_REGISTER_ROBOTS", default=not IS_PROD)

# Đơn hàng
ACS_ORDER_QUEUE_TIMEOUT_SECONDS = env_float("ACS_ORDER_QUEUE_TIMEOUT_SECONDS", 600.0)
ACS_ORDER_MAX_QUEUE = env_int("ACS_ORDER_MAX_QUEUE", 500)
# Robot có thể chưa gửi actionStates -> cho phép hoàn thành khi tới nút cuối.
ACS_REQUIRE_ACTION_FEEDBACK = env_bool("ACS_REQUIRE_ACTION_FEEDBACK", default=False)

# Worker pipeline theo tài liệu (acs_order_send / acs_order_master)
ACS_WORKER_LOOP_SLEEP_SECONDS = env_float("ACS_WORKER_LOOP_SLEEP_SECONDS", 0.5)
ACS_SENT_TIMEOUT_SECONDS = env_float("ACS_SENT_TIMEOUT_SECONDS", 300.0)
ACS_RESEND_INTERVAL_SECONDS = env_float("ACS_RESEND_INTERVAL_SECONDS", 3.0)
ACS_MAX_CACHE_SIZE = env_int("ACS_MAX_CACHE_SIZE", 20000)
ACS_ORDER_MASTER_POOL = env_int("ACS_ORDER_MASTER_POOL", 3)
ACS_PAIRING_BATCH_SIZE = env_int("ACS_PAIRING_BATCH_SIZE", 50)
ACS_ANCHOR_LOOKAHEAD_NODES = env_int("ACS_ANCHOR_LOOKAHEAD_NODES", 3)

# Aligo MES (tài liệu: polling 3-5s). Mặc định TẮT để hệ offline không gọi mạng.
ALIGO_ENABLED = env_bool("ALIGO_ENABLED", default=False)
ALIGO_BASE_URL = os.getenv("ALIGO_BASE_URL", "")
ALIGO_API_KEY = os.getenv("ALIGO_API_KEY", "")
ALIGO_TIMEOUT_SECONDS = env_float("ALIGO_TIMEOUT_SECONDS", 5.0)
ALIGO_POLL_INTERVAL_SECONDS = env_float("ALIGO_POLL_INTERVAL_SECONDS", 3.0)
ALIGO_VERIFY_TLS = env_bool("ALIGO_VERIFY_TLS", default=True)

# Realtime
ACS_WS_SNAPSHOT_INTERVAL_SECONDS = env_float("ACS_WS_SNAPSHOT_INTERVAL_SECONDS", 1.0)
ACS_WS_MAX_EVENTS_PER_TICK = env_int("ACS_WS_MAX_EVENTS_PER_TICK", 50)
ACS_HEALTH_PUBLIC = env_bool("ACS_HEALTH_PUBLIC", default=True)
ACS_WORKER_STALE_SECONDS = env_float("ACS_WORKER_STALE_SECONDS", 20.0)

# Dọn dẹp dữ liệu (worker chạy mỗi ACS_PRUNE_INTERVAL_SECONDS)
ACS_PRUNE_INTERVAL_SECONDS = env_float("ACS_PRUNE_INTERVAL_SECONDS", 60.0)
ACS_SYSTEM_LOG_RETENTION_DAYS = env_int("ACS_SYSTEM_LOG_RETENTION_DAYS", 30)

# Topology cache (A*/Dijkstra in-memory)
ACS_TOPOLOGY_CACHE_SECONDS = env_float("ACS_TOPOLOGY_CACHE_SECONDS", 60.0)

# Định danh hệ thống
ACS_DEFAULT_MANUFACTURER = os.getenv("ACS_DEFAULT_MANUFACTURER", "SEVT_Robotics")
ACS_INSTANCE_NAME = os.getenv("ACS_INSTANCE_NAME", "ACS-FACTORY-01")

