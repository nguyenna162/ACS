"""Test hạ tầng offline: Event Outbox, MQTT Outbox, heartbeat và health API."""

from django.test import TestCase
from django.utils import timezone

from workers.acs_system import EventBus
from acs.models import MqttOutboundMessage, SystemHeartbeat
from workers.acs_system import MqttOutbox


class EventBusTests(TestCase):
    def test_publish_and_incremental_fetch(self):
        EventBus.publish("order", {"event": "created"})
        first_id = EventBus.latest_id()
        EventBus.publish("alert", {"level": "warning"})

        events = EventBus.fetch_after(first_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].topic, "alert")
        self.assertEqual(events[0].payload["level"], "warning")

    def test_publish_many_and_prune(self):
        count = EventBus.publish_many([("order", {"i": 1}), ("traffic", {"i": 2})])
        self.assertEqual(count, 2)
        from acs.models import RealtimeEvent

        RealtimeEvent.objects.update(created_at=timezone.now() - timezone.timedelta(hours=2))
        self.assertGreaterEqual(EventBus.prune(retention_seconds=60), 2)


class MqttOutboxTests(TestCase):
    def test_claim_lease_and_mark_sent(self):
        message = MqttOutbox.enqueue("vda5050/v2/M/AMR_01/order", {"orderId": "X"})
        claimed = MqttOutbox.claim(limit=10, lease_seconds=30)
        self.assertEqual([item.id for item in claimed], [message.id])
        self.assertEqual(claimed[0].attempts, 1)

        # Trong thời gian lease, worker khác không nhận lại bản tin.
        self.assertEqual(MqttOutbox.claim(limit=10, lease_seconds=30), [])

        MqttOutbox.mark_sent([message.id])
        message.refresh_from_db()
        self.assertEqual(message.state, MqttOutboundMessage.STATE_SENT)
        self.assertIsNotNone(message.sent_at)

    def test_failure_marks_failed_after_max_attempts(self):
        message = MqttOutbox.enqueue("vda5050/v2/M/AMR_02/order", {"orderId": "Y"})
        MqttOutbox.claim(limit=10)
        MqttOutbox.mark_failure([message.id], "broker down", max_attempts=1)
        message.refresh_from_db()
        self.assertEqual(message.state, MqttOutboundMessage.STATE_FAILED)
        self.assertIn("broker down", message.last_error)

    def test_pending_count(self):
        MqttOutbox.enqueue("a/b", {})
        self.assertEqual(MqttOutbox.pending_count(), 1)


class HealthEndpointTests(TestCase):
    def test_liveness_ready_and_metrics(self):
        live = self.client.get("/api/health/live/")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["status"], "ok")

        ready = self.client.get("/api/health/ready/")
        self.assertEqual(ready.status_code, 200)
        body = ready.json()
        self.assertTrue(body["database"]["ok"])

        metrics = self.client.get("/api/health/metrics/")
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("robots", metrics.json())
        self.assertIn("orders", metrics.json())


class HeartbeatTests(TestCase):
    def test_record_and_age(self):
        SystemHeartbeat.record("order_pipeline", "host:1", {"cycles": 3})
        heartbeat = SystemHeartbeat.objects.get(component="order_pipeline")
        self.assertGreaterEqual(heartbeat.age_seconds(), 0.0)
        self.assertEqual(heartbeat.meta["cycles"], 3)

        SystemHeartbeat.record("order_pipeline", "host:1", {"cycles": 4})
        heartbeat.refresh_from_db()
        self.assertEqual(heartbeat.meta["cycles"], 4)
        self.assertEqual(SystemHeartbeat.objects.filter(component="order_pipeline").count(), 1)
