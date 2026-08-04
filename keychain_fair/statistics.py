from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile


CellValue = str | int | float | None
Sheet = tuple[str, list[list[CellValue]]]


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _local_datetime(value: str | None) -> datetime | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return parsed.astimezone()


def _format_local(value: str | None) -> str:
    local = _local_datetime(value)
    return local.strftime("%Y-%m-%d %H:%M:%S") if local else ""


def _created_hour(value: str | None) -> str:
    local = _local_datetime(value)
    return local.strftime("%H:00") if local else "unknown"


def _minutes(seconds: Any) -> float | None:
    if seconds is None:
        return None
    try:
        return round(float(seconds) / 60, 1)
    except (TypeError, ValueError):
        return None


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _order_minutes(order: dict[str, Any]) -> float | None:
    return _minutes(order.get("paid_to_ready_seconds"))


def _is_paid(order: dict[str, Any]) -> bool:
    return bool(order.get("paid_at"))


def _is_ready(order: dict[str, Any]) -> bool:
    return bool(order.get("ready_for_pickup_at"))


def _orders_sheet(orders: list[dict[str, Any]]) -> list[list[CellValue]]:
    rows: list[list[CellValue]] = [
        [
            "Order #",
            "ID",
            "Статус",
            "Модель",
            "Размер",
            "Дата заказа",
            "Дата оплаты",
            "Дата готовности",
            "Минут от оплаты до готовности",
        ]
    ]
    for order in sorted(orders, key=lambda item: str(item.get("created_at") or "")):
        rows.append(
            [
                order.get("order_number"),
                order.get("id"),
                order.get("status"),
                order.get("design_name"),
                order.get("size_label"),
                _format_local(order.get("created_at")),
                _format_local(order.get("paid_at")),
                _format_local(order.get("ready_for_pickup_at")),
                _order_minutes(order),
            ]
        )
    return rows


def _models_sheet(orders: list[dict[str, Any]]) -> list[list[CellValue]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        groups[(str(order.get("design_name") or ""), str(order.get("size_label") or ""))].append(order)

    rows: list[list[CellValue]] = [["Модель", "Размер", "Заказов", "Оплачено", "Готово к выдаче", "Среднее минут до готовности"]]
    for (design_name, size_label), items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0][0], item[0][1])):
        ready_minutes = [value for value in (_order_minutes(order) for order in items) if value is not None]
        rows.append(
            [
                design_name,
                size_label,
                len(items),
                sum(1 for order in items if _is_paid(order)),
                sum(1 for order in items if _is_ready(order)),
                _average(ready_minutes),
            ]
        )
    return rows


def _hours_sheet(orders: list[dict[str, Any]]) -> list[list[CellValue]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        groups[_created_hour(order.get("created_at"))].append(order)

    rows: list[list[CellValue]] = [["Час заказа", "Заказов", "Оплачено", "Готово к выдаче", "Среднее минут до готовности"]]
    for hour, items in sorted(groups.items()):
        ready_minutes = [value for value in (_order_minutes(order) for order in items) if value is not None]
        rows.append(
            [
                hour,
                len(items),
                sum(1 for order in items if _is_paid(order)),
                sum(1 for order in items if _is_ready(order)),
                _average(ready_minutes),
            ]
        )
    return rows


def _summary_sheet(orders: list[dict[str, Any]]) -> list[list[CellValue]]:
    ready_minutes = [value for value in (_order_minutes(order) for order in orders) if value is not None]
    rows: list[list[CellValue]] = [
        ["Показатель", "Значение"],
        ["Дата генерации", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")],
        ["Всего заказов", len(orders)],
        ["Оплачено", sum(1 for order in orders if _is_paid(order))],
        ["Готово к выдаче", sum(1 for order in orders if _is_ready(order))],
        ["Среднее минут до готовности", _average(ready_minutes)],
        ["Минимум минут до готовности", min(ready_minutes) if ready_minutes else None],
        ["Максимум минут до готовности", max(ready_minutes) if ready_minutes else None],
    ]
    return rows


def build_statistics_workbook(orders: list[dict[str, Any]]) -> bytes:
    sheets: list[Sheet] = [
        ("Orders", _orders_sheet(orders)),
        ("Models", _models_sheet(orders)),
        ("By Hour", _hours_sheet(orders)),
        ("Summary", _summary_sheet(orders)),
    ]
    return _build_xlsx(sheets)


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell_xml(row_index: int, column_index: int, value: CellValue) -> str:
    ref = f"{_column_name(column_index)}{row_index}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value:g}</v></c>'
    text = "" if value is None else str(value)
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def _worksheet_xml(rows: list[list[CellValue]]) -> str:
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(_cell_xml(row_index, column_index, value) for column_index, value in enumerate(row, start=1))
        row_xml.append(f'<row r="{row_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )


def _content_types_xml(sheet_count: int) -> str:
    sheet_overrides = "".join(
        '<Override '
        f'PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{sheet_overrides}"
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheets: list[Sheet]) -> str:
    sheet_xml = "".join(
        f'<sheet name={quoteattr(name)} sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _rows) in enumerate(sheets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheet_xml}</sheets>"
        "</workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    sheet_rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{sheet_rels}"
        "</Relationships>"
    )


def _build_xlsx(sheets: list[Sheet]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(sheets)))
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml(sheets))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(sheets)))
        for index, (_name, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))
    return buffer.getvalue()
