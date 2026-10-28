from django.apps import AppConfig


class AcsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "acs"
    label = "acs"
    verbose_name = "ACS Order Dispatcher & VDA 5050"

    def ready(self):
        from workers import acs_system  # noqa: F401

        # Windows console mặc định cp1252 không mã hoá được tiếng Việt: đổi sang
        # UTF-8 để log/CLI không làm hỏng tiến trình khi in thông báo.
        import sys

        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass

