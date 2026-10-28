from django.core.management.base import BaseCommand
from django.utils import timezone
from map.models import Edge, MapBase, MapTopology, Node, TrafficZone, ZoneSet
from buffer.models import BufferZone, BufferSlot
from amr.models import RobotProfile, ensure_topic_controls

class Command(BaseCommand):
    help = "Khởi tạo dữ liệu mẫu bản đồ nhà xưởng, trạm đệm và đội xe AMR theo kịch bản nghiệm thu VDA 5050"

    def handle(self, *args, **options):
        self.stdout.write("Initializing demo factory topology and fleet data...")

        # 1. Map Topology
        topology, _ = MapTopology.objects.get_or_create(
            map_id="MAP_FACTORY_01",
            defaults={
                "map_name": "SEVT Smart Factory Floor A",
                "version": "2.0.0",
                "is_active": True
            }
        )

        # 2. Nodes: Grid with orthogonal intersection at N_10 (N_09 -> N_10)
        # Coordinates (x, y) in meters
        node_coords = {
            "N_01": (0.0, 0.0),
            "N_02": (10.0, 0.0),
            "N_03": (20.0, 0.0),
            "N_04": (30.0, 0.0),
            "N_05": (30.0, 10.0),
            "N_06": (0.0, 20.0),
            "N_07": (10.0, 20.0),
            "N_08": (20.0, 20.0),
            "N_09": (30.0, 20.0),
            "N_10": (40.0, 20.0),  # Intersection node
            "N_11": (50.0, 20.0),
            "N_12": (10.0, 30.0),
            "N_13": (40.0, 10.0),  # Vertical approach to N_10
            "N_14": (40.0, 30.0),  # Vertical departure from N_10
            "N_15": (60.0, 20.0),  # Destination for Drop Slot
        }

        created_nodes = {}
        for nid, (x, y) in node_coords.items():
            n, _ = Node.objects.update_or_create(
                node_id=nid,
                defaults={
                    "map_topology": topology,
                    "pos_x": x,
                    "pos_y": y,
                    "theta": 0.0,
                    "is_occupied": False,
                    "locked_by": None,
                    "locked_at": None
                }
            )
            created_nodes[nid] = n

        # 3. Edges
        # Horizontal corridor: N_01 -> N_02 -> N_03 -> N_04
        # Connect to N_05 -> N_13 -> N_10 -> N_11 -> N_15
        # Cross corridor: N_06 -> N_07 -> N_08 -> N_09 -> N_10 -> N_11 -> N_15
        # Vertical corridor: N_13 -> N_10 -> N_14
        connections = [
            ("N_01", "N_02", 10.0, "BI"),
            ("N_02", "N_03", 10.0, "BI"),
            ("N_03", "N_04", 10.0, "BI"),
            ("N_04", "N_05", 10.0, "BI"),
            ("N_05", "N_13", 10.0, "BI"),
            ("N_13", "N_10", 10.0, "BI"),
            ("N_10", "N_14", 10.0, "BI"),
            ("N_06", "N_07", 10.0, "BI"),
            ("N_07", "N_08", 10.0, "BI"),
            ("N_08", "N_09", 10.0, "BI"),
            ("N_09", "N_10", 10.0, "BI"),
            ("N_10", "N_11", 10.0, "BI"),
            ("N_11", "N_15", 10.0, "BI"),
            ("N_07", "N_12", 10.0, "BI"),
            ("N_02", "N_07", 20.0, "BI"),
        ]

        for s_id, e_id, dist, dir_choice in connections:
            edge_id = f"E_{s_id}_{e_id}"
            Edge.objects.update_or_create(
                edge_id=edge_id,
                defaults={
                    "start_node": created_nodes[s_id],
                    "end_node": created_nodes[e_id],
                    "distance": dist,
                    "max_speed": 1.2,
                    "direction": dir_choice,
                    "is_locked": False,
                    "locked_by": None
                }
            )

        # 4. Buffer Zones
        zone_pick, _ = BufferZone.objects.update_or_create(
            zone_id="ZONE_STORAGE_01",
            defaults={
                "map_topology": topology,
                "zone_name": "Khu vực kho nguyên vật liệu",
                "zone_type": "STORAGE",
                "capacity": 5
            }
        )
        zone_drop, _ = BufferZone.objects.update_or_create(
            zone_id="ZONE_LINE_01",
            defaults={
                "map_topology": topology,
                "zone_name": "Dây chuyền lắp ráp chính",
                "zone_type": "LINE",
                "capacity": 5
            }
        )
        zone_charge, _ = BufferZone.objects.update_or_create(
            zone_id="ZONE_CHARGE_01",
            defaults={
                "map_topology": topology,
                "zone_name": "Trạm sạc tự động",
                "zone_type": "CHARGING",
                "capacity": 2
            }
        )

        # 5. Buffer Slots
        # SLOT_PICK_01 at N_01 (OCCUPIED with PALLET_A100)
        BufferSlot.objects.update_or_create(
            slot_id="SLOT_PICK_01",
            defaults={
                "zone": zone_pick,
                "node": created_nodes["N_01"],
                "slot_status": "OCCUPIED",
                "pallet_code": "PALLET_A100"
            }
        )
        # SLOT_PICK_02 at N_06 (OCCUPIED with PALLET_B200)
        BufferSlot.objects.update_or_create(
            slot_id="SLOT_PICK_02",
            defaults={
                "zone": zone_pick,
                "node": created_nodes["N_06"],
                "slot_status": "OCCUPIED",
                "pallet_code": "PALLET_B200"
            }
        )
        # SLOT_DROP_01 at N_15 (EMPTY)
        BufferSlot.objects.update_or_create(
            slot_id="SLOT_DROP_01",
            defaults={
                "zone": zone_drop,
                "node": created_nodes["N_15"],
                "slot_status": "EMPTY",
                "pallet_code": None
            }
        )
        # SLOT_DROP_02 at N_14 (EMPTY)
        BufferSlot.objects.update_or_create(
            slot_id="SLOT_DROP_02",
            defaults={
                "zone": zone_drop,
                "node": created_nodes["N_14"],
                "slot_status": "EMPTY",
                "pallet_code": None
            }
        )
        # SLOT_CHARGE_01 at N_12 (EMPTY)
        BufferSlot.objects.update_or_create(
            slot_id="SLOT_CHARGE_01",
            defaults={
                "zone": zone_charge,
                "node": created_nodes["N_12"],
                "slot_status": "EMPTY",
                "pallet_code": None
            }
        )

        # 6. Robot Fleet (AMR_01, AMR_02, AMR_03)
        # AMR_01: At N_01, 90% Battery, IDLE
        RobotProfile.objects.update_or_create(
            serial_number="AMR_01",
            defaults={
                "manufacturer": "SEVT_Robotics",
                "current_node": created_nodes["N_01"],
                "pos_x": 0.0,
                "pos_y": 0.0,
                "theta": 0.0,
                "battery_level": 90.0,
                "operational_state": "IDLE"
            }
        )
        # AMR_02: At N_06, 85% Battery, IDLE
        RobotProfile.objects.update_or_create(
            serial_number="AMR_02",
            defaults={
                "manufacturer": "SEVT_Robotics",
                "current_node": created_nodes["N_06"],
                "pos_x": 0.0,
                "pos_y": 20.0,
                "theta": 0.0,
                "battery_level": 85.0,
                "operational_state": "IDLE"
            }
        )
        # AMR_03: At N_12, 22% Battery (< 25% threshold), IDLE
        RobotProfile.objects.update_or_create(
            serial_number="AMR_03",
            defaults={
                "manufacturer": "SEVT_Robotics",
                "current_node": created_nodes["N_12"],
                "pos_x": 10.0,
                "pos_y": 30.0,
                "theta": 0.0,
                "battery_level": 22.0,
                "operational_state": "IDLE"
            }
        )

        # 7. Lớp bản đồ mở rộng (MapBase/ZoneSet/TrafficZone) + topic MQTT theo AMR
        map_base, _ = MapBase.objects.update_or_create(
            name="SEVT Smart Factory Floor A",
            defaults={
                "topology": topology,
                "part": "2G",
                "version": "2.0.0",
                "status": "ACTIVE",
            },
        )
        Node.objects.filter(map_topology=topology).update(map=map_base)

        zone_set, _ = ZoneSet.objects.get_or_create(
            map=map_base,
            name="ZONESET_MAIN",
            defaults={
                "description": "Zone set mặc định nhà máy",
                "version": "2.0.0",
                "is_active": True,
            },
        )
        zone_type_map = {"STORAGE": "BUFFER", "LINE": "LINE", "CHARGING": "CHARGING"}
        for zone in BufferZone.objects.all():
            TrafficZone.objects.update_or_create(
                zone_set=zone_set,
                name=zone.zone_id,
                defaults={
                    "map": map_base,
                    "zone_type": zone_type_map.get(zone.zone_type, "LINE"),
                    "max_occupancy": max(1, zone.capacity),
                    "priority": 0,
                    "max_speed": 1.2,
                },
            )

        BufferSlot.objects.filter(slot_id__startswith="SLOT_PICK").update(
            buffer_type="STORAGE", product_model="MODEL_X", enable=True
        )
        BufferSlot.objects.filter(slot_id__startswith="SLOT_DROP").update(
            buffer_type="LINE", product_model="MODEL_X", enable=True
        )
        BufferSlot.objects.filter(slot_id__startswith="SLOT_CHARGE").update(
            buffer_type="CHARGING", enable=True
        )
        RobotProfile.objects.filter(
            serial_number__in=["AMR_01", "AMR_02", "AMR_03"]
        ).update(is_online=True, is_idle=True)
        for robot in RobotProfile.objects.filter(
            serial_number__in=["AMR_01", "AMR_02", "AMR_03"]
        ):
            ensure_topic_controls(robot)

        # 8. Reset trạng thái demo (idempotent): hủy đơn đang mở, giải phóng khóa
        from acs.models import Order
        from map.traffic_lock import TrafficController

        for order in Order.objects.filter(
            order_status__in=["CREATED", "ASSIGNED", "EXECUTING"]
        ):
            TrafficController.release_order_locks(order)
            Order.objects.filter(pk=order.pk).update(
                order_status="CANCELLED", completed_at=timezone.now()
            )
        RobotProfile.objects.filter(
            serial_number__in=["AMR_01", "AMR_02", "AMR_03"]
        ).update(
            is_idle=True,
            is_used_by_master=None,
            order_id="",
            order_update_id=0,
            new_base_request=False,
        )


        self.stdout.write(self.style.SUCCESS("Successfully seeded demo data:"))
        self.stdout.write(f"- 1 Map Topology ({topology.map_name}) + MapBase/ZoneSet/TrafficZone")
        self.stdout.write(f"- {len(node_coords)} Nodes (Intersection at N_10)")
        self.stdout.write(f"- {len(connections)} Edges")
        self.stdout.write("- 3 Buffer Zones (Storage, Assembly Line, Charging)")
        self.stdout.write("- 5 Buffer Slots (including SLOT_PICK_01, SLOT_DROP_01)")
        self.stdout.write("- 3 AMR Fleet (AMR_01 batt 90%, AMR_02 batt 85%, AMR_03 batt 22%)")
