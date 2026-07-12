from pathlib import Path

from fastapi.testclient import TestClient

from keychain_fair.adapters import AdapterResult, CuraEngineSlicer, NullNotifier
from tests.support import (
    ADMIN_HEADERS,
    FakePrinter,
    FakeSlicer,
    create_paid_order,
    create_test_app,
    make_settings,
    ready_printer,
)


def test_print_batch_requires_ready_printer(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    offline_printer = FakePrinter(
        settings,
        AdapterResult(True, "fake job", extra={"state": "Operational"}),
        AdapterResult(False, "printer offline"),
    )
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=offline_printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        create_paid_order(client, "offline-printer-order")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = prepared["batching"]["batches"][0]["id"]
        response = client.post(f"/api/admin/batches/{batch_id}/print", headers=ADMIN_HEADERS)

    assert response.status_code == 400
    assert "Printer is not ready" in response.json()["detail"]
    assert offline_printer.uploads == []


def test_print_batch_requires_slicer_or_existing_gcode(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=False, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=CuraEngineSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        create_paid_order(client, "slicer-disabled-order")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = prepared["batching"]["batches"][0]["id"]
        response = client.post(f"/api/admin/batches/{batch_id}/print", headers=ADMIN_HEADERS)

    assert response.status_code == 400
    assert "Slicing is disabled" in response.json()["detail"]
    assert printer.uploads == []


def test_print_batch_slices_and_uploads_only_after_operator_confirmation(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    slicer = FakeSlicer(settings)
    app = create_test_app(
        settings,
        slicer=slicer,
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        order_id = create_paid_order(client, "confirmed-print-order")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = prepared["batching"]["batches"][0]["id"]

        batch_before_print = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"][0]
        assert batch_before_print["gcode_path"] is None
        assert printer.uploads == []

        response = client.post(f"/api/admin/batches/{batch_id}/print", headers=ADMIN_HEADERS)
        order = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"][0]
        batch = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"][0]
        blocked_delete = client.delete(f"/api/admin/orders/{order_id}", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert len(printer.uploads) == 1
    assert slicer.pause_heights == [order["thickness_mm"]]
    assert order["id"] == order_id
    assert order["status"] == "printing"
    assert batch["status"] == "printing"
    assert Path(batch["gcode_path"]).exists()
    assert blocked_delete.status_code == 400


def test_admin_bed_controls_send_targets(tmp_path):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(settings, printer=printer)

    with TestClient(app) as client:
        heat = client.post("/api/admin/printer/bed/heat", headers=ADMIN_HEADERS)
        cool = client.post("/api/admin/printer/bed/cool", headers=ADMIN_HEADERS)

    assert heat.status_code == 200
    assert cool.status_code == 200
    assert printer.bed_targets == [60, 0]
    assert printer.tool_targets == [("tool0", 200), ("tool0", 0)]


def test_admin_resume_print_sends_octoprint_resume(tmp_path):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(settings, printer=printer)

    with TestClient(app) as client:
        response = client.post("/api/admin/printer/resume", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert printer.resume_calls == 1
    assert response.json()["message"] == "fake resumed"


def test_stop_print_cancels_job_turns_bed_off_and_requeues_orders(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        order_id = create_paid_order(client, "stop-print-order")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = prepared["batching"]["batches"][0]["id"]
        started = client.post(f"/api/admin/batches/{batch_id}/print", headers=ADMIN_HEADERS)
        stopped = client.post("/api/admin/printer/stop", headers=ADMIN_HEADERS)
        order = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"][0]
        batch = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"][0]

    assert started.status_code == 200
    assert stopped.status_code == 200
    assert printer.cancel_calls == 1
    assert printer.bed_targets == [0]
    assert stopped.json()["orders_updated"] == 1
    assert order["id"] == order_id
    assert order["status"] == "queued"
    assert batch["id"] == batch_id
    assert batch["status"] == "queued"
