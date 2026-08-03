from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from .config import AppSettings
from .editor import (
    CUSTOM_TEXT_LINE_GAP_FACTOR,
    CUSTOM_TEXT_MAX_FONT_SIZE_MM,
    CUSTOM_TEXT_MIN_FONT_SIZE_MM,
    editor_params_to_scad_array,
    order_with_fitted_custom_text,
)


FILAMENT_CHANGE_MARKER = "KEYCHAIN_FAIR_FILAMENT_CHANGE"
FILAMENT_CHANGE_RETRACT_MM = 40.0
FILAMENT_CHANGE_Z_LIFT_MM = 100.0
FILAMENT_CHANGE_MOVE_FEEDRATE = 3000
Z_MOVE_RE = re.compile(r"^(?:G0|G1)\b", re.IGNORECASE)
Z_VALUE_RE = re.compile(r"(?:^|\s)Z(-?\d+(?:\.\d+)?)", re.IGNORECASE)
E_VALUE_RE = re.compile(r"(?:^|\s)E(-?\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class AdapterResult:
    success: bool
    message: str
    output_path: Path | None = None
    extra: dict[str, Any] | None = None


def _resolve_executable(command: str) -> str | None:
    path = Path(command)
    if path.is_absolute() or "\\" in command or "/" in command:
        return str(path) if path.exists() else None
    return shutil.which(command)


def _scad_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _scad_string_array(values: list[str]) -> str:
    return "[" + ", ".join(_scad_string(value) for value in values) + "]"


def _size_model_scale(order: dict[str, Any], design: dict[str, Any]) -> float:
    size = next((item for item in design.get("sizes", []) if item.get("id") == order.get("size_id")), None)
    if not size:
        return 1.0
    return float(size.get("legacy_model_scale", size.get("model_scale", 1.0)))


def _order_model_params(order: dict[str, Any]) -> dict[str, Any] | None:
    params = order.get("model_params")
    if isinstance(params, dict):
        return params
    if isinstance(params, str) and params.strip():
        try:
            decoded = json.loads(params)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _print_lines(order: dict[str, Any]) -> tuple[str, str]:
    first = str(order.get("print_line_1") or "").strip()
    second = str(order.get("print_line_2") or "").strip()
    if first or second:
        return first, second
    return str(order.get("car_number") or ""), ""


def _font_size_from_params(params: dict[str, Any], fallback: float) -> float:
    text_blocks = params.get("text_blocks") if isinstance(params.get("text_blocks"), dict) else {}
    car_block = text_blocks.get("car") if isinstance(text_blocks.get("car"), dict) else {}
    try:
        return float(car_block.get("font_size_mm", fallback))
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    number = _safe_float(value, fallback)
    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _two_line_space_candidates(full_text: str) -> list[list[str]]:
    cleaned = _clean_text(full_text)
    if len(cleaned) <= 1:
        return [[cleaned]]

    candidates: list[list[str]] = [[cleaned]]
    for index, char in enumerate(cleaned):
        if char != " ":
            continue
        first = _clean_text(cleaned[:index])
        second = _clean_text(cleaned[index + 1 :])
        if first and second:
            candidates.append([first, second])
    return candidates


def _binary_stl_triangle_count(data: bytes) -> int | None:
    if len(data) < 84:
        return None
    count = struct.unpack("<I", data[80:84])[0]
    return count if 84 + count * 50 == len(data) else None


def _read_binary_stl_triangles(data: bytes) -> list[tuple[tuple[float, float, float], list[tuple[float, float, float]]]]:
    count = struct.unpack("<I", data[80:84])[0]
    triangles = []
    offset = 84
    for _ in range(count):
        normal = struct.unpack("<fff", data[offset : offset + 12])
        offset += 12
        vertices = []
        for _vertex_index in range(3):
            vertices.append(struct.unpack("<fff", data[offset : offset + 12]))
            offset += 12
        offset += 2
        triangles.append((normal, vertices))
    return triangles


def _read_ascii_stl_triangles(text: str) -> list[tuple[tuple[float, float, float], list[tuple[float, float, float]]]]:
    triangles = []
    normal = (0.0, 0.0, 0.0)
    vertices: list[tuple[float, float, float]] = []

    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if len(parts) == 5 and parts[0].lower() == "facet" and parts[1].lower() == "normal":
            try:
                normal = (float(parts[2]), float(parts[3]), float(parts[4]))
            except ValueError:
                normal = (0.0, 0.0, 0.0)
            vertices = []
        elif len(parts) == 4 and parts[0].lower() == "vertex":
            try:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                vertices = []
        elif parts and parts[0].lower() == "endfacet" and len(vertices) == 3:
            triangles.append((normal, vertices))
            vertices = []

    return triangles


def _read_stl_triangles(path: Path) -> list[tuple[tuple[float, float, float], list[tuple[float, float, float]]]]:
    data = path.read_bytes()
    if _binary_stl_triangle_count(data) is not None:
        return _read_binary_stl_triangles(data)
    return _read_ascii_stl_triangles(data.decode("utf-8", errors="ignore"))


def stl_bounds(path: Path) -> dict[str, float]:
    triangles = _read_stl_triangles(path)
    points = [vertex for _normal, vertices in triangles for vertex in vertices]
    if not points:
        raise ValueError("STL has no vertices")

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    min_x = min(xs)
    min_y = min(ys)
    min_z = min(zs)
    max_x = max(xs)
    max_y = max(ys)
    max_z = max(zs)
    return {
        "min_x_mm": min_x,
        "min_y_mm": min_y,
        "min_z_mm": min_z,
        "max_x_mm": max_x,
        "max_y_mm": max_y,
        "max_z_mm": max_z,
        "width_mm": max_x - min_x,
        "height_mm": max_y - min_y,
        "depth_mm": max_z - min_z,
    }


def _write_binary_stl(
    path: Path,
    triangles: list[tuple[tuple[float, float, float], list[tuple[float, float, float]]]],
) -> None:
    header = b"KeychainFair batch STL".ljust(80, b" ")
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(struct.pack("<I", len(triangles)))
        for normal, vertices in triangles:
            handle.write(struct.pack("<fff", *normal))
            for vertex in vertices:
                handle.write(struct.pack("<fff", *vertex))
            handle.write(struct.pack("<H", 0))


class OpenScadModelGenerator:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._text_metric_cache: dict[str, tuple[float, float]] = {}
        self._text_metric_lock = threading.Lock()

    def check_ready(self) -> AdapterResult:
        executable = _resolve_executable(self.settings.tools.openscad_path)
        if not executable:
            return AdapterResult(
                success=False,
                message=(
                    "OpenSCAD РЅРµ РЅР°Р№РґРµРЅ. РЈСЃС‚Р°РЅРѕРІРёС‚Рµ OpenSCAD РёР»Рё Р·Р°РґР°Р№С‚Рµ "
                    "external_tools.openscad_path РІ config/app.yaml."
                ),
            )
        return AdapterResult(success=True, message="OpenSCAD ready", output_path=Path(executable))

    def generate_order_stl(self, order: dict[str, Any], design: dict[str, Any]) -> AdapterResult:
        ready = self.check_ready()
        if not ready.success:
            return ready

        order_dir = self.settings.generated_dir / "orders" / order["id"]
        return self._generate_stl(ready, order, design, order_dir, order["id"], "STL created")

    def generate_preview_stl(
        self,
        preview_id: str,
        order: dict[str, Any],
        design: dict[str, Any],
    ) -> AdapterResult:
        ready = self.check_ready()
        if not ready.success:
            return ready

        preview_dir = self.settings.generated_dir / "editor" / "previews" / preview_id
        return self._generate_stl(ready, order, design, preview_dir, preview_id, "Preview STL created")

    def render_order_preview_png(
        self,
        cache_key: str,
        order: dict[str, Any],
        design: dict[str, Any],
        image_size: tuple[int, int] = (480, 320),
    ) -> AdapterResult:
        ready = self.check_ready()
        if not ready.success:
            return ready

        safe_key = "".join(char for char in cache_key.lower() if char in "abcdefghijklmnopqrstuvwxyz0123456789_-")[:80]
        if not safe_key:
            return AdapterResult(False, "Preview cache key is empty")

        preview_dir = self.settings.generated_dir / "telegram" / "previews" / safe_key
        preview_dir.mkdir(parents=True, exist_ok=True)
        png_path = preview_dir / "preview.png"
        wrapper_path = preview_dir / "preview.scad"
        if png_path.exists():
            return AdapterResult(True, "Preview PNG cached", png_path, {"wrapper_scad_path": str(wrapper_path), "cached": True})

        prepared = self._write_order_wrapper_scad(ready, order, design, preview_dir, "preview")
        command = self._with_virtual_display([
            str(ready.output_path),
            "-o",
            str(png_path),
            "--imgsize",
            f"{int(image_size[0])},{int(image_size[1])}",
            "--viewall",
            "--autocenter",
            "--projection",
            "perspective",
            str(prepared["wrapper_path"]),
        ])
        result = self._run_command(command, png_path, "Preview PNG created", "OpenSCAD PNG preview failed")
        if result.extra is None:
            result = AdapterResult(result.success, result.message, result.output_path, {})
        result.extra["wrapper_scad_path"] = str(prepared["wrapper_path"])
        if prepared["text_layout"] is not None:
            result.extra["text_layout"] = prepared["text_layout"]
        return result

    def _generate_stl(
        self,
        ready: AdapterResult,
        order: dict[str, Any],
        design: dict[str, Any],
        target_dir: Path,
        file_stem: str,
        success_message: str,
    ) -> AdapterResult:
        target_dir.mkdir(parents=True, exist_ok=True)
        stl_path = target_dir / f"{file_stem}.stl"
        prepared = self._write_order_wrapper_scad(ready, order, design, target_dir, file_stem)
        wrapper_path = prepared["wrapper_path"]
        text_layout = prepared["text_layout"]
        command = [
            str(ready.output_path),
            "--export-format",
            "binstl",
            "-o",
            str(stl_path),
            str(wrapper_path),
        ]
        result = self._run_command(command, stl_path, success_message, "OpenSCAD STL generation failed")
        if result.extra is None:
            result = AdapterResult(result.success, result.message, result.output_path, {})
        result.extra["wrapper_scad_path"] = str(wrapper_path)
        if text_layout is not None:
            result.extra["text_layout"] = text_layout
        return result

    def _write_order_wrapper_scad(
        self,
        ready: AdapterResult,
        order: dict[str, Any],
        design: dict[str, Any],
        target_dir: Path,
        file_stem: str,
    ) -> dict[str, Any]:
        target_dir.mkdir(parents=True, exist_ok=True)
        wrapper_path = target_dir / f"{file_stem}.scad"
        original_order = order
        order, text_layout = order_with_fitted_custom_text(order, design)
        exact_order, exact_layout = self._order_with_exact_custom_text_fit(original_order, design, ready.output_path, target_dir)
        if exact_layout is not None:
            order = exact_order
            text_layout = exact_layout
        element_shapes = [item.get("shape", item["id"]) for item in order["selected_elements"]]
        if design.get("print_mode") == "by_number_single" and not element_shapes:
            element_shapes = ["car"]
        print_line_1, print_line_2 = _print_lines(order)
        template_path = Path(design["template_path"]).resolve()
        model_params = _order_model_params(order)
        if model_params:
            model_scale = 1.0
            width_mm = float(order["width_mm"])
            height_mm = float(order["height_mm"])
            thickness_mm = float(model_params.get("thickness_mm", order["thickness_mm"]))
            font_size_mm = _font_size_from_params(model_params, float(order["font_size_mm"]))
            editor_arg = f",\n    {editor_params_to_scad_array(model_params)}"
        else:
            model_scale = _size_model_scale(order, design)
            width_mm = float(order["width_mm"]) / model_scale
            height_mm = float(order["height_mm"]) / model_scale
            thickness_mm = float(order["thickness_mm"])
            font_size_mm = float(order["font_size_mm"]) / model_scale
            editor_arg = ""
        call_prefix = f"scale([{model_scale}, {model_scale}, 1]) " if model_scale != 1.0 else ""
        wrapper_path.write_text(
            "\n".join(
                [
                    f'use <{template_path.as_posix()}>',
                    f"{call_prefix}keychain(",
                    f"    {_scad_string(order['customer_name'])},",
                    f"    {_scad_string(print_line_1)},",
                    f"    {_scad_string(print_line_2)},",
                    f"    {_scad_string_array(element_shapes)},",
                    f"    {width_mm},",
                    f"    {height_mm},",
                    f"    {thickness_mm},",
                    f"    {font_size_mm}{editor_arg}",
                    ");",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return {"wrapper_path": wrapper_path, "text_layout": text_layout}

    def _with_virtual_display(self, command: list[str]) -> list[str]:
        if os.environ.get("DISPLAY"):
            return command
        xvfb_run = shutil.which("xvfb-run")
        if not xvfb_run:
            return command
        return [xvfb_run, "-a", *command]

    def _order_with_exact_custom_text_fit(
        self,
        order: dict[str, Any],
        design: dict[str, Any],
        openscad_path: Path,
        target_dir: Path,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if design.get("print_mode") != "custom_text":
            return order, None
        params = _order_model_params(order)
        if not isinstance(params, dict):
            return order, None

        text_blocks = params.get("text_blocks") if isinstance(params.get("text_blocks"), dict) else {}
        block = text_blocks.get("car") if isinstance(text_blocks.get("car"), dict) else {}
        box_width = _safe_float(block.get("box_width_mm"), _safe_float(params.get("base_width_mm"), 60.0) - 22.0)
        box_height = _safe_float(block.get("box_height_mm"), _safe_float(params.get("base_height_mm"), 24.0) * 0.48)
        line_spacing = _clamp_float(params.get("line_spacing"), CUSTOM_TEXT_LINE_GAP_FACTOR, 0.7, 1.8)
        full_text = _clean_text(" ".join(item for item in _print_lines(order) if item))
        if not full_text:
            return order, None

        try:
            layout = self._exact_custom_text_layout(
                full_text,
                max(1.0, box_width),
                max(1.0, box_height),
                line_spacing,
                openscad_path,
                target_dir / "_text_metrics",
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return order, None

        fitted_order = deepcopy(order)
        fitted_params = deepcopy(params)
        fitted_params.setdefault("text_blocks", {}).setdefault("car", {})["font_size_mm"] = layout["font_size_mm"]
        fitted_params["text_exact_fit"] = 1
        fitted_order["model_params"] = fitted_params
        fitted_order["print_line_1"] = layout["lines"][0]
        fitted_order["print_line_2"] = layout["lines"][1]
        fitted_order["font_size_mm"] = layout["font_size_mm"]
        layout["box_width_mm"] = round(max(1.0, box_width), 3)
        layout["box_height_mm"] = round(max(1.0, box_height), 3)
        return fitted_order, layout

    def _exact_custom_text_layout(
        self,
        full_text: str,
        box_width: float,
        box_height: float,
        line_spacing: float,
        openscad_path: Path,
        metrics_dir: Path,
    ) -> dict[str, Any]:
        one_line = [full_text]
        one_size = self._exact_font_size(one_line, box_width, box_height, line_spacing, openscad_path, metrics_dir)
        if one_size >= CUSTOM_TEXT_MIN_FONT_SIZE_MM:
            lines = [full_text, ""]
            font_size = one_size
        else:
            candidates = _two_line_space_candidates(full_text)
            if len(candidates) == 1 and candidates[0] == one_line:
                lines = [full_text, ""]
                font_size = one_size
            else:
                best = max(
                    candidates,
                    key=lambda item: self._exact_font_size(item, box_width, box_height, line_spacing, openscad_path, metrics_dir),
                )
                lines = [best[0], best[1] if len(best) > 1 else ""]
                font_size = self._exact_font_size(best, box_width, box_height, line_spacing, openscad_path, metrics_dir)

        return {
            "lines": lines,
            "font_size_mm": round(max(0.1, min(font_size, CUSTOM_TEXT_MAX_FONT_SIZE_MM)), 3),
            "fits": font_size >= CUSTOM_TEXT_MIN_FONT_SIZE_MM,
            "min_font_size_mm": round(CUSTOM_TEXT_MIN_FONT_SIZE_MM, 3),
            "line_spacing": round(line_spacing, 3),
            "fit_source": "openscad_bounds",
        }

    def _exact_font_size(
        self,
        lines: list[str],
        box_width: float,
        box_height: float,
        line_spacing: float,
        openscad_path: Path,
        metrics_dir: Path,
    ) -> float:
        cleaned = [_clean_text(line) for line in lines if _clean_text(line)]
        if not cleaned:
            return CUSTOM_TEXT_MIN_FONT_SIZE_MM

        metrics = [self._text_metric(line, openscad_path, metrics_dir) for line in cleaned]
        width_size = min(box_width / max(width, 0.001) for width, _height in metrics)
        if len(metrics) == 1:
            height_units = max(metrics[0][1], 0.001)
        else:
            height_units = line_spacing + (metrics[0][1] + metrics[1][1]) / 2
        height_size = box_height / max(height_units, 0.001)
        return max(0.1, min(width_size, height_size, CUSTOM_TEXT_MAX_FONT_SIZE_MM))

    def _text_metric(self, text: str, openscad_path: Path, metrics_dir: Path) -> tuple[float, float]:
        key = f"Liberation Sans:style=Bold\x00{text}"
        with self._text_metric_lock:
            cached = self._text_metric_cache.get(key)
        if cached is not None:
            return cached

        metrics_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        scad_path = metrics_dir / f"text_{digest}.scad"
        stl_path = metrics_dir / f"text_{digest}.stl"
        probe_size = 10.0
        scad_path.write_text(
            "\n".join(
                [
                    "linear_extrude(height = 0.1)",
                    f"    text({_scad_string(text)}, size = {probe_size}, halign = \"center\", valign = \"center\", font = \"Liberation Sans:style=Bold\");",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        command = [str(openscad_path), "--export-format", "binstl", "-o", str(stl_path), str(scad_path)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0 or not stl_path.exists():
            raise subprocess.CalledProcessError(completed.returncode, command, completed.stdout, completed.stderr)

        bounds = stl_bounds(stl_path)
        metric = (max(bounds["width_mm"] / probe_size, 0.001), max(bounds["height_mm"] / probe_size, 0.001))
        with self._text_metric_lock:
            self._text_metric_cache[key] = metric
        return metric

    def combine_batch_stl(self, batch_id: str, layout_items: list[dict[str, Any]]) -> AdapterResult:
        batch_dir = self.settings.generated_dir / "batches" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        plate_scad_path = batch_dir / f"{batch_id}_plate.scad"
        plate_stl_path = batch_dir / f"{batch_id}_plate.stl"

        lines = ["// Reference only. The batch STL is merged directly to avoid CGAL boolean failures."]
        triangles: list[tuple[tuple[float, float, float], list[tuple[float, float, float]]]] = []
        bed_width, bed_height = self.settings.queue.bed_size_mm
        for item in layout_items:
            order = item["order"]
            stl_path = Path(order["stl_path"]).resolve()
            physical_x_mm = float(item["x_mm"])
            physical_y_mm = float(item["y_mm"])
            if not stl_path.exists():
                return AdapterResult(False, f"Order STL not found: {stl_path}")
            try:
                source_triangles = _read_stl_triangles(stl_path)
                bounds = stl_bounds(stl_path)
                x_offset = physical_x_mm - bounds["min_x_mm"] - bed_width / 2
                y_offset = physical_y_mm - bounds["min_y_mm"] - bed_height / 2
                lines.append(f"translate([{x_offset}, {y_offset}, 0]) import({_scad_string(stl_path.as_posix())});")
                for normal, vertices in source_triangles:
                    translated = [(x + x_offset, y + y_offset, z) for x, y, z in vertices]
                    triangles.append((normal, translated))
            except (OSError, ValueError, struct.error) as exc:
                return AdapterResult(False, f"STL merge failed for {stl_path}: {exc}")
        lines.append("")
        plate_scad_path.write_text("\n".join(lines), encoding="utf-8")

        if not triangles:
            return AdapterResult(False, "Batch STL merge failed: no triangles found")

        _write_binary_stl(plate_stl_path, triangles)
        return AdapterResult(True, "Batch STL created", plate_stl_path, {"plate_scad_path": str(plate_scad_path)})

    def _run_command(self, command: list[str], output_path: Path, success_message: str, error_prefix: str) -> AdapterResult:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.tools.openscad_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return AdapterResult(False, f"{error_prefix}: timeout after {self.settings.tools.openscad_timeout_seconds}s")

        if completed.returncode != 0 or not output_path.exists():
            stderr = completed.stderr.strip() or completed.stdout.strip() or "no output"
            return AdapterResult(False, f"{error_prefix}: {stderr[:1000]}")

        return AdapterResult(True, success_message, output_path)


class CuraEngineSlicer:
    def __init__(self, settings: AppSettings):
        self.settings = settings

    def slice_plate(
        self,
        plate_stl_path: Path,
        batch_id: str,
        filament_change_height_mm: float | None = None,
    ) -> AdapterResult:
        if not self.settings.slicer.enabled:
            return AdapterResult(False, "Slicing РІС‹РєР»СЋС‡РµРЅ РІ config/app.yaml")

        executable = _resolve_executable(self.settings.tools.cura_engine_path)
        if not executable:
            return AdapterResult(
                False,
                "CuraEngine РЅРµ РЅР°Р№РґРµРЅ. РЈСЃС‚Р°РЅРѕРІРёС‚Рµ Cura/CuraEngine РёР»Рё Р·Р°РґР°Р№С‚Рµ external_tools.cura_engine_path.",
            )

        profile_result = self._load_profile()
        if not profile_result.success:
            return profile_result
        profile = profile_result.extra or {}
        cura = profile.get("cura_engine", {}) if isinstance(profile.get("cura_engine"), dict) else {}
        settings = cura.get("settings", {}) if isinstance(cura.get("settings"), dict) else {}
        extruder_settings = cura.get("extruder_settings", {}) if isinstance(cura.get("extruder_settings"), dict) else {}

        batch_dir = self.settings.generated_dir / "batches" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        output_gcode = batch_dir / f"{batch_id}.gcode"
        output_gcode.unlink(missing_ok=True)

        settings_json_path = self._profile_path(cura.get("settings_json_path"))
        if settings_json_path is None or not settings_json_path.exists():
            return AdapterResult(False, f"Cura settings JSON not found: {settings_json_path or 'not configured'}")

        command = [executable, "slice", "-j", str(settings_json_path)]
        command.extend(self._settings_args(settings))

        extruder_json_path = self._profile_path(cura.get("extruder_json_path"))
        if extruder_json_path is not None:
            if not extruder_json_path.exists():
                return AdapterResult(False, f"Cura extruder JSON not found: {extruder_json_path}")
            command.extend(["-e0", "-j", str(extruder_json_path)])
            command.extend(self._settings_args(extruder_settings))
        elif extruder_settings:
            command.append("-e0")
            command.extend(self._settings_args(extruder_settings))

        extra_args = cura.get("extra_args", [])
        if isinstance(extra_args, list):
            command.extend(str(arg) for arg in extra_args)
        command.extend(["-o", str(output_gcode), "-l", str(plate_stl_path)])

        env = os.environ.copy()
        search_path = cura.get("search_path")
        if search_path:
            if isinstance(search_path, list):
                env["CURA_ENGINE_SEARCH_PATH"] = os.pathsep.join(str(self._profile_path(item) or item) for item in search_path)
            else:
                env["CURA_ENGINE_SEARCH_PATH"] = str(self._profile_path(search_path) or search_path)

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.tools.cura_timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return AdapterResult(False, f"CuraEngine timeout after {self.settings.tools.cura_timeout_seconds}s")

        if completed.returncode != 0 or not output_gcode.exists() or output_gcode.stat().st_size == 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "no output"
            return AdapterResult(False, f"CuraEngine failed: {stderr[:1000]}")

        self._replace_header_from_stdout(output_gcode, completed.stdout)
        pause_height_mm = self.settings.model.base_height_mm if filament_change_height_mm is None else filament_change_height_mm
        self._insert_filament_change_pause(output_gcode, pause_height_mm)
        if not self._has_extrusion_moves(output_gcode):
            return AdapterResult(False, "CuraEngine produced G-code without extrusion moves")

        return AdapterResult(True, "G-code СЃРѕР·РґР°РЅ", output_gcode)


    def _load_profile(self) -> AdapterResult:
        if not self.settings.slicer.profile_path.exists():
            return AdapterResult(False, f"Cura profile not found: {self.settings.slicer.profile_path}")
        try:
            profile = yaml.safe_load(self.settings.slicer.profile_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            return AdapterResult(False, f"Cura profile YAML is invalid: {exc}")
        if not isinstance(profile, dict):
            return AdapterResult(False, "Cura profile must be a YAML mapping")
        cura = profile.get("cura_engine")
        if not isinstance(cura, dict) or not cura.get("settings_json_path"):
            return AdapterResult(False, "Cura profile is incomplete: set cura_engine.settings_json_path")
        return AdapterResult(True, "Cura profile loaded", extra=profile)

    def _profile_path(self, value: Any) -> Path | None:
        if value in (None, ""):
            return None
        path = Path(str(value))
        if path.is_absolute():
            return path
        return self.settings.base_dir / path

    def _settings_args(self, settings: dict[str, Any]) -> list[str]:
        args: list[str] = []
        for key, value in settings.items():
            args.extend(["-s", f"{key}={self._setting_value(value)}"])
        return args

    def _setting_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _replace_header_from_stdout(self, gcode_path: Path, stdout: str) -> None:
        marker = "Gcode header after slicing:"
        marker_index = stdout.rfind(marker)
        if marker_index < 0:
            return

        header_lines: list[str] = []
        for line in stdout[marker_index + len(marker) :].splitlines():
            clean = line.strip()
            if not clean and header_lines:
                break
            if clean.startswith(";"):
                header_lines.append(clean)
            elif header_lines:
                break

        if not header_lines:
            return

        lines = gcode_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if not lines or not lines[0].startswith(";FLAVOR:"):
            return

        index = 0
        while index < len(lines) and (lines[index].startswith(";") or not lines[index].strip()):
            index += 1
        gcode_path.write_text("\n".join([*header_lines, "", *lines[index:]]) + "\n", encoding="utf-8")

    def _insert_filament_change_pause(self, gcode_path: Path, height_mm: float) -> None:
        lines = gcode_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if any(FILAMENT_CHANGE_MARKER in line for line in lines):
            return

        insert_at = self._filament_change_insert_index(lines, height_mm)
        if insert_at is None:
            return

        gcode_state = self._gcode_state_before(lines, insert_at)
        pause_lines = [
            f"; {FILAMENT_CHANGE_MARKER}_START",
            f"; Change filament after base at Z={height_mm:.2f} mm",
            "M117 Change filament",
            "G91 ; relative positioning for filament-change lift",
            "M83 ; relative extrusion for filament-change retract",
            f"G1 E-{FILAMENT_CHANGE_RETRACT_MM:g} F{FILAMENT_CHANGE_MOVE_FEEDRATE} ; retract before lift",
            f"G1 Z{FILAMENT_CHANGE_Z_LIFT_MM:g} F{FILAMENT_CHANGE_MOVE_FEEDRATE} ; lift for filament change",
            "M17 X Y Z ; keep motion axes locked during filament change",
            "M84 S0 ; disable stepper idle timeout during filament change",
            f"@pause Change filament at Z={height_mm:.2f} mm",
            f"G1 Z-{FILAMENT_CHANGE_Z_LIFT_MM:g} F{FILAMENT_CHANGE_MOVE_FEEDRATE} ; return to print height",
            "G90" if gcode_state["xyz_absolute"] else "G91",
            "M82" if gcode_state["extruder_absolute"] else "M83",
            f"G92 E{gcode_state['e_position']:.5f} ; keep slicer E position after manual retract",
            "M117 Printing details",
            f"; {FILAMENT_CHANGE_MARKER}_END",
        ]
        updated = [*lines[:insert_at], *pause_lines, *lines[insert_at:]]
        gcode_path.write_text("\n".join(updated) + "\n", encoding="utf-8")

    def _gcode_state_before(self, lines: list[str], end_index: int) -> dict[str, Any]:
        xyz_absolute = True
        extruder_absolute = True
        e_position = 0.0

        for raw_line in lines[:end_index]:
            command = raw_line.split(";", 1)[0].strip()
            if not command:
                continue

            normalized = command.upper()
            if re.match(r"^G90\b", normalized):
                xyz_absolute = True
                continue
            if re.match(r"^G91\b", normalized):
                xyz_absolute = False
                continue
            if re.match(r"^M82\b", normalized):
                extruder_absolute = True
                continue
            if re.match(r"^M83\b", normalized):
                extruder_absolute = False
                continue

            e_match = E_VALUE_RE.search(command)
            if e_match is None:
                continue

            e_value = float(e_match.group(1))
            if re.match(r"^G92\b", normalized):
                e_position = e_value
            elif re.match(r"^(?:G0|G1)\b", normalized):
                e_position = e_value if extruder_absolute else e_position + e_value

        return {
            "xyz_absolute": xyz_absolute,
            "extruder_absolute": extruder_absolute,
            "e_position": e_position,
        }

    def _filament_change_insert_index(self, lines: list[str], height_mm: float) -> int | None:
        for index, line in enumerate(lines):
            if not line.startswith(";LAYER:"):
                continue
            layer_z = self._first_z_after(lines, index)
            if layer_z is not None and layer_z > height_mm + 0.001:
                return index

        for index, line in enumerate(lines):
            z_value = self._z_from_gcode_line(line)
            if z_value is not None and z_value > height_mm + 0.001:
                return index
        return None

    def _first_z_after(self, lines: list[str], start_index: int, max_scan_lines: int = 120) -> float | None:
        for line in lines[start_index + 1 : start_index + max_scan_lines + 1]:
            z_value = self._z_from_gcode_line(line)
            if z_value is not None:
                return z_value
        return None

    def _z_from_gcode_line(self, line: str) -> float | None:
        command = line.split(";", 1)[0].strip()
        if not command or not Z_MOVE_RE.search(command):
            return None
        match = Z_VALUE_RE.search(command)
        if not match:
            return None
        return float(match.group(1))

    def _has_extrusion_moves(self, gcode_path: Path) -> bool:
        extrusion_move = re.compile(r"^G1\b.*\bE-?\d", re.IGNORECASE)
        with gcode_path.open("r", encoding="utf-8", errors="ignore") as handle:
            return any(extrusion_move.search(line) for line in handle)


class OctoPrintController:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._connection_lock = threading.Lock()

    def _preflight(self) -> AdapterResult | None:
        if not self.settings.octoprint.enabled:
            return AdapterResult(False, "OctoPrint РІС‹РєР»СЋС‡РµРЅ РІ config/app.yaml")
        if not self.settings.octoprint.api_key:
            return AdapterResult(False, "OCTOPRINT_API_KEY РЅРµ Р·Р°РґР°РЅ РІ .env")
        return None

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.settings.octoprint.api_key}

    def _is_local_octoprint(self) -> bool:
        parsed = urlparse(self.settings.octoprint.base_url)
        return parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    def _octoprint_reachable(self) -> bool:
        try:
            response = httpx.get(
                f"{self.settings.octoprint.base_url}/api/version",
                timeout=1,
                follow_redirects=True,
            )
            return response.status_code < 500 and response.status_code != 404
        except Exception:
            return False

    def ensure_server_running(self, wait_seconds: float = 25.0) -> AdapterResult:
        if not self.settings.octoprint.enabled:
            return AdapterResult(True, "OctoPrint disabled; server startup skipped", extra={"skipped": True})
        if self._octoprint_reachable():
            return AdapterResult(True, "OctoPrint server reachable")
        if not self._is_local_octoprint():
            return AdapterResult(False, f"OctoPrint server is not reachable: {self.settings.octoprint.base_url}")

        executable = self.settings.base_dir / ".octoprint-venv" / "Scripts" / "octoprint.exe"
        basedir = self.settings.base_dir / "octoprint"
        if not executable.exists():
            return AdapterResult(False, f"OctoPrint executable not found: {executable}")

        tmp_dir = self.settings.base_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_path = tmp_dir / "octoprint_autostart.out.log"
        err_path = tmp_dir / "octoprint_autostart.err.log"
        parsed = urlparse(self.settings.octoprint.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = str(parsed.port or 5000)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            with out_path.open("ab") as stdout, err_path.open("ab") as stderr:
                subprocess.Popen(
                    [str(executable), "serve", "--basedir", str(basedir), "--host", host, "--port", port],
                    cwd=self.settings.base_dir,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creationflags,
                )
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint autostart failed: {exc}")

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self._octoprint_reachable():
                return AdapterResult(True, "OctoPrint server autostarted")
            time.sleep(0.5)

        return AdapterResult(False, f"OctoPrint did not become reachable within {wait_seconds:.0f}s")

    @staticmethod
    def _connection_state(connection_data: dict[str, Any]) -> str:
        current = connection_data.get("current")
        if not isinstance(current, dict):
            return ""
        return str(current.get("state") or "")

    @classmethod
    def _is_connected_state(cls, state: str) -> bool:
        normalized = state.strip().lower()
        return normalized in {"operational", "printing", "paused", "pausing", "cancelling"}

    @staticmethod
    def _is_transient_connection_state(state: str) -> bool:
        normalized = state.strip().lower()
        return any(word in normalized for word in ["opening", "connecting", "detecting"])

    @staticmethod
    def _connection_options(connection_data: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(connection_data, dict):
            return {}
        options = connection_data.get("options")
        return options if isinstance(options, dict) else {}

    def _connection_port(self, options: dict[str, Any]) -> str:
        configured = self.settings.octoprint.printer_port
        ports = options.get("ports")
        if not isinstance(ports, list) or not ports:
            return configured

        normalized_ports = [str(port) for port in ports if port not in ("", None)]
        if configured in normalized_ports:
            return configured

        real_ports = [port for port in normalized_ports if port.upper() != "AUTO"]
        if len(real_ports) == 1:
            return real_ports[0]
        if "AUTO" in [port.upper() for port in normalized_ports]:
            return "AUTO"
        return configured

    def _connection_baudrate(self, options: dict[str, Any]) -> int:
        configured = self.settings.octoprint.baudrate
        baudrates = options.get("baudrates")
        if not isinstance(baudrates, list) or not baudrates:
            return configured

        normalized_baudrates = []
        for baudrate in baudrates:
            try:
                normalized_baudrates.append(int(baudrate))
            except (TypeError, ValueError):
                continue
        if configured in normalized_baudrates:
            return configured
        if 0 in normalized_baudrates:
            return 0
        return normalized_baudrates[0] if normalized_baudrates else configured

    def _connection_printer_profile(self, options: dict[str, Any]) -> str:
        configured = self.settings.octoprint.printer_profile
        profiles = options.get("printerProfiles")
        if not isinstance(profiles, list) or not profiles:
            return configured

        profile_ids = []
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id"):
                profile_ids.append(str(profile["id"]))
            elif isinstance(profile, str):
                profile_ids.append(profile)

        if configured in profile_ids:
            return configured
        if "_default" in profile_ids:
            return "_default"
        return profile_ids[0] if profile_ids else configured

    def _configured_connection_payload(self, connection_data: dict[str, Any] | None = None) -> dict[str, Any]:
        options = self._connection_options(connection_data)
        payload: dict[str, Any] = {
            "command": "connect",
            "port": self._connection_port(options),
            "baudrate": self._connection_baudrate(options),
            "printerProfile": self._connection_printer_profile(options),
            "save": self.settings.octoprint.save_connection,
        }
        return {key: value for key, value in payload.items() if value not in ("", None)}

    def get_connection(self) -> AdapterResult:
        preflight = self._preflight()
        if preflight:
            return preflight

        try:
            response = httpx.get(
                f"{self.settings.octoprint.base_url}/api/connection",
                headers=self._headers(),
                timeout=3,
            )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint connection status failed: {exc}")
        return AdapterResult(True, "OctoPrint connection status", extra=response.json())

    def connect(self) -> AdapterResult:
        preflight = self._preflight()
        if preflight:
            return preflight

        server = self.ensure_server_running()
        if not server.success:
            return server

        current = self.get_connection()
        if current.success and isinstance(current.extra, dict):
            state = self._connection_state(current.extra)
            if self._is_connected_state(state):
                return AdapterResult(True, f"OctoPrint connected: {state}", extra={"connection": current.extra})

        payload = self._configured_connection_payload(current.extra if isinstance(current.extra, dict) else None)
        try:
            response = httpx.post(
                f"{self.settings.octoprint.base_url}/api/connection",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=5,
            )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint connect failed: {exc}", extra={"payload": payload})
        wait_result = self._wait_for_connected(wait_seconds=30.0, fail_on_stable_state=False)
        if wait_result.success:
            return AdapterResult(True, wait_result.message, extra={"payload": payload, "connection": wait_result.extra})
        return AdapterResult(False, wait_result.message, extra={"payload": payload, "connection": wait_result.extra})

    def _wait_for_connected(
        self,
        wait_seconds: float = 8.0,
        poll_seconds: float = 0.5,
        fail_on_stable_state: bool = True,
    ) -> AdapterResult:
        deadline = time.monotonic() + max(0.1, wait_seconds)
        last_message = ""
        last_extra: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            probe = self.get_connection()
            if probe.success and isinstance(probe.extra, dict):
                state = self._connection_state(probe.extra)
                last_extra = probe.extra
                if self._is_connected_state(state):
                    return AdapterResult(True, f"OctoPrint connected: {state}", extra=probe.extra)
                last_message = f"OctoPrint state is {state or 'unknown'}"
                if fail_on_stable_state and state and not self._is_transient_connection_state(state):
                    return AdapterResult(False, last_message, extra=probe.extra)
            else:
                last_message = probe.message
                last_extra = probe.extra
            time.sleep(poll_seconds)
        return AdapterResult(False, last_message or "OctoPrint did not finish connecting", extra=last_extra)

    def ensure_connected(self, attempts: int = 2, settle_seconds: float = 1.0) -> AdapterResult:
        with self._connection_lock:
            return self._ensure_connected(attempts, settle_seconds)

    def _ensure_connected(self, attempts: int = 2, settle_seconds: float = 1.0) -> AdapterResult:
        if not self.settings.octoprint.enabled:
            return AdapterResult(True, "OctoPrint disabled; printer connection check skipped", extra={"skipped": True})

        server = self.ensure_server_running()
        if not server.success:
            return server

        last_message = ""
        for attempt in range(max(1, attempts)):
            connection = self.get_connection()
            if connection.success and isinstance(connection.extra, dict):
                state = self._connection_state(connection.extra)
                if self._is_connected_state(state):
                    return AdapterResult(True, f"OctoPrint connected: {state}", extra=connection.extra)
                if self._is_transient_connection_state(state):
                    wait_result = self._wait_for_connected(max(4.0, settle_seconds * 8))
                    if wait_result.success:
                        return wait_result
                    last_message = wait_result.message
                else:
                    last_message = f"OctoPrint state is {state or 'unknown'}"
            else:
                last_message = connection.message

            if attempt < attempts - 1:
                time.sleep(settle_seconds)

        final = self.get_connection()
        if final.success and isinstance(final.extra, dict):
            state = self._connection_state(final.extra)
            if self._is_connected_state(state):
                return AdapterResult(True, f"OctoPrint connected: {state}", extra=final.extra)
            last_message = f"OctoPrint state is {state or 'unknown'}"

        return AdapterResult(False, last_message or final.message)

    def upload_and_print(self, gcode_path: Path) -> AdapterResult:
        if not self.settings.octoprint.enabled:
            return AdapterResult(False, "OctoPrint РІС‹РєР»СЋС‡РµРЅ РІ config/app.yaml")
        if not self.settings.octoprint.api_key:
            return AdapterResult(False, "OCTOPRINT_API_KEY РЅРµ Р·Р°РґР°РЅ РІ .env")

        url = f"{self.settings.octoprint.base_url}/api/files/local"
        headers = {"X-Api-Key": self.settings.octoprint.api_key}
        try:
            with httpx.Client(timeout=30) as client:
                with gcode_path.open("rb") as handle:
                    response = client.post(
                        url,
                        headers=headers,
                        files={"file": (gcode_path.name, handle, "application/octet-stream")},
                        data={"select": "true", "print": "true"},
                    )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint upload failed: {exc}")

        return AdapterResult(True, "G-code РѕС‚РїСЂР°РІР»РµРЅ РІ OctoPrint", gcode_path)

    def set_bed_target(self, target_c: int) -> AdapterResult:
        preflight = self._preflight()
        if preflight:
            return preflight

        try:
            response = httpx.post(
                f"{self.settings.octoprint.base_url}/api/printer/bed",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"command": "target", "target": int(target_c)},
                timeout=5,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - integration path
            return AdapterResult(
                False,
                f"OctoPrint bed target failed: HTTP {exc.response.status_code}",
                extra={"status_code": exc.response.status_code},
            )
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint bed target failed: {exc}")

        return AdapterResult(True, f"Bed target set to {int(target_c)}C", extra={"target_c": int(target_c)})

    def set_tool_target(self, target_c: int, tool: str = "tool0") -> AdapterResult:
        preflight = self._preflight()
        if preflight:
            return preflight

        try:
            response = httpx.post(
                f"{self.settings.octoprint.base_url}/api/printer/tool",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"command": "target", "targets": {tool: int(target_c)}},
                timeout=5,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - integration path
            return AdapterResult(
                False,
                f"OctoPrint tool target failed: HTTP {exc.response.status_code}",
                extra={"status_code": exc.response.status_code, "tool": tool},
            )
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint tool target failed: {exc}", extra={"tool": tool})

        return AdapterResult(True, f"{tool} target set to {int(target_c)}C", extra={"target_c": int(target_c), "tool": tool})

    def cancel_job(self) -> AdapterResult:
        preflight = self._preflight()
        if preflight:
            return preflight

        try:
            response = httpx.post(
                f"{self.settings.octoprint.base_url}/api/job",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"command": "cancel"},
                timeout=5,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - integration path
            if exc.response.status_code == 409:
                return AdapterResult(
                    True,
                    "No active OctoPrint job to cancel",
                    extra={"status_code": exc.response.status_code, "no_active_job": True},
                )
            return AdapterResult(
                False,
                f"OctoPrint cancel failed: HTTP {exc.response.status_code}",
                extra={"status_code": exc.response.status_code},
            )
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint cancel failed: {exc}")

        return AdapterResult(True, "OctoPrint print job cancelled")

    def resume_print(self) -> AdapterResult:
        preflight = self._preflight()
        if preflight:
            return preflight

        try:
            response = httpx.post(
                f"{self.settings.octoprint.base_url}/api/job",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"command": "pause", "action": "resume"},
                timeout=5,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - integration path
            return AdapterResult(
                False,
                f"OctoPrint resume failed: HTTP {exc.response.status_code}",
                extra={"status_code": exc.response.status_code},
            )
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint resume failed: {exc}")

        return AdapterResult(True, "OctoPrint print job resumed")

    def get_job(self) -> AdapterResult:
        if not self.settings.octoprint.enabled:
            return AdapterResult(False, "OctoPrint РІС‹РєР»СЋС‡РµРЅ РІ config/app.yaml")
        if not self.settings.octoprint.api_key:
            return AdapterResult(False, "OCTOPRINT_API_KEY РЅРµ Р·Р°РґР°РЅ РІ .env")

        try:
            response = httpx.get(
                f"{self.settings.octoprint.base_url}/api/job",
                headers={"X-Api-Key": self.settings.octoprint.api_key},
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint status failed: {exc}")
        return AdapterResult(True, "OctoPrint job status", extra=response.json())

    def get_printer(self) -> AdapterResult:
        if not self.settings.octoprint.enabled:
            return AdapterResult(False, "OctoPrint РІС‹РєР»СЋС‡РµРЅ РІ config/app.yaml")
        if not self.settings.octoprint.api_key:
            return AdapterResult(False, "OCTOPRINT_API_KEY РЅРµ Р·Р°РґР°РЅ РІ .env")

        try:
            response = httpx.get(
                f"{self.settings.octoprint.base_url}/api/printer",
                headers={"X-Api-Key": self.settings.octoprint.api_key},
                timeout=10,
            )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"OctoPrint printer status failed: {exc}")
        return AdapterResult(True, "OctoPrint printer status", extra=response.json())


class NullNotifier:
    def notify_ready(self, order: dict[str, Any]) -> AdapterResult:
        return AdapterResult(True, "Notifications are disabled for MVP", extra={"order_id": order["id"]})
