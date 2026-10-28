import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'acs.settings')
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

import amr.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(
                amr.routing.websocket_urlpatterns
            )
        )
    ),
})

# Phục vụ file tĩnh khi chạy bằng daphne (tương đương runserver).
# Production: chạy `collectstatic` và để nginx/web server phục vụ /static/.
application = ASGIStaticFilesHandler(application)


