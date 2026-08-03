from dataclasses import replace
from pathlib import Path
import shutil

from keychain_fair.adapters import AdapterResult, CuraEngineSlicer, OctoPrintController, OpenScadModelGenerator
from keychain_fair.config import (
    AppSettings,
    ModelSettings,
    OctoPrintSettings,
    PrinterControlSettings,
    PROJECT_ROOT,
    QueueSettings,
    SlicerSettings,
    ToolSettings,
)
from keychain_fair.main import create_app


ADMIN_HEADERS = {"X-Admin-Pin": "9999"}
CASHIER_HEADERS = {"X-Cashier-Pin": "9999"}
INTERNAL_HEADERS = {"X-Internal-Token": "test-internal-token"}


class FakeGenerator(OpenScadModelGenerator):
    def check_ready(self):
        return AdapterResult(True, "fake OpenSCAD ready")

    def generate_order_stl(self, order, design):
        order_dir = self.settings.generated_dir / "orders" / order["id"]
        order_dir.mkdir(parents=True, exist_ok=True)
        stl = order_dir / f"{order['id']}.stl"
        scad = order_dir / f"{order['id']}.scad"
        stl.write_text("solid fake\nendsolid fake\n", encoding="utf-8")
        scad.write_text("// fake\n", encoding="utf-8")
        return AdapterResult(True, "fake stl", stl, {"wrapper_scad_path": str(scad)})

    def combine_batch_stl(self, batch_id, layout_items):
        batch_dir = self.settings.generated_dir / "batches" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        stl = batch_dir / f"{batch_id}_plate.stl"
        scad = batch_dir / f"{batch_id}_plate.scad"
        stl.write_text("solid batch\nendsolid batch\n", encoding="utf-8")
        scad.write_text("// batch\n", encoding="utf-8")
        return AdapterResult(True, "fake batch", stl, {"plate_scad_path": str(scad)})

    def generate_preview_stl(self, preview_id, order, design):
        preview_dir = self.settings.generated_dir / "editor" / "previews" / preview_id
        preview_dir.mkdir(parents=True, exist_ok=True)
        stl = preview_dir / f"{preview_id}.stl"
        scad = preview_dir / f"{preview_id}.scad"
        stl.write_text("solid preview\nendsolid preview\n", encoding="utf-8")
        scad.write_text("// preview\n", encoding="utf-8")
        return AdapterResult(True, "fake preview", stl, {"wrapper_scad_path": str(scad)})

    def render_order_preview_png(self, cache_key, order, design, image_size=(480, 320)):
        preview_dir = self.settings.generated_dir / "telegram" / "previews" / cache_key
        preview_dir.mkdir(parents=True, exist_ok=True)
        png = preview_dir / "preview.png"
        scad = preview_dir / "preview.scad"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        scad.write_text("// preview png\n", encoding="utf-8")
        return AdapterResult(True, "fake preview png", png, {"wrapper_scad_path": str(scad)})


class FakeSlicer(CuraEngineSlicer):
    def __init__(self, settings):
        super().__init__(settings)
        self.pause_heights = []

    def slice_plate(self, plate_stl_path, batch_id, filament_change_height_mm=None):
        self.pause_heights.append(filament_change_height_mm)
        batch_dir = self.settings.generated_dir / "batches" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        gcode = batch_dir / f"{batch_id}.gcode"
        gcode.write_text("; fake gcode\n", encoding="utf-8")
        return AdapterResult(True, "fake gcode", gcode)


class FakePrinter(OctoPrintController):
    def __init__(self, settings, job_result, printer_result, connection_result=None):
        super().__init__(settings)
        self.job_result = job_result
        self.printer_result = printer_result
        self.connection_result = connection_result
        self.ensure_calls = 0
        self.uploads = []
        self.bed_targets = []
        self.tool_targets = []
        self.cancel_calls = 0
        self.cancel_result = AdapterResult(True, "fake cancelled")
        self.resume_calls = 0
        self.resume_result = AdapterResult(True, "fake resumed")

    def ensure_connected(self, attempts=3, settle_seconds=1.0):
        self.ensure_calls += 1
        return self.connection_result or AdapterResult(True, "fake connected")

    def get_job(self):
        return self.job_result

    def get_printer(self):
        return self.printer_result

    def upload_and_print(self, gcode_path):
        self.uploads.append(gcode_path)
        return AdapterResult(True, "fake print started", gcode_path)

    def set_bed_target(self, target_c):
        self.bed_targets.append(int(target_c))
        return AdapterResult(True, f"fake bed target {int(target_c)}", extra={"target_c": int(target_c)})

    def set_tool_target(self, target_c, tool="tool0"):
        self.tool_targets.append((tool, int(target_c)))
        return AdapterResult(True, f"fake {tool} target {int(target_c)}", extra={"target_c": int(target_c), "tool": tool})

    def cancel_job(self):
        self.cancel_calls += 1
        return self.cancel_result

    def resume_print(self):
        self.resume_calls += 1
        return self.resume_result


def make_settings(
    tmp_path: Path,
    slicer_enabled: bool = False,
    octoprint_enabled: bool = False,
    base_height_mm: float = 3.2,
) -> AppSettings:
    return AppSettings(
        base_dir=PROJECT_ROOT,
        host="127.0.0.1",
        port=8080,
        public_url=None,
        wifi_ssid="KeychainFair",
        wifi_password="fair2026",
        hotspot_gateway="192.168.137.1",
        admin_pin="9999",
        internal_api_token="test-internal-token",
        database_path=tmp_path / "orders.sqlite3",
        database_url=None,
        generated_dir=tmp_path / "generated",
        designs_dir=PROJECT_ROOT / "designs",
        queue=QueueSettings(max_items_per_plate=4, max_wait_minutes=10, bed_size_mm=(220, 220), item_spacing_mm=8),
        tools=ToolSettings(
            openscad_path="fake",
            openscad_timeout_seconds=1,
            cura_engine_path="fake",
            cura_timeout_seconds=1,
        ),
        slicer=SlicerSettings(enabled=slicer_enabled, profile_path=PROJECT_ROOT / "config" / "slicer.example.yaml"),
        model=ModelSettings(base_height_mm=base_height_mm),
        octoprint=OctoPrintSettings(
            enabled=octoprint_enabled,
            base_url="http://127.0.0.1:5000",
            api_key="fake-key" if octoprint_enabled else "",
            printer_port="COM8",
            baudrate=250000,
            printer_profile="_default",
            save_connection=True,
        ),
        printer_control=PrinterControlSettings(bed_preheat_c=60),
        worker_poll_seconds=3600,
    )


def make_temp_design_settings(
    tmp_path: Path,
    source_name: str = "classic_plate",
    base_height_mm: float = 3.2,
    source_names: list[str] | None = None,
) -> AppSettings:
    design_json_files = {
        "stacked_plate_classic": "01_rectangular_number",
        "square_plate": "02_square_number",
        "classic_plate": "03_custom_rectangular",
        "rounded_tag": "04_custom_oval",
    }
    designs_dir = tmp_path / "designs"
    static_dir = tmp_path / "static"
    designs_dir.mkdir()
    static_dir.mkdir()
    for name in (source_names or [source_name]):
        json_name = design_json_files.get(name, name)
        shutil.copy2(PROJECT_ROOT / "designs" / f"{json_name}.json", designs_dir / f"{json_name}.json")
        shutil.copy2(PROJECT_ROOT / "designs" / f"{name}.scad", designs_dir / f"{name}.scad")
    return replace(make_settings(tmp_path, base_height_mm=base_height_mm), base_dir=tmp_path, designs_dir=designs_dir)


def create_test_app(settings, *, generator=None, slicer=None, printer=None, notifier=None):
    return create_app(
        settings=settings,
        generator=generator or FakeGenerator(settings),
        slicer=slicer,
        printer=printer,
        notifier=notifier,
        start_worker=False,
    )


def ready_printer(settings):
    return FakePrinter(
        settings,
        AdapterResult(True, "fake job", extra={"state": "Operational", "progress": {}}),
        AdapterResult(
            True,
            "fake printer",
            extra={
                "state": {
                    "text": "Operational",
                    "flags": {"operational": True, "ready": True, "printing": False, "closedOrError": False, "error": False},
                }
            },
        ),
    )


def order_payload(
    *,
    customer_name: str = "Anna",
    car_number: str = "1234 AB-7",
    design_id: str = "classic_plate",
    size_id: str = "standard",
    elements: list[str] | None = None,
    print_line_1: str = "Hello",
    print_line_2: str = "",
    idempotency_key: str = "test-order-flow-1",
    client_id: str | None = None,
) -> dict:
    payload = {
        "customer_name": customer_name,
        "car_number": car_number,
        "design_id": design_id,
        "size_id": size_id,
        "elements": [] if elements is None else elements,
        "print_line_1": print_line_1,
        "print_line_2": print_line_2,
        "idempotency_key": idempotency_key,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    return payload


def create_order(client, **overrides) -> str:
    response = client.post("/api/orders", json=order_payload(**overrides))
    assert response.status_code == 201
    return response.json()["order"]["id"]


def create_paid_order(client, order_key="test-order-flow-1", **overrides) -> str:
    order_id = create_order(client, idempotency_key=order_key, **overrides)
    paid = client.post(f"/api/admin/orders/{order_id}/status", headers=ADMIN_HEADERS, json={"status": "paid"})
    assert paid.status_code == 200
    return order_id


def create_unpaid_order(client, order_key="test-unpaid-order") -> str:
    return create_order(client, idempotency_key=order_key)
