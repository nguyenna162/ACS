"""Client Aligo MES/ERP - chạy offline an toàn.

Tài liệu: ACS đồng bộ trạng thái robot/line và nhận lệnh cấp vật tư từ Aligo
(polling 3-5s). Trong nhà máy offline, tích hợp mặc định TẮT
(``ALIGO_ENABLED=False``) và mọi lỗi mạng được cô lập: worker chỉ ghi log,
không ảnh hưởng điều phối nội bộ.

Dùng ``urllib`` (stdlib) để không phát sinh phụ thuộc mới khi triển khai.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger("integrations.aligo")


class AligoError(Exception):
    """Lỗi giao tiếp Aligo (đã được worker bắt và ghi log)."""


class AligoDisabled(AligoError):
    """Tích hợp Aligo đang tắt theo cấu hình."""


class AligoClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        verify_tls: bool | None = None,
        enabled: bool | None = None,
    ):
        self.base_url = (base_url if base_url is not None else settings.ALIGO_BASE_URL or "").rstrip("/")
        self.api_key = api_key if api_key is not None else (settings.ALIGO_API_KEY or "")
        self.timeout = float(
            settings.ALIGO_TIMEOUT_SECONDS if timeout is None else timeout
        )
        self.verify_tls = bool(settings.ALIGO_VERIFY_TLS if verify_tls is None else verify_tls)
        self.enabled = bool(settings.ALIGO_ENABLED if enabled is None else enabled)
        if self.enabled and not self.base_url:
            raise AligoError("ALIGO_ENABLED=true nhưng thiếu ALIGO_BASE_URL")

    # -- HTTP -------------------------------------------------------------
    def _request(self, method: str, path: str, payload: dict | None = None,
                 params: dict | None = None) -> dict:
        if not self.enabled:
            raise AligoDisabled("Tích hợp Aligo đang tắt (ALIGO_ENABLED=false)")
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method.upper())
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.api_key:
            request.add_header("X-API-KEY", self.api_key)
            request.add_header("Authorization", f"Bearer {self.api_key}")

        context = None
        if url.startswith("https") and not self.verify_tls:
            import ssl

            context = ssl._create_unverified_context()  # noqa: SLF001 - cấu hình nội bộ

        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise AligoError(f"Aligo HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AligoError(f"Không kết nối được Aligo: {exc}") from exc
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise AligoError("Aligo trả về JSON không hợp lệ") from exc

    # -- API --------------------------------------------------------------
    def health(self) -> dict:
        return self._request("GET", "/api/health/")

    def get_robot_status(self) -> list[dict]:
        data = self._request("GET", "/api/robots/status/")
        return data.get("results", data) if isinstance(data, dict) else list(data or [])

    def get_line_status(self) -> list[dict]:
        data = self._request("GET", "/api/lines/status/")
        return data.get("results", data) if isinstance(data, dict) else list(data or [])

    def get_supply_commands(self, since: str | None = None) -> list[dict]:
        params = {"since": since} if since else None
        data = self._request("GET", "/api/supply-commands/", params=params)
        return data.get("results", data) if isinstance(data, dict) else list(data or [])

    def ack_supply_command(self, command_id: str, status: str, message: str = "") -> dict:
        return self._request(
            "POST",
            f"/api/supply-commands/{command_id}/ack/",
            payload={"status": status, "message": message},
        )

    def push_robot_status(self, payload: dict) -> dict:
        return self._request("POST", "/api/robots/status/", payload=payload)
