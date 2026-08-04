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
    seed_printed_blank_batch,
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
        seed_printed_blank_batch(app)
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
        seed_printed_blank_batch(app)
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
        seed_printed_blank_batch(app)
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
    assert slicer.pause_heights == []
    assert len(slicer.overlay_safety) == 1
    assert slicer.overlay_safety[0].base_thickness_mm == order["thickness_mm"]
    assert order["id"] == order_id
    assert order["status"] == "printing"
    assert batch["status"] == "printing"
    assert Path(batch["gcode_path"]).exists()
    assert blocked_delete.status_code == 400


def test_print_blanks_prints_full_table_without_filament_change(tmp_path):
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
        response = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS)
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    payload = response.json()
    batch = batches[0]
    gcode_text = Path(batch["gcode_path"]).read_text(encoding="utf-8")

    assert response.status_code == 200
    assert payload["requested_count"] == 18
    assert payload["selected_count"] == 18
    assert len(payload["slots"]) == 18
    assert [slot["index"] for slot in payload["slots"]] == list(range(18))
    assert all(slot["state_code"] == 3 for slot in payload["slots"])
    assert payload["slots"][0]["y_mm"] == 13.0
    assert payload["slots"][0]["width_mm"] == 56.0
    assert payload["slots"][0]["height_mm"] == 24.0
    assert payload["blank_preview"]["state_counts"]["3"] == 18
    assert payload["blank_preview"]["empty_slots"] == 0
    assert payload["batch"]["kind"] == "blank"
    assert batch["id"] == payload["batch"]["id"]
    assert batch["status"] == "printing"
    assert batch["items"] == []
    assert slicer.blank_slices == [batch["id"]]
    assert slicer.pause_heights == []
    assert len(printer.uploads) == 1
    assert "@pause" not in gcode_text
    assert "M600" not in gcode_text


def test_print_blanks_target_count_selects_exactly_requested_slots(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        response = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS, json={"target_count": 10})

    payload = response.json()

    assert response.status_code == 200
    assert payload["requested_count"] == 10
    assert payload["selected_count"] == 10
    assert [slot["index"] for slot in payload["slots"]] == list(range(10))
    assert all(slot["state_code"] == 3 for slot in payload["slots"])
    assert payload["blank_preview"]["state_counts"]["3"] == 10
    assert payload["blank_preview"]["state_counts"]["1"] == 8
    assert len(printer.uploads) == 1


def test_print_blanks_uses_medium_startup_size_and_slot_count(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True, blank_size_id="medium")
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        response = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS)

    payload = response.json()

    assert response.status_code == 200
    assert payload["requested_count"] == 10
    assert payload["selected_count"] == 10
    assert len(payload["slots"]) == 10
    assert payload["blank_preview"]["blank_size_id"] == "standard"
    assert payload["blank_preview"]["blank_size_mm"] == [64.0, 30.0]
    assert payload["blank_preview"]["slot_count"] == 10
    assert payload["blank_preview"]["state_counts"]["3"] == 10
    assert [slot["index"] for slot in payload["slots"]] == list(range(10))
    assert payload["slots"][-1]["x_mm"] == 80.0
    assert payload["slots"][-1]["y_mm"] == 165.0
    assert len(printer.uploads) == 1


def test_print_blanks_target_count_fills_only_missing_empty_slots(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        blank_id = seed_printed_blank_batch(app)
        for slot_index in range(6, 18):
            patched = client.patch(
                f"/api/admin/blanks/{blank_id}/slots/{slot_index}",
                headers=ADMIN_HEADERS,
                json={"state_code": 1},
            )
            assert patched.status_code == 200
        response = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS, json={"target_count": 10})

    payload = response.json()

    assert response.status_code == 200
    assert payload["requested_count"] == 10
    assert payload["selected_count"] == 4
    assert [slot["index"] for slot in payload["slots"]] == [6, 7, 8, 9]
    assert payload["blank_preview"]["state_counts"]["4"] == 6
    assert payload["blank_preview"]["state_counts"]["3"] == 4
    assert payload["blank_preview"]["state_counts"]["1"] == 8
    assert len(printer.uploads) == 1


def test_print_blanks_target_count_at_or_below_occupied_is_noop(tmp_path):
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
        seed_printed_blank_batch(app, slot_count=6)
        response = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS, json={"target_count": 4})
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    payload = response.json()

    assert response.status_code == 200
    assert payload["selected_count"] == 0
    assert payload["slots"] == []
    assert "не запускались" in payload["message"]
    assert len(printer.uploads) == 0
    assert slicer.blank_slices == []
    assert len(batches) == 1
    assert batches[0]["status"] == "printed"


def test_blank_print_completion_changes_printing_slots_to_printed(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        response = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS, json={"target_count": 3})
        printer.job_result = AdapterResult(
            True,
            "fake completed job",
            extra={"state": "Operational", "progress": {"completion": 100, "printTime": 900, "printTimeLeft": 0}},
        )
        printer.printer_result = AdapterResult(
            True,
            "fake printer ready",
            extra={
                "state": {
                    "text": "Operational",
                    "flags": {
                        "operational": True,
                        "ready": True,
                        "printing": False,
                        "closedOrError": False,
                        "error": False,
                    },
                }
            },
        )
        status = client.get("/api/admin/printer/status", headers=ADMIN_HEADERS)
        preview = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["blank_preview"]

    assert response.status_code == 200
    assert status.json()["completed_prints"]["completed"] is True
    assert preview["state_counts"]["4"] == 3
    assert preview["state_counts"]["3"] == 0
    assert [slot["state_code"] for slot in preview["slots"][:3]] == [4, 4, 4]


def test_manual_blank_slot_state_updates_preview_and_blocks_active_printing(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        blank_id = seed_printed_blank_batch(app, slot_count=2)
        emptied = client.patch(
            f"/api/admin/blanks/{blank_id}/slots/0",
            headers=ADMIN_HEADERS,
            json={"state_code": 1},
        )
        stale_printing = client.patch(
            f"/api/admin/blanks/{blank_id}/slots/1",
            headers=ADMIN_HEADERS,
            json={"state_code": 6},
        )
        cleared = client.patch(
            f"/api/admin/blanks/{blank_id}/slots/1",
            headers=ADMIN_HEADERS,
            json={"state_code": 7},
        )
        preview_after_manual = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["blank_preview"]

        restored = client.patch(
            f"/api/admin/blanks/{blank_id}/slots/1",
            headers=ADMIN_HEADERS,
            json={"state_code": 4},
        )
        started = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS, json={"target_count": 2})
        blocked = client.patch(
            f"/api/admin/blanks/{blank_id}/slots/0",
            headers=ADMIN_HEADERS,
            json={"state_code": 1},
        )

    assert emptied.status_code == 200
    assert stale_printing.status_code == 200
    assert cleared.status_code == 200
    assert preview_after_manual["slots"][0]["state_code"] == 1
    assert preview_after_manual["slots"][1]["state_code"] == 7
    assert preview_after_manual["state_counts"]["1"] == 1
    assert preview_after_manual["state_counts"]["7"] == 1
    assert restored.status_code == 200
    assert started.status_code == 200
    assert started.json()["selected_count"] == 1
    assert blocked.status_code == 400
    assert "printing" in blocked.json()["detail"]


def test_print_blanks_requires_connected_printer_without_creating_batch(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    disconnected_printer = FakePrinter(
        settings,
        AdapterResult(True, "fake job", extra={"state": "Operational", "progress": {}}),
        AdapterResult(True, "fake printer"),
        connection_result=AdapterResult(False, "OctoPrint state is Closed"),
    )
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=disconnected_printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        response = client.post("/api/admin/blanks/print", headers=ADMIN_HEADERS)
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    assert response.status_code == 400
    assert "Printer is not ready" in response.json()["detail"]
    assert disconnected_printer.uploads == []
    assert batches == []


def test_run_queue_prints_overlay_batches_one_at_a_time_with_delay(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        first_order_id = create_paid_order(client, "autorun-overlay-first")
        second_order_id = create_paid_order(client, "autorun-overlay-second")

        run = client.post("/api/admin/print-queue/run", headers=ADMIN_HEADERS)
        first_started_batch = run.json()["started"]["batch"]
        batches_after_start = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

        printer.job_result = AdapterResult(
            True,
            "fake completed job",
            extra={"state": "Operational", "progress": {"completion": 100, "printTime": 120, "printTimeLeft": 0}},
        )
        printer.printer_result = AdapterResult(
            True,
            "fake printer ready",
            extra={
                "state": {
                    "text": "Operational",
                    "flags": {
                        "operational": True,
                        "ready": True,
                        "printing": False,
                        "closedOrError": False,
                        "error": False,
                    },
                }
            },
        )
        handoff = client.get("/api/admin/printer/status", headers=ADMIN_HEADERS)

        app.state.service._overlay_next_start_at = 0.0
        second_start = client.get("/api/admin/printer/status", headers=ADMIN_HEADERS)
        orders = {order["id"]: order for order in client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]}
        batches_after_second = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    first_order_in_batch = first_started_batch["items"][0]["order_id"]
    queued_after_start = [batch for batch in batches_after_start if batch["kind"] == "overlay" and batch["status"] == "queued"]
    printing_after_second = [
        batch for batch in batches_after_second if batch["kind"] == "overlay" and batch["status"] == "printing"
    ]

    assert run.status_code == 200
    assert run.json()["started"]["started"] is True
    assert first_order_in_batch == first_order_id
    assert len(printer.uploads) == 2
    assert len(queued_after_start) == 1
    assert handoff.json()["completed_prints"]["orders_updated"] == 1
    assert handoff.json()["overlay_autorun"]["reason"] == "handoff_delay"
    assert second_start.json()["overlay_autorun"]["started"] is True
    assert orders[first_order_id]["status"] == "printed"
    assert orders[second_order_id]["status"] == "printing"
    assert len(printing_after_second) == 1
    assert printing_after_second[0]["items"][0]["order_id"] == second_order_id


def test_prepare_can_append_paid_overlay_order_while_printing(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        first_order_id = create_paid_order(client, "append-while-printing-first", customer_name="First")
        run = client.post("/api/admin/print-queue/run", headers=ADMIN_HEADERS)

        second_order_id = create_paid_order(client, "append-while-printing-second", customer_name="Second")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        payload = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()

    batches = payload["batches"]
    preview = payload["blank_preview"]
    printing_batches = [batch for batch in batches if batch["kind"] == "overlay" and batch["status"] == "printing"]
    queued_batches = [batch for batch in batches if batch["kind"] == "overlay" and batch["status"] == "queued"]
    assignments = [slot["assignment"] for slot in preview["slots"] if slot["assigned"]]

    assert run.status_code == 200
    assert prepared["batching"]["batches_created"] == 1
    assert len(printing_batches) == 1
    assert len(queued_batches) == 1
    assert printing_batches[0]["items"][0]["order_id"] == first_order_id
    assert queued_batches[0]["items"][0]["order_id"] == second_order_id
    assert preview["assigned_slots"] == 2
    assert preview["free_slots"] == 16
    assert preview["state_counts"]["5"] == 1
    assert preview["state_counts"]["6"] == 1
    assert {assignment["order_id"] for assignment in assignments} == {first_order_id, second_order_id}
    assert {assignment["batch_status"] for assignment in assignments} == {"printing", "queued"}


def test_done_printer_status_completes_printing_batch_and_allows_delete(tmp_path):
    settings = make_settings(tmp_path, slicer_enabled=True, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(
        settings,
        slicer=FakeSlicer(settings),
        printer=printer,
        notifier=NullNotifier(),
    )

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        order_id = create_paid_order(client, "completed-print-order")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = prepared["batching"]["batches"][0]["id"]
        started = client.post(f"/api/admin/batches/{batch_id}/print", headers=ADMIN_HEADERS)

        printer.job_result = AdapterResult(
            True,
            "fake completed job",
            extra={"state": "Operational", "progress": {"completion": 100, "printTime": 900, "printTimeLeft": 0}},
        )
        printer.printer_result = AdapterResult(
            True,
            "fake printer ready",
            extra={
                "state": {
                    "text": "Operational",
                    "flags": {
                        "operational": True,
                        "ready": True,
                        "printing": False,
                        "closedOrError": False,
                        "error": False,
                    },
                }
            },
        )

        status = client.get("/api/admin/printer/status", headers=ADMIN_HEADERS)
        order = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"][0]
        payload = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()
        batch = payload["batches"][0]
        preview = payload["blank_preview"]
        deleted = client.delete(f"/api/admin/orders/{order_id}", headers=ADMIN_HEADERS)

    assert started.status_code == 200
    assert status.status_code == 200
    assert status.json()["state"] == "done_printing"
    assert status.json()["completed_prints"]["completed"] is True
    assert status.json()["completed_prints"]["orders_updated"] == 1
    assert order["status"] == "printed"
    assert batch["id"] == batch_id
    assert batch["status"] == "printed"
    assert preview["slots"][0]["state_code"] == 7
    assert preview["state_counts"]["7"] == 1
    assert deleted.status_code == 200
    assert deleted.json()["order"]["archived_at"] is not None


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
        seed_printed_blank_batch(app)
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
