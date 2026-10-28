from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from acs.views import (
    amr_view,
    dashboard_view,
    map_editor_view,
    orders_view,
    system_view,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("acs.api_urls")),
    path("", dashboard_view, name="dashboard"),
    path("map/", map_editor_view, name="map-editor"),
    path("orders/", orders_view, name="orders"),
    path("amr/", amr_view, name="amr"),
    path("system/", system_view, name="system"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

