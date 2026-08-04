from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from tests.support import ADMIN_HEADERS, create_test_app, create_unpaid_order, make_settings


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def worksheet_rows(workbook: bytes, path: str) -> list[list[str]]:
    with ZipFile(BytesIO(workbook)) as archive:
        root = ET.fromstring(archive.read(path))

    rows: list[list[str]] = []
    for row in root.findall("x:sheetData/x:row", NS):
        values: list[str] = []
        for cell in row.findall("x:c", NS):
            text = cell.find("x:is/x:t", NS)
            number = cell.find("x:v", NS)
            values.append((text.text if text is not None else number.text if number is not None else "") or "")
        rows.append(values)
    return rows


def test_ready_for_pickup_timer_is_recorded(tmp_path, monkeypatch):
    times = iter(
        [
            "2026-07-12T09:00:00+00:00",
            "2026-07-12T09:10:00+00:00",
            "2026-07-12T10:40:30+00:00",
        ]
    )
    monkeypatch.setattr("keychain_fair.database.now_iso", lambda: next(times))
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        order_id = create_unpaid_order(client, "ready-timer")
        paid = client.post(f"/api/admin/orders/{order_id}/status", headers=ADMIN_HEADERS, json={"status": "paid"})
        ready = client.post(f"/api/admin/orders/{order_id}/status", headers=ADMIN_HEADERS, json={"status": "ready_for_pickup"})
        public_order = client.get(f"/api/orders/{order_id}").json()["order"]

    ready_order = ready.json()["order"]
    assert paid.status_code == 200
    assert ready.status_code == 200
    assert ready_order["paid_at"] == "2026-07-12T09:10:00+00:00"
    assert ready_order["ready_for_pickup_at"] == "2026-07-12T10:40:30+00:00"
    assert ready_order["paid_to_ready_seconds"] == 5430
    assert public_order["paid_to_ready_seconds"] == 5430


def test_marking_unpaid_clears_payment_and_ready_timer(tmp_path, monkeypatch):
    times = iter(
        [
            "2026-07-12T09:00:00+00:00",
            "2026-07-12T09:10:00+00:00",
            "2026-07-12T10:00:00+00:00",
            "2026-07-12T11:00:00+00:00",
        ]
    )
    monkeypatch.setattr("keychain_fair.database.now_iso", lambda: next(times))
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        order_id = create_unpaid_order(client, "clear-ready-timer")
        client.post(f"/api/admin/orders/{order_id}/status", headers=ADMIN_HEADERS, json={"status": "paid"})
        client.post(f"/api/admin/orders/{order_id}/status", headers=ADMIN_HEADERS, json={"status": "ready_for_pickup"})
        unpaid = client.post(f"/api/admin/orders/{order_id}/status", headers=ADMIN_HEADERS, json={"status": "unpaid"})

    order = unpaid.json()["order"]
    assert unpaid.status_code == 200
    assert order["paid_at"] is None
    assert order["ready_for_pickup_at"] is None
    assert order["paid_to_ready_seconds"] is None


def test_admin_statistics_xlsx_requires_pin_and_contains_expected_sheets(tmp_path, monkeypatch):
    times = iter(
        [
            "2026-07-12T09:00:00+00:00",
            "2026-07-12T09:05:00+00:00",
            "2026-07-12T09:35:00+00:00",
            "2026-07-12T10:00:00+00:00",
        ]
    )
    monkeypatch.setattr("keychain_fair.database.now_iso", lambda: next(times))
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        protected = client.get("/api/admin/statistics.xlsx")
        ready_id = create_unpaid_order(client, "statistics-ready")
        client.post(f"/api/admin/orders/{ready_id}/status", headers=ADMIN_HEADERS, json={"status": "paid"})
        client.post(f"/api/admin/orders/{ready_id}/status", headers=ADMIN_HEADERS, json={"status": "ready_for_pickup"})
        unpaid_id = create_unpaid_order(client, "statistics-unpaid")
        response = client.get("/api/admin/statistics.xlsx", headers=ADMIN_HEADERS)

    assert protected.status_code == 401
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"] == 'attachment; filename="keychain-fair-statistics.xlsx"'

    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")

    assert {"xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml", "xl/worksheets/sheet4.xml"} <= names
    assert 'name="Orders"' in workbook_xml
    assert 'name="Models"' in workbook_xml
    assert 'name="By Hour"' in workbook_xml
    assert 'name="Summary"' in workbook_xml

    orders_rows = worksheet_rows(response.content, "xl/worksheets/sheet1.xml")
    model_rows = worksheet_rows(response.content, "xl/worksheets/sheet2.xml")
    hour_rows = worksheet_rows(response.content, "xl/worksheets/sheet3.xml")

    assert any(ready_id in row for row in orders_rows)
    assert any(unpaid_id in row for row in orders_rows)
    assert ["Order #", "ID"] == orders_rows[0][:2]
    assert {"001", "002"} <= {row[0] for row in orders_rows[1:]}
    assert model_rows[1][2] == "2"
    assert len(hour_rows) == 3
