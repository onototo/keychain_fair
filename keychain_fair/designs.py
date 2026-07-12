from __future__ import annotations

from dataclasses import dataclass
import json
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
            designs.append(design)
        return designs

    def load_public_designs(self) -> list[dict[str, Any]]:
        public_designs: list[dict[str, Any]] = []
        for design in self.load_designs():
            public_design = dict(design)
            public_design.pop("source", None)
            public_design.pop("template_path", None)
            public_design.pop("template_available", None)
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

        for size in design["sizes"]:
            for key in ["id", "label", "width_mm", "height_mm", "thickness_mm", "font_size_mm"]:
                if key not in size:
                    raise DesignCatalogError(f"{source_path.name} size misses '{key}'")

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
