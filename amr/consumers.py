"""WebSocket HMI: snapshot đội xe 1s/lần + sự kiện nghiệp vụ từ DB Event Outbox."""

import asyncio
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from workers.acs_system import EventBus
from acs.models import RealtimeEvent
from amr.models import RobotProfile
from amr.services import telemetry_snapshot

logger = logging.getLogger("amr.consumers")


class RobotTelemetryConsumer(AsyncWebsocketConsumer):
    GROUP_NAME = "robot_telemetry"

    async def connect(self):
        await self.channel_layer.group_add(self.GROUP_NAME, self.channel_name)
        await self.accept()
        self._closed = False
        self._last_event_id = await self._latest_event_id()
        self._tasks = [
            asyncio.create_task(self._snapshot_loop()),
            asyncio.create_task(self._event_loop()),
        ]
        await self._send_snapshot("initial_snapshot")

    async def disconnect(self, close_code):
        self._closed = True
        for task in getattr(self, "_tasks", []):
            task.cancel()
        await self.channel_layer.group_discard(self.GROUP_NAME, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return
        action = data.get("action")
        if action == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))
        elif action == "refresh":
            await self._send_snapshot("initial_snapshot")

    # -- group message (tương thích consumer cũ) --------------------------
    async def robot_update(self, event):
        await self.send(
            text_data=json.dumps({"type": "robot_update", "data": event["data"]})
        )

    async def traffic_update(self, event):
        await self.send(
            text_data=json.dumps({"type": "traffic_update", "data": event["data"]})
        )

    async def order_update(self, event):
        await self.send(
            text_data=json.dumps({"type": "order_update", "data": event["data"]})
        )

    # -- snapshot/event loops --------------------------------------------
    async def _send_snapshot(self, event_type: str):
        robots = await self.get_fleet_snapshot()
        await self.send(
            text_data=json.dumps(
                {
                    "type": event_type,
                    "robots": robots,
                    "server_time": timezone.now().isoformat(),
                },
                default=str,
            )
        )

    async def _snapshot_loop(self):
        interval = float(getattr(settings, "ACS_WS_SNAPSHOT_INTERVAL_SECONDS", 1.0))
        try:
            while not self._closed:
                await asyncio.sleep(max(0.2, interval))
                await self._send_snapshot("fleet_snapshot")
        except asyncio.CancelledError:  # pragma: no cover - khi client đóng
            return
        except Exception:  # pragma: no cover
            logger.debug("Dừng snapshot loop", exc_info=True)

    async def _event_loop(self):
        max_events = int(getattr(settings, "ACS_WS_MAX_EVENTS_PER_TICK", 50))
        try:
            while not self._closed:
                await asyncio.sleep(0.5)
                events = await self._fetch_events(self._last_event_id, max_events)
                for event in events:
                    self._last_event_id = max(self._last_event_id, event["id"])
                    await self.send(
                        text_data=json.dumps(
                            {
                                "type": "realtime_event",
                                "topic": event["topic"],
                                "data": event["payload"],
                                "id": event["id"],
                            },
                            default=str,
                        )
                    )
        except asyncio.CancelledError:  # pragma: no cover
            return
        except Exception:  # pragma: no cover
            logger.debug("Dừng event loop", exc_info=True)

    @database_sync_to_async
    def _latest_event_id(self) -> int:
        return EventBus.latest_id()

    @database_sync_to_async
    def _fetch_events(self, last_id: int, limit: int) -> list[dict]:
        rows = RealtimeEvent.objects.filter(id__gt=last_id).order_by("id")[:limit]
        return [
            {"id": row.id, "topic": row.topic, "payload": row.payload}
            for row in rows
        ]

    @database_sync_to_async
    def get_fleet_snapshot(self):
        return [telemetry_snapshot(robot) for robot in RobotProfile.objects.all()]

