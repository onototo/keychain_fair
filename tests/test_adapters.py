from dataclasses import replace
from pathlib import Path
import subprocess
import struct

import yaml

from keychain_fair.adapters import (
    AdapterResult,
    CuraEngineSlicer,
    OctoPrintController,
    OpenScadModelGenerator,
    OverlaySafetySpec,
)
from keychain_fair.config import PROJECT_ROOT
from tests.support import make_settings


def write_triangle_stl(path, vertices):
    with path.open("wb") as handle:
        handle.write(b"test stl".ljust(80, b" "))
        handle.write(struct.pack("<I", 1))
        handle.write(struct.pack("<fff", 0.0, 0.0, 1.0))
        for vertex in vertices:
            handle.write(struct.pack("<fff", *vertex))
        handle.write(struct.pack("<H", 0))


def make_cura_settings(tmp_path, profile_lines, base_height_mm=3.2):
    fake_exe = tmp_path / "CuraEngine.exe"
    fake_exe.write_text("fake", encoding="utf-8")
    profile = tmp_path / "slicer.yaml"
    profile.write_text("\n".join(["cura_engine:", *profile_lines]), encoding="utf-8")
    settings = make_settings(tmp_path, slicer_enabled=True, base_height_mm=base_height_mm)
    return replace(
        settings,
        tools=replace(settings.tools, cura_engine_path=str(fake_exe)),
        slicer=replace(settings.slicer, profile_path=profile),
    )


def test_ender3_profile_uses_three_line_brim_without_switching_to_skirt():
    profile = yaml.safe_load((PROJECT_ROOT / "config" / "slicer.ender3.yaml").read_text(encoding="utf-8"))
    settings = profile["cura_engine"]["settings"]

    assert settings["adhesion_type"] == "brim"
    assert settings["brim_width"] == 1.2
    assert settings["skirt_line_count"] == 0
    assert settings["material_print_temperature"] == 205
    assert settings["material_print_temperature_layer_0"] == 205


def test_cura_slicer_uses_profile_and_repairs_header(tmp_path, monkeypatch):
    machine_json = tmp_path / "fdmprinter.def.json"
    extruder_json = tmp_path / "fdmextruder.def.json"
    machine_json.write_text("{}", encoding="utf-8")
    extruder_json.write_text("{}", encoding="utf-8")
    settings = make_cura_settings(
        tmp_path,
        [
            f"  search_path: {tmp_path.as_posix()}",
            f"  settings_json_path: {machine_json.as_posix()}",
            f"  extruder_json_path: {extruder_json.as_posix()}",
            "  settings:",
            "    machine_width: 220",
            "    machine_start_gcode: |",
            "      M140 S60",
            "      G28",
            "  extruder_settings:",
            "    machine_nozzle_size: 0.4",
        ],
    )
    plate = tmp_path / "plate.stl"
    plate.write_text("solid plate\nendsolid plate\n", encoding="utf-8")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            "\n".join([";FLAVOR:Marlin", ";TIME:9999", ";Filament used: 0m", "", "G1 X0 Y0 E0.1"]),
            encoding="utf-8",
        )
        stdout = "\n".join(["[info] Gcode header after slicing: ;FLAVOR:Marlin", ";TIME:12", ";Filament used: 1m", ""])
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("keychain_fair.adapters.subprocess.run", fake_run)

    result = CuraEngineSlicer(settings).slice_plate(plate, "batch_profile")

    assert result.success is True
    command = seen["command"]
    assert command[:4] == [settings.tools.cura_engine_path, "slice", "-j", str(machine_json)]
    assert "-e0" in command
    assert "machine_width=220" in command
    assert "machine_nozzle_size=0.4" in command
    assert seen["env"]["CURA_ENGINE_SEARCH_PATH"] == str(tmp_path)
    gcode_lines = Path(result.output_path).read_text(encoding="utf-8").splitlines()
    assert ";TIME:12" in gcode_lines[:4]
    assert ";TIME:9999" not in gcode_lines[:4]


def test_cura_slicer_rejects_gcode_without_extrusion(tmp_path, monkeypatch):
    machine_json = tmp_path / "fdmprinter.def.json"
    machine_json.write_text("{}", encoding="utf-8")
    settings = make_cura_settings(
        tmp_path,
        [f"  settings_json_path: {machine_json.as_posix()}", "  settings:", "    machine_width: 220"],
    )
    plate = tmp_path / "plate.stl"
    plate.write_text("solid plate\nendsolid plate\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text(";FLAVOR:Marlin\nG0 X0 Y0\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("keychain_fair.adapters.subprocess.run", fake_run)

    result = CuraEngineSlicer(settings).slice_plate(plate, "batch_empty")

    assert result.success is False
    assert "without extrusion" in result.message


def test_cura_slicer_inserts_filament_change_pause_at_base_height(tmp_path, monkeypatch):
    machine_json = tmp_path / "fdmprinter.def.json"
    machine_json.write_text("{}", encoding="utf-8")
    settings = make_cura_settings(
        tmp_path,
        [f"  settings_json_path: {machine_json.as_posix()}", "  settings:", "    machine_width: 220"],
        base_height_mm=1.9,
    )
    plate = tmp_path / "plate.stl"
    plate.write_text("solid plate\nendsolid plate\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            "\n".join(
                [
                    ";FLAVOR:Marlin",
                    ";LAYER:8",
                    "G0 X0 Y0 Z1.9",
                    "G1 X1 Y1 E1",
                    ";MESH:NONMESH",
                    "G0 X0 Y0 Z2.1",
                    "G0 X2 Y2",
                    ";TIME_ELAPSED:12.3",
                    ";LAYER:9",
                    "G1 X1 Y1 E2",
                ]
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("keychain_fair.adapters.subprocess.run", fake_run)

    result = CuraEngineSlicer(settings).slice_plate(plate, "batch_filament_pause")

    assert result.success is True
    lines = Path(result.output_path).read_text(encoding="utf-8").splitlines()
    pause_index = lines.index("@pause Change filament at Z=1.90 mm")
    assert lines.index("G1 X1 Y1 E1") < pause_index < lines.index("G0 X0 Y0 Z2.1")
    pause_block = lines[pause_index - 8 : pause_index + 7]
    assert "G91 ; relative positioning for filament-change lift" in pause_block
    assert "M83 ; relative extrusion for filament-change retract" in pause_block
    assert "G1 E-40 F3000 ; retract before lift" in pause_block
    assert "G1 Z100 F3000 ; lift for filament change" in pause_block
    assert "M17 X Y Z ; keep motion axes locked during filament change" in pause_block
    assert "M84 S0 ; disable stepper idle timeout during filament change" in pause_block
    assert "G1 Z-100 F3000 ; return to print height" in pause_block
    assert "G90" in pause_block
    assert "M82" in pause_block
    assert "G92 E1.00000 ; keep slicer E position after manual retract" in pause_block
    assert not any("E40" in line for line in pause_block)


def test_cura_slicer_uses_explicit_filament_change_height(tmp_path, monkeypatch):
    machine_json = tmp_path / "fdmprinter.def.json"
    machine_json.write_text("{}", encoding="utf-8")
    settings = make_cura_settings(
        tmp_path,
        [f"  settings_json_path: {machine_json.as_posix()}", "  settings:", "    machine_width: 220"],
        base_height_mm=1.9,
    )
    plate = tmp_path / "plate.stl"
    plate.write_text("solid plate\nendsolid plate\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            "\n".join(
                [
                    ";FLAVOR:Marlin",
                    ";LAYER:8",
                    "G0 X0 Y0 Z2.1",
                    "G1 X1 Y1 E1",
                    ";LAYER:9",
                    "G0 X0 Y0 Z2.3",
                    "G1 X2 Y2 E2",
                    ";LAYER:10",
                    "G0 X0 Y0 Z4.7",
                    "G1 X3 Y3 E3",
                    ";LAYER:11",
                    "G0 X0 Y0 Z4.9",
                    "G1 X4 Y4 E4",
                ]
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("keychain_fair.adapters.subprocess.run", fake_run)

    result = CuraEngineSlicer(settings).slice_plate(plate, "batch_explicit_pause", filament_change_height_mm=4.7)

    assert result.success is True
    lines = Path(result.output_path).read_text(encoding="utf-8").splitlines()
    pause_index = lines.index("@pause Change filament at Z=4.70 mm")
    assert lines.index("G1 X3 Y3 E3") < pause_index < lines.index("G0 X0 Y0 Z4.9")
    assert "@pause Change filament at Z=1.90 mm" not in lines


def test_overlay_postprocess_removes_startup_prime_and_keeps_safe_clearance(tmp_path):
    safety = OverlaySafetySpec(
        base_thickness_mm=2.1,
        relief_height_mm=0.8,
        safety_clearance_mm=2.0,
        slot_x_mm=8.0,
        slot_y_mm=8.0,
        slot_width_mm=64.0,
        slot_height_mm=30.0,
        bed_size_mm=(220.0, 220.0),
    )
    gcode = tmp_path / "overlay.gcode"
    gcode.write_text(
        "\n".join(
            [
                "M82",
                "G28",
                "G0 Z0.2",
                "G1 X180 Y0 E3.0 ; prime line",
                "G0 X20 Y20",
                "G1 X30 Y20 E4.0",
                "M104 S0",
                "M140 S0",
                "M84",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    slicer = CuraEngineSlicer(make_settings(tmp_path))
    slicer._postprocess_overlay_gcode(gcode, safety)
    text = gcode.read_text(encoding="utf-8")
    analysis = slicer.analyze_overlay_gcode(gcode, safety)

    assert analysis.success is True
    assert "prime line" not in "\n".join(line for line in text.splitlines() if not line.startswith("; skipped"))
    assert "M104 S0" not in text
    assert "M140 S0" not in text
    assert "M84" not in text
    assert "G28" not in "\n".join(line for line in text.splitlines() if not line.startswith("; skipped"))
    assert "G0 X20 Y20 Z4.9" in text


def test_overlay_collision_analyzer_rejects_low_travel_and_out_of_slot_extrusion(tmp_path):
    safety = OverlaySafetySpec(
        base_thickness_mm=2.1,
        relief_height_mm=0.8,
        safety_clearance_mm=2.0,
        slot_x_mm=8.0,
        slot_y_mm=8.0,
        slot_width_mm=64.0,
        slot_height_mm=30.0,
        bed_size_mm=(220.0, 220.0),
    )
    gcode = tmp_path / "unsafe_overlay.gcode"
    gcode.write_text(
        "\n".join(
            [
                "M82",
                "M104 S0",
                "G0 Z2.3",
                "G0 X80 Y20",
                "G1 X80 Y20 Z1.9 E1.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = CuraEngineSlicer(make_settings(tmp_path)).analyze_overlay_gcode(gcode, safety)
    failures = " | ".join(result.extra["failures"])

    assert result.success is False
    assert "hotend shutdown" in failures
    assert "travel move below safe Z" in failures
    assert "extrusion below blank surface" in failures
    assert "extrusion outside slot bounds" in failures


def test_openscad_model_scale_keeps_z_thickness(tmp_path, monkeypatch):
    fake_exe = tmp_path / "openscad.exe"
    fake_exe.write_text("fake", encoding="utf-8")
    settings = make_settings(tmp_path)
    settings = replace(settings, tools=replace(settings.tools, openscad_path=str(fake_exe)))
    design = {
        "template_path": str(PROJECT_ROOT / "designs" / "stacked_plate_classic.scad"),
        "sizes": [{"id": "large", "model_scale": 0.9}],
    }
    order = {
        "id": "ord_scaled",
        "size_id": "large",
        "customer_name": "Nikita",
        "car_number": "A123BC77",
        "selected_elements": [{"id": "car", "shape": "car"}],
        "width_mm": 79.2,
        "height_mm": 18.0,
        "thickness_mm": 3.2,
        "font_size_mm": 7.74,
    }

    def fake_run(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text("solid fake\nendsolid fake\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("keychain_fair.adapters.subprocess.run", fake_run)

    result = OpenScadModelGenerator(settings).generate_order_stl(order, design)

    assert result.success is True
    wrapper_text = (settings.generated_dir / "orders" / "ord_scaled" / "ord_scaled.scad").read_text(encoding="utf-8")
    assert "scale([0.9, 0.9, 1]) keychain(" in wrapper_text
    assert "    88.0," in wrapper_text
    assert "    20.0," in wrapper_text
    assert "    3.2," in wrapper_text
    assert "    8.6" in wrapper_text


def test_batch_stl_merge_translates_order_meshes_without_openscad(tmp_path):
    settings = make_settings(tmp_path)
    first_stl = tmp_path / "first.stl"
    second_stl = tmp_path / "second.stl"
    write_triangle_stl(first_stl, [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
    write_triangle_stl(second_stl, [(-5.0, -2.0, 0.0), (2.0, -2.0, 0.0), (-5.0, 2.0, 0.0)])

    result = OpenScadModelGenerator(settings).combine_batch_stl(
        "batch_merge",
        [
            {"order": {"stl_path": str(first_stl)}, "x_mm": 8.0, "y_mm": 8.0},
            {"order": {"stl_path": str(second_stl)}, "x_mm": 20.0, "y_mm": 30.0},
        ],
    )

    data = Path(result.output_path).read_bytes()
    assert result.success is True
    assert struct.unpack("<I", data[80:84])[0] == 2
    assert struct.unpack("<fff", data[84 + 50 + 12 : 84 + 50 + 24]) == (-90.0, -80.0, 0.0)


def test_octoprint_connect_uses_configured_payload(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    controller = OctoPrintController(settings)
    posted = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        posted.append((url, kwargs))
        return Response()

    states = ["Closed", "Operational"]

    def fake_get_connection():
        state = states.pop(0)
        return AdapterResult(True, "fake connection", extra={"current": {"state": state}})

    monkeypatch.setattr(controller, "ensure_server_running", lambda: AdapterResult(True, "fake server"))
    monkeypatch.setattr(controller, "get_connection", fake_get_connection)
    monkeypatch.setattr("keychain_fair.adapters.httpx.post", fake_post)

    result = controller.connect()

    assert result.success is True
    assert posted == [
        (
            f"{settings.octoprint.base_url}/api/connection",
            {
                "headers": {"X-Api-Key": "fake-key", "Content-Type": "application/json"},
                "json": {
                    "command": "connect",
                    "port": "COM8",
                    "baudrate": 250000,
                    "printerProfile": "_default",
                    "save": True,
                },
                "timeout": 5,
            },
        )
    ]


def test_octoprint_connect_uses_detected_port_when_configured_port_is_unavailable(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    controller = OctoPrintController(settings)
    posted = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        posted.append((url, kwargs))
        return Response()

    states = [
        {
            "current": {"state": "Closed"},
            "options": {
                "ports": ["AUTO", "COM5"],
                "baudrates": [0, 115200, 250000],
                "printerProfiles": [{"id": "_default", "name": "Default"}],
            },
        },
        {"current": {"state": "Operational"}},
    ]

    def fake_get_connection():
        return AdapterResult(True, "fake connection", extra=states.pop(0))

    monkeypatch.setattr(controller, "ensure_server_running", lambda: AdapterResult(True, "fake server"))
    monkeypatch.setattr(controller, "get_connection", fake_get_connection)
    monkeypatch.setattr("keychain_fair.adapters.httpx.post", fake_post)

    result = controller.connect()

    assert result.success is True
    assert posted[0][1]["json"]["port"] == "COM5"
    assert posted[0][1]["json"]["baudrate"] == 250000


def test_octoprint_connect_skips_post_when_already_connected(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    controller = OctoPrintController(settings)

    def fail_post(*args, **kwargs):
        raise AssertionError("already connected printer must not reconnect")

    monkeypatch.setattr(controller, "ensure_server_running", lambda: AdapterResult(True, "fake server"))
    monkeypatch.setattr(
        controller,
        "get_connection",
        lambda: AdapterResult(True, "fake connection", extra={"current": {"state": "Operational"}}),
    )
    monkeypatch.setattr("keychain_fair.adapters.httpx.post", fail_post)

    result = controller.connect()

    assert result.success is True
    assert result.message == "OctoPrint connected: Operational"


def test_octoprint_ensure_connected_reports_closed_without_connect_request(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    controller = OctoPrintController(settings)

    def fail_post(*args, **kwargs):
        raise AssertionError("ensure_connected must not request OctoPrint connect")

    monkeypatch.setattr(controller, "ensure_server_running", lambda: AdapterResult(True, "fake server"))
    monkeypatch.setattr(
        controller,
        "get_connection",
        lambda: AdapterResult(True, "fake connection", extra={"current": {"state": "Closed"}}),
    )
    monkeypatch.setattr("keychain_fair.adapters.httpx.post", fail_post)

    result = controller.ensure_connected(settle_seconds=0.01)

    assert result.success is False
    assert result.message == "OctoPrint state is Closed"


def test_octoprint_ensure_connected_waits_for_transient_state(tmp_path):
    settings = make_settings(tmp_path, octoprint_enabled=True)

    class TransientPrinter(OctoPrintController):
        def __init__(self):
            super().__init__(settings)
            self.states = ["Opening serial connection", "Operational"]

        def ensure_server_running(self, wait_seconds=25.0):
            return AdapterResult(True, "fake server")

        def get_connection(self):
            state = self.states.pop(0)
            return AdapterResult(True, "fake connection", extra={"current": {"state": state}})

    controller = TransientPrinter()

    result = controller.ensure_connected(settle_seconds=0.01)

    assert result.success is True


def test_octoprint_server_autostart_for_local_base_url(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    settings = replace(settings, base_dir=tmp_path)
    fake_octoprint = tmp_path / ".octoprint-venv" / "Scripts" / "octoprint.exe"
    fake_octoprint.parent.mkdir(parents=True)
    fake_octoprint.write_text("fake", encoding="utf-8")
    controller = OctoPrintController(settings)
    reachable = {"value": False}
    starts = []

    def fake_reachable():
        return reachable["value"]

    def fake_popen(*args, **kwargs):
        starts.append((args, kwargs))
        reachable["value"] = True
        return object()

    monkeypatch.setattr(controller, "_octoprint_reachable", fake_reachable)
    monkeypatch.setattr("keychain_fair.adapters.subprocess.Popen", fake_popen)

    result = controller.ensure_server_running(wait_seconds=1)

    assert result.success is True
    assert starts
    command = starts[0][0][0]
    assert Path(command[0]).name == "octoprint.exe"
    assert command[1] == "serve"


def test_octoprint_reachable_uses_api_version_and_accepts_auth_status(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    controller = OctoPrintController(settings)
    calls = []

    class Response:
        status_code = 403

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("keychain_fair.adapters.httpx.get", fake_get)

    assert controller._octoprint_reachable() is True
    assert calls == [(f"{settings.octoprint.base_url}/api/version", {"timeout": 1, "follow_redirects": True})]


def test_octoprint_reachable_rejects_missing_api(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    controller = OctoPrintController(settings)

    class Response:
        status_code = 404

    monkeypatch.setattr("keychain_fair.adapters.httpx.get", lambda *args, **kwargs: Response())

    assert controller._octoprint_reachable() is False
