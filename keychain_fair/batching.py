from __future__ import annotations

from pathlib import Path
import json
from typing import Any


def layout_orders(
    orders: list[dict[str, Any]],
    bed_size_mm: tuple[float, float],
    spacing_mm: float,
    max_items: int,
) -> list[dict[str, Any]]:
    bed_width, bed_height = bed_size_mm
    selected = orders[:max_items]
    layout: list[dict[str, Any]] = []
    x = spacing_mm
    y = spacing_mm
    row_height = 0.0

    for order in selected:
        width = float(order["width_mm"])
        height = float(order["height_mm"])

        if x + width + spacing_mm > bed_width:
            x = spacing_mm
            y += row_height + spacing_mm
            row_height = 0.0

        if y + height + spacing_mm > bed_height:
            break

        layout.append({"order": order, "x_mm": x, "y_mm": y})
        x += width + spacing_mm
        row_height = max(row_height, height)

    return layout


def write_batch_manifest(batch_id: str, layout_items: list[dict[str, Any]], target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "batch_id": batch_id,
        "items": [
            {
                "order_id": item["order"]["id"],
                "customer_name": item["order"]["customer_name"],
                "car_number": item["order"]["car_number"],
                "phone": item["order"]["phone"],
                "stl_path": item["order"]["stl_path"],
                "x_mm": item["x_mm"],
                "y_mm": item["y_mm"],
                "width_mm": item["order"]["width_mm"],
                "height_mm": item["order"]["height_mm"],
            }
            for item in layout_items
        ],
    }
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target_path

