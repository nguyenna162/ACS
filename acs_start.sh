#!/usr/bin/env bash
# Khởi động ACS trên Linux KHÔNG cần Docker/Redis.
#   ./acs_start.sh                 # mặc định ACS_ENV=prod, cổng 8000
#   ACS_ENV=dev ./acs_start.sh     # chạy thử với SQLite (nếu ACS_DB_ENGINE=sqlite)
set -euo pipefail
cd "$(dirname "$0")"

# Nạp biến môi trường từ .env nếu có (systemd EnvironmentFile cũng dùng được).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export ACS_ENV="${ACS_ENV:-prod}"
PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if [[ -x .venv/bin/python ]]; then PY=".venv/bin/python"; else PY="python3"; fi
fi
PORT="${ACS_PORT:-8000}"
HOST="${ACS_BIND:-0.0.0.0}"

echo "== ACS khởi động: env=$ACS_ENV db=${ACS_DB_ENGINE:-postgresql} port=$PORT =="
"$PY" manage.py migrate --noinput
"$PY" manage.py check_deployment --skip-mqtt || true
mkdir -p "${ACS_LOG_DIR:-Logs}"

PIDS=()
cleanup() {
  echo "== Dừng ACS =="
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$PY" manage.py run_acs_workers &
PIDS+=("$!")
"$PY" manage.py run_mqtt_worker &
PIDS+=("$!")

exec "$PY" -m daphne -b "$HOST" -p "$PORT" acs.asgi:application
