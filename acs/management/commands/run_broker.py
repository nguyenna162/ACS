import asyncio
import logging
from django.core.management.base import BaseCommand
from amqtt.broker import Broker

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Chạy local MQTT broker độc lập trên cổng 1883 phục vụ thử nghiệm không cần Docker"

    def add_arguments(self, parser):
        parser.add_argument('--port', type=int, default=1883)

    def handle(self, *args, **options):
        port = options['port']
        config = {
            'listeners': {
                'default': {
                    'type': 'tcp',
                    'bind': f'0.0.0.0:{port}',
                }
            },
            'sys_interval': 0,
            'auth': {
                'allow-anonymous': True,
            }
        }

        self.stdout.write(self.style.SUCCESS(f"Starting Local MQTT Broker on port {port}..."))

        async def start_broker():
            broker = Broker(config)
            await broker.start()
            self.stdout.write(self.style.SUCCESS(f"MQTT Broker is listening on tcp://0.0.0.0:{port}"))
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                await broker.shutdown()

        try:
            asyncio.run(start_broker())
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS("MQTT Broker stopped."))
