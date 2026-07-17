from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any
import uuid

from .validation import normalize_by_car_number, split_by_car_number


DEFAULT_BASE_HEIGHT_MM = 3.2
DEFAULT_RELIEF_HEIGHT_MM = 0.8
PRINT_FIRST_LAYER_HEIGHT_MM = 0.1
PRINT_LAYER_HEIGHT_MM = 0.2
BASE_HEIGHT_MIN_MM = 1.1
BASE_HEIGHT_MAX_MM = 5.9
RELIEF_HEIGHT_MIN_MM = 0.2
RELIEF_HEIGHT_MAX_MM = 2.0
TEXT_BLOCK_KEYS = ("name", "car", "phone")
TEXT_BLOCK_FIELDS = (
    "x_offset_mm",
    "y_offset_mm",
    "box_width_mm",
    "box_height_mm",
    "font_size_mm",
    "relief_height_mm",
)
STACKED_OVERLAP_INDEX = 25
DEFAULT_SAMPLE_TEXT = {
    "customer_name": "Nikita",
    "car_number": "1234AB7",
    "print_line_2": "",
}


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    number = _float(value, fallback)
    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _layer_aligned(value: Any, fallback: float, minimum: float, maximum: float, offset: float = 0.0) -> float:
    number = _clamp(value, fallback, minimum, maximum)
    steps = int(((number - offset) / PRINT_LAYER_HEIGHT_MM) + 0.5 + 0.000000001)
    aligned = offset + steps * PRINT_LAYER_HEIGHT_MM
    return round(min(max(aligned, minimum), maximum), 3)


def _base_height(value: Any, fallback: float = DEFAULT_BASE_HEIGHT_MM) -> float:
    return _layer_aligned(value, fallback, BASE_HEIGHT_MIN_MM, BASE_HEIGHT_MAX_MM, PRINT_FIRST_LAYER_HEIGHT_MM)


def _relief_height(value: Any, fallback: float = DEFAULT_RELIEF_HEIGHT_MM) -> float:
    return _layer_aligned(value, fallback, RELIEF_HEIGHT_MIN_MM, RELIEF_HEIGHT_MAX_MM)


def _model_scale(size: dict[str, Any]) -> float:
    return _float(size.get("model_scale"), 1.0) or 1.0


def _default_overlap_mm(design: dict[str, Any], size: dict[str, Any]) -> float:
    overlap = _float(size.get("section_overlap_mm", design.get("section_overlap_mm")), 1.4)
    return overlap * _model_scale(size)


def is_stacked_design(design: dict[str, Any]) -> bool:
    return design.get("layout") == "stacked_plate"


def content_element_count(selected_elements: list[dict[str, Any]] | list[str]) -> int:
    count = 0
    for item in selected_elements:
        shape = item.get("shape", item.get("id")) if isinstance(item, dict) else item
        if shape in TEXT_BLOCK_KEYS:
            count += 1
    return max(1, count)


def _selected_shapes(selected_elements: list[dict[str, Any]] | list[str]) -> list[str]:
    shapes: list[str] = []
    for item in selected_elements:
        shape = item.get("shape", item.get("id")) if isinstance(item, dict) else item
        if shape:
            shapes.append(str(shape))
    return shapes


def stacked_total_height_mm(
    design: dict[str, Any],
    row_height_mm: float,
    selected_elements: list[dict[str, Any]] | list[str],
    overlap_mm: float | None = None,
) -> float:
    count = content_element_count(selected_elements)
    top_ratio = _float(design.get("top_block_ratio"), 0.56)
    bottom_ratio = _float(design.get("bottom_block_ratio"), 0.56)
    overlap = _float(overlap_mm, _float(design.get("section_overlap_mm"), 1.4))
    if count == 1:
        return row_height_mm
    if count == 2:
        return row_height_mm + row_height_mm * bottom_ratio - overlap
    return row_height_mm + row_height_mm * top_ratio + row_height_mm * bottom_ratio - 2 * overlap


def final_dimensions_mm(
    design: dict[str, Any],
    params: dict[str, Any],
    selected_elements: list[dict[str, Any]] | list[str],
) -> tuple[float, float]:
    width = _float(params.get("base_width_mm"), 60.0)
    base_height = _float(params.get("base_height_mm"), 24.0)
    if is_stacked_design(design):
        return width, stacked_total_height_mm(design, base_height, selected_elements, params.get("section_overlap_mm"))
    return width, base_height


def _default_text_blocks(design: dict[str, Any], base_width: float, base_height: float, font_size: float) -> dict[str, dict[str, float]]:
    relief = DEFAULT_RELIEF_HEIGHT_MM
    if is_stacked_design(design):
        return {
            "name": {
                "x_offset_mm": 0.0,
                "y_offset_mm": 0.0,
                "box_width_mm": 0.0,
                "box_height_mm": 0.0,
                "font_size_mm": round(font_size * 0.72, 3),
                "relief_height_mm": relief,
            },
            "car": {
                "x_offset_mm": 0.0,
                "y_offset_mm": 0.0,
                "box_width_mm": 0.0,
                "box_height_mm": 0.0,
                "font_size_mm": round(font_size, 3),
                "relief_height_mm": relief,
            },
            "phone": {
                "x_offset_mm": 0.0,
                "y_offset_mm": 0.0,
                "box_width_mm": 0.0,
                "box_height_mm": 0.0,
                "font_size_mm": round(font_size * 0.76, 3),
                "relief_height_mm": relief,
            },
        }

    return {
        "name": {
            "x_offset_mm": 0.0,
            "y_offset_mm": 0.0,
            "box_width_mm": 0.0,
            "box_height_mm": 0.0,
            "font_size_mm": round(font_size * 0.72, 3),
            "relief_height_mm": relief,
        },
        "car": {
            "x_offset_mm": 0.0,
            "y_offset_mm": 0.0,
            "box_width_mm": round(max(8.0, base_width - 22.0), 3),
            "box_height_mm": round(max(5.0, base_height * 0.34), 3),
            "font_size_mm": round(font_size, 3),
            "relief_height_mm": relief,
        },
        "phone": {
            "x_offset_mm": 0.0,
            "y_offset_mm": 0.0,
            "box_width_mm": round(max(8.0, base_width - 18.0), 3),
            "box_height_mm": round(max(4.0, base_height * 0.22), 3),
            "font_size_mm": round(font_size * 0.48, 3),
            "relief_height_mm": relief,
        },
    }


def default_editor_params(design: dict[str, Any], size: dict[str, Any], base_height_mm: float = DEFAULT_BASE_HEIGHT_MM) -> dict[str, Any]:
    scale = _model_scale(size)
    if "editor_params" in size and isinstance(size["editor_params"], dict):
        base = deepcopy(size["editor_params"])
    else:
        base_width = _float(size.get("width_mm"), 60.0) * scale
        raw_height = _float(size.get("row_height_mm" if is_stacked_design(design) else "height_mm"), 24.0)
        base_height = raw_height * scale
        font_size = _float(size.get("font_size_mm"), 7.0) * scale
        hole_x = 0.0 if is_stacked_design(design) else (7.0 if design.get("id") == "rounded_tag" else 6.5)
        hole_y = 0.0 if is_stacked_design(design) else (base_height / 2 if design.get("id") == "rounded_tag" else max(6.5, base_height - 6.5))
        hole_radius = max(1.2, base_height * 0.10) if is_stacked_design(design) else 2.35
        base = {
            "base_width_mm": round(base_width, 3),
            "base_height_mm": round(base_height, 3),
            "thickness_mm": _base_height(size.get("thickness_mm"), base_height_mm),
            "relief_height_mm": _relief_height(size.get("relief_height_mm"), DEFAULT_RELIEF_HEIGHT_MM),
            "section_overlap_mm": round(_default_overlap_mm(design, size), 3) if is_stacked_design(design) else 0.0,
            "hole": {
                "x_mm": round(hole_x, 3),
                "y_mm": round(hole_y, 3),
                "radius_mm": round(hole_radius, 3),
            },
            "text_blocks": _default_text_blocks(design, base_width, base_height, font_size),
        }
    return normalize_editor_params(design, size, base, base_height_mm)


def normalize_editor_params(
    design: dict[str, Any],
    size: dict[str, Any],
    params: dict[str, Any] | None,
    base_height_mm: float = DEFAULT_BASE_HEIGHT_MM,
) -> dict[str, Any]:
    default = deepcopy(size.get("editor_params")) if isinstance(size.get("editor_params"), dict) else None
    if default is None:
        scale = _model_scale(size)
        base_width = _float(size.get("width_mm"), 60.0) * scale
        raw_height = _float(size.get("row_height_mm" if is_stacked_design(design) else "height_mm"), 24.0)
        base_height = raw_height * scale
        font_size = _float(size.get("font_size_mm"), 7.0) * scale
        default = {
            "base_width_mm": base_width,
            "base_height_mm": base_height,
            "thickness_mm": _base_height(size.get("thickness_mm"), base_height_mm),
            "relief_height_mm": _relief_height(size.get("relief_height_mm"), DEFAULT_RELIEF_HEIGHT_MM),
            "section_overlap_mm": round(_default_overlap_mm(design, size), 3) if is_stacked_design(design) else 0.0,
            "hole": {
                "x_mm": 0.0 if is_stacked_design(design) else (7.0 if design.get("id") == "rounded_tag" else 6.5),
                "y_mm": 0.0 if is_stacked_design(design) else (base_height / 2 if design.get("id") == "rounded_tag" else max(6.5, base_height - 6.5)),
                "radius_mm": max(1.2, base_height * 0.10) if is_stacked_design(design) else 2.35,
            },
            "text_blocks": _default_text_blocks(design, base_width, base_height, font_size),
        }

    source = params if isinstance(params, dict) else {}
    hole_source = source.get("hole") if isinstance(source.get("hole"), dict) else {}
    default_hole = default.get("hole") if isinstance(default.get("hole"), dict) else {}

    normalized = {
        "base_width_mm": round(_clamp(source.get("base_width_mm"), default.get("base_width_mm", 60), 20, 160), 3),
        "base_height_mm": round(_clamp(source.get("base_height_mm"), default.get("base_height_mm", 24), 8, 80), 3),
        "thickness_mm": _base_height(source.get("thickness_mm"), default.get("thickness_mm", base_height_mm)),
        "relief_height_mm": _relief_height(source.get("relief_height_mm"), default.get("relief_height_mm", DEFAULT_RELIEF_HEIGHT_MM)),
        "section_overlap_mm": round(
            _clamp(
                source.get("section_overlap_mm"),
                default.get("section_overlap_mm", _default_overlap_mm(design, size) if is_stacked_design(design) else 0.0),
                0,
                20,
            ),
            3,
        ),
        "hole": {
            "x_mm": round(_clamp(hole_source.get("x_mm"), default_hole.get("x_mm", 0), -80, 160), 3),
            "y_mm": round(_clamp(hole_source.get("y_mm"), default_hole.get("y_mm", 0), -80, 160), 3),
            "radius_mm": round(_clamp(hole_source.get("radius_mm"), default_hole.get("radius_mm", 2.35), 1, 8), 3),
        },
        "text_blocks": {},
    }

    block_source = source.get("text_blocks") if isinstance(source.get("text_blocks"), dict) else {}
    default_blocks = default.get("text_blocks") if isinstance(default.get("text_blocks"), dict) else {}
    for key in TEXT_BLOCK_KEYS:
        current = block_source.get(key) if isinstance(block_source.get(key), dict) else {}
        fallback = default_blocks.get(key) if isinstance(default_blocks.get(key), dict) else {}
        normalized["text_blocks"][key] = {
            "x_offset_mm": round(_clamp(current.get("x_offset_mm"), fallback.get("x_offset_mm", 0), -50, 50), 3),
            "y_offset_mm": round(_clamp(current.get("y_offset_mm"), fallback.get("y_offset_mm", 0), -50, 50), 3),
            "box_width_mm": round(_clamp(current.get("box_width_mm"), fallback.get("box_width_mm", 0), 0, 160), 3),
            "box_height_mm": round(_clamp(current.get("box_height_mm"), fallback.get("box_height_mm", 0), 0, 80), 3),
            "font_size_mm": round(_clamp(current.get("font_size_mm"), fallback.get("font_size_mm", 7), 1, 40), 3),
            "relief_height_mm": _relief_height(current.get("relief_height_mm"), fallback.get("relief_height_mm", DEFAULT_RELIEF_HEIGHT_MM)),
        }
    return normalized


def editor_params_to_scad_array(params: dict[str, Any]) -> str:
    values: list[float] = [
        _float(params.get("base_width_mm"), 60.0),
        _float(params.get("base_height_mm"), 24.0),
        _float(params.get("thickness_mm"), DEFAULT_BASE_HEIGHT_MM),
        _float(params.get("relief_height_mm"), DEFAULT_RELIEF_HEIGHT_MM),
    ]
    hole = params.get("hole") if isinstance(params.get("hole"), dict) else {}
    values.extend(
        [
            _float(hole.get("x_mm"), 0.0),
            _float(hole.get("y_mm"), 0.0),
            _float(hole.get("radius_mm"), 2.35),
        ]
    )
    text_blocks = params.get("text_blocks") if isinstance(params.get("text_blocks"), dict) else {}
    for key in TEXT_BLOCK_KEYS:
        block = text_blocks.get(key) if isinstance(text_blocks.get(key), dict) else {}
        for field in TEXT_BLOCK_FIELDS:
            values.append(_float(block.get(field), 0.0))
    values.append(_float(params.get("section_overlap_mm"), 0.0))
    return "[" + ", ".join(f"{value:.3f}".rstrip("0").rstrip(".") for value in values) + "]"


def model_params_for_selection(selection: Any, base_height_mm: float = DEFAULT_BASE_HEIGHT_MM) -> dict[str, Any]:
    return default_editor_params(selection.design, selection.size, base_height_mm)


def designs_for_editor(designs: list[dict[str, Any]], base_height_mm: float = DEFAULT_BASE_HEIGHT_MM) -> list[dict[str, Any]]:
    editable: list[dict[str, Any]] = []
    for design in designs:
        item = deepcopy(design)
        for size in item.get("sizes", []):
            size["editor_params"] = default_editor_params(item, size, base_height_mm)
            width, height = final_dimensions_mm(item, size["editor_params"], _default_preview_elements(item))
            size["preview_bounds_mm"] = {
                "width": round(width, 3),
                "height": round(height, 3),
                "z": round(size["editor_params"]["thickness_mm"] + size["editor_params"]["relief_height_mm"], 3),
            }
        editable.append(item)
    return editable


def _default_preview_elements(design: dict[str, Any]) -> list[str]:
    if design.get("print_mode") == "by_number_single":
        return ["car"]
    return list(design.get("default_elements") or [])


def preview_order_payload(
    design: dict[str, Any],
    size: dict[str, Any],
    params: dict[str, Any],
    sample_text: dict[str, Any] | None = None,
    element_ids: list[str] | None = None,
) -> dict[str, Any]:
    sample = dict(DEFAULT_SAMPLE_TEXT)
    if isinstance(sample_text, dict):
        for key in DEFAULT_SAMPLE_TEXT:
            value = sample_text.get(key)
            if value is not None:
                sample[key] = str(value)

    allowed = {item["id"]: item for item in design.get("elements", [])}
    requested = element_ids if element_ids else _default_preview_elements(design)
    selected: list[dict[str, Any]] = []
    for element_id in requested:
        if element_id in allowed:
            selected.append(allowed[element_id])
        elif element_id in TEXT_BLOCK_KEYS:
            selected.append({"id": element_id, "shape": element_id})
    if is_stacked_design(design) and not any(item.get("shape") in TEXT_BLOCK_KEYS for item in selected):
        selected.extend([{"id": "car", "shape": "car"}, {"id": "loop_left", "shape": "loop_left"}])

    print_line_1 = str(sample["car_number"])
    print_line_2 = ""
    mode = str(design.get("print_mode") or "")
    if mode == "by_number_square":
        try:
            print_line_1, print_line_2 = split_by_car_number(sample["car_number"])
        except ValueError:
            print_line_1, print_line_2 = "1234", "AB-7"
    elif mode == "by_number_single":
        try:
            print_line_1 = normalize_by_car_number(sample["car_number"])
        except ValueError:
            print_line_1 = "1234 AB-7"
    elif mode == "custom_text":
        print_line_1 = str(sample_text.get("print_line_1") if isinstance(sample_text, dict) and sample_text.get("print_line_1") else sample["car_number"])
        print_line_2 = str(sample_text.get("print_line_2") if isinstance(sample_text, dict) and sample_text.get("print_line_2") else sample["print_line_2"])

    width, height = final_dimensions_mm(design, params, selected)
    car_block = params.get("text_blocks", {}).get("car", {}) if isinstance(params.get("text_blocks"), dict) else {}
    return {
        "id": f"preview_{uuid.uuid4().hex[:12]}",
        "customer_name": sample["customer_name"],
        "car_number": sample["car_number"],
        "phone": "",
        "print_line_1": print_line_1,
        "print_line_2": print_line_2,
        "design_id": design["id"],
        "size_id": size["id"],
        "selected_elements": selected,
        "width_mm": width,
        "height_mm": height,
        "thickness_mm": params["thickness_mm"],
        "font_size_mm": _float(car_block.get("font_size_mm"), size.get("font_size_mm", 7.0)),
        "model_params": params,
    }


def bounds_for_params(
    design: dict[str, Any],
    params: dict[str, Any],
    selected_elements: list[dict[str, Any]] | list[str],
) -> dict[str, float]:
    width, height = final_dimensions_mm(design, params, selected_elements)
    return {
        "width": round(width, 3),
        "height": round(height, 3),
        "z": round(
            _float(params.get("thickness_mm"), DEFAULT_BASE_HEIGHT_MM)
            + _float(params.get("relief_height_mm"), DEFAULT_RELIEF_HEIGHT_MM),
            3,
        ),
    }


def _sync_shared_heights_for_design(raw: dict[str, Any], base_height_mm: float, relief_height_mm: float) -> bool:
    changed = False
    synced_base_height = _base_height(base_height_mm)
    synced_relief_height = _relief_height(relief_height_mm)
    for size in raw.get("sizes", []):
        if not isinstance(size, dict):
            continue
        if "thickness_mm" not in size or round(_float(size.get("thickness_mm"), synced_base_height), 3) != synced_base_height:
            size["thickness_mm"] = synced_base_height
            changed = True
        if "relief_height_mm" not in size or round(_float(size.get("relief_height_mm"), synced_relief_height), 3) != synced_relief_height:
            size["relief_height_mm"] = synced_relief_height
            changed = True
        editor_params = size.get("editor_params")
        if isinstance(editor_params, dict):
            if (
                "thickness_mm" not in editor_params
                or round(_float(editor_params.get("thickness_mm"), synced_base_height), 3) != synced_base_height
            ):
                editor_params["thickness_mm"] = synced_base_height
                changed = True
            if (
                "relief_height_mm" not in editor_params
                or round(_float(editor_params.get("relief_height_mm"), synced_relief_height), 3) != synced_relief_height
            ):
                editor_params["relief_height_mm"] = synced_relief_height
                changed = True
    return changed


def _write_design_json(path: Path, raw: dict[str, Any]) -> None:
    encoded = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(encoded, encoding="utf-8")
    temp_path.replace(path)


def save_design_preset(
    design: dict[str, Any],
    size_id: str,
    params: dict[str, Any],
    backups_dir: Path,
    base_height_mm: float = DEFAULT_BASE_HEIGHT_MM,
) -> dict[str, Any]:
    source_path = Path(design["source"])
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    sizes = raw.get("sizes", [])
    target = next((item for item in sizes if item.get("id") == size_id), None)
    if target is None:
        raise KeyError(size_id)

    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = backups_dir / f"{stamp}_{source_path.name}"
    shutil.copy2(source_path, backup_path)

    if "legacy_model_scale" not in target and "model_scale" in target:
        target["legacy_model_scale"] = target.get("model_scale")
    target["width_mm"] = params["base_width_mm"]
    target["height_mm"] = params["base_height_mm"]
    if is_stacked_design(raw):
        target["row_height_mm"] = params["base_height_mm"]
        target["section_overlap_mm"] = params.get("section_overlap_mm", target.get("section_overlap_mm", 1.4))
    preset_params = deepcopy(params)
    target["thickness_mm"] = preset_params["thickness_mm"]
    target["relief_height_mm"] = preset_params["relief_height_mm"]
    car_block = params.get("text_blocks", {}).get("car", {}) if isinstance(params.get("text_blocks"), dict) else {}
    target["font_size_mm"] = car_block.get("font_size_mm", target.get("font_size_mm", 7.0))
    target["model_scale"] = 1
    target["editor_params"] = preset_params
    synced_base_height = _float(preset_params.get("thickness_mm"), base_height_mm)
    synced_relief_height = _float(preset_params.get("relief_height_mm"), DEFAULT_RELIEF_HEIGHT_MM)
    _sync_shared_heights_for_design(raw, synced_base_height, synced_relief_height)

    _write_design_json(source_path, raw)
    synced_sources = [str(source_path)]
    backup_paths = [str(backup_path)]

    for other_path in sorted(source_path.parent.glob("*.json")):
        if other_path.resolve() == source_path.resolve():
            continue
        other_raw = json.loads(other_path.read_text(encoding="utf-8"))
        if not _sync_shared_heights_for_design(other_raw, synced_base_height, synced_relief_height):
            continue
        other_backup = backups_dir / f"{stamp}_{other_path.name}"
        shutil.copy2(other_path, other_backup)
        _write_design_json(other_path, other_raw)
        synced_sources.append(str(other_path))
        backup_paths.append(str(other_backup))

    return {"source": str(source_path), "backup": str(backup_path), "synced_sources": synced_sources, "backups": backup_paths}
