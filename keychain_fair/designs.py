from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any


class DesignCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class DesignSelection:
    design: dict[str, Any]
    size: dict[str, Any]
    elements: list[dict[str, Any]]


class DesignCatalog:
    def __init__(self, designs_dir: Path):
        self.designs_dir = designs_dir

    def load_designs(self) -> list[dict[str, Any]]:
        designs: list[dict[str, Any]] = []
        if not self.designs_dir.exists():
            return designs

        for path in sorted(self.designs_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            design = self._normalize_design(raw, path)
            if design["id"] != "classic_plate":
                continue
            designs.append(design)
        return sorted(designs, key=lambda item: (float(item.get("sort_order", 999)), item["id"]))

    def load_public_designs(self) -> list[dict[str, Any]]:
        public_designs: list[dict[str, Any]] = []
        for design in self.load_designs():
            public_design = dict(design)
            public_design.pop("source", None)
            public_design.pop("template_path", None)
            public_design.pop("template_available", None)
            public_design.pop("preview_image_path", None)
            public_design.pop("preview_image_available", None)
            public_designs.append(public_design)
        return public_designs

    def get_design(self, design_id: str) -> dict[str, Any]:
        for design in self.load_designs():
            if design["id"] == design_id:
                return design
        raise DesignCatalogError(f"Unknown design: {design_id}")

    def validate_selection(self, design_id: str, size_id: str, element_ids: list[str]) -> DesignSelection:
        design = self.get_design(design_id)
        size = next((item for item in design["sizes"] if item["id"] == size_id), None)
        if size is None:
            raise DesignCatalogError(f"Unknown size '{size_id}' for design '{design_id}'")

        allowed_elements = {item["id"]: item for item in design.get("elements", [])}
        requested_element_ids = list(element_ids)
        if not requested_element_ids and design.get("default_elements"):
            requested_element_ids = list(design["default_elements"])

        for default_id in design.get("default_elements", []):
            default_element = allowed_elements.get(default_id)
            if default_element is None:
                continue
            default_kind = default_element.get("kind")
            if not default_kind or default_kind == "content":
                continue
            has_same_kind = any(allowed_elements.get(item, {}).get("kind") == default_kind for item in requested_element_ids)
            if not has_same_kind:
                requested_element_ids.append(default_id)

        unknown = [item for item in requested_element_ids if item not in allowed_elements]
        if unknown:
            raise DesignCatalogError(f"Unsupported elements for design '{design_id}': {', '.join(unknown)}")

        elements = [allowed_elements[item] for item in requested_element_ids]
        return DesignSelection(design=design, size=size, elements=elements)

    def _normalize_design(self, raw: dict[str, Any], source_path: Path) -> dict[str, Any]:
        required = ["id", "name", "template", "sizes"]
        missing = [key for key in required if key not in raw]
        if missing:
            raise DesignCatalogError(f"{source_path.name} misses required fields: {', '.join(missing)}")

        template_path = self.designs_dir / raw["template"]
        design = dict(raw)
        design["source"] = str(source_path)
        design["template_path"] = str(template_path)
        design["template_available"] = template_path.exists()
        design.setdefault("description", "")
        design.setdefault("accent", "#3b82f6")
        design.setdefault("elements", [])
        design.setdefault("default_elements", [])
        preview_url, preview_path = self._preview_image(raw)
        design["preview_image"] = preview_url
        design["preview_image_path"] = str(preview_path)
        design["preview_image_available"] = preview_path.exists()

        for size in design["sizes"]:
            for key in ["id", "label", "width_mm", "height_mm", "thickness_mm", "font_size_mm"]:
                if key not in size:
                    raise DesignCatalogError(f"{source_path.name} size misses '{key}'")
            if design.get("print_mode") == "custom_text":
                size["custom_text_limits"] = custom_text_limits_for_size(size)

        if design.get("default_size_id"):
            size_ids = {item["id"] for item in design["sizes"]}
            if design["default_size_id"] not in size_ids:
                raise DesignCatalogError(f"{source_path.name} default_size_id points to an unknown size")

        allowed_element_ids = {item["id"] for item in design["elements"]}
        unknown_defaults = [item for item in design["default_elements"] if item not in allowed_element_ids]
        if unknown_defaults:
            raise DesignCatalogError(
                f"{source_path.name} default_elements contains unknown ids: {', '.join(unknown_defaults)}"
            )
        return design

    def _preview_image(self, raw: dict[str, Any]) -> tuple[str, Path]:
        configured = str(raw.get("preview_image") or "").strip()
        static_root = self.designs_dir.parent / "static"

        if configured:
            if configured.startswith("/static/"):
                return configured, self.designs_dir.parent / configured.lstrip("/")
            if configured.startswith("/"):
                return configured, self.designs_dir.parent / configured.lstrip("/")
            return f"/static/design-previews/{configured}", static_root / "design-previews" / configured

        design_id = str(raw.get("id") or "fallback")
        candidate = static_root / "design-previews" / f"{design_id}.png"
        if candidate.exists():
            return f"/static/design-previews/{design_id}.png", candidate

        fallback = static_root / "design-previews" / "fallback.png"
        return "/static/design-previews/fallback.png", fallback


def custom_text_limits_for_size(size: dict[str, Any]) -> dict[str, int]:
    params = size.get("editor_params") if isinstance(size.get("editor_params"), dict) else {}
    blocks = params.get("text_blocks") if isinstance(params.get("text_blocks"), dict) else {}

    block = blocks.get("car") if isinstance(blocks.get("car"), dict) else {}
    try:
        width = float(block.get("box_width_mm"))
        height = float(block.get("box_height_mm"))
    except (TypeError, ValueError):
        width = 0
        height = 0
    if width <= 0:
        width = float(size.get("width_mm", 60)) - 22
    if height <= 0:
        height = float(size.get("height_mm", 24)) * 0.48

    min_font = 6.0
    first = max(1, math.floor(width * 1.35 / (min_font * 0.82)))
    second = first if height / 2.08 >= min_font else 0
    return {
        "max_total_chars": first + second,
        "max_line_1_chars": first,
        "max_line_2_chars": second,
    }
