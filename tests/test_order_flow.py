from dataclasses import replace
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keychain_fair.adapters import CuraEngineSlicer, NullNotifier, OctoPrintController
from tests.support import (
    ADMIN_HEADERS,
    create_paid_order,
    create_test_app,
    create_unpaid_order,
    make_settings,
    order_payload,
    ready_printer,
    seed_printed_blank_batch,
)


def queue_app(settings):
    return create_test_app(
        settings,
        slicer=CuraEngineSlicer(settings),
        printer=OctoPrintController(settings),
        notifier=NullNotifier(),
    )


def test_startup_attempts_printer_connection(tmp_path):
    settings = make_settings(tmp_path, octoprint_enabled=True)
    printer = ready_printer(settings)
    app = create_test_app(settings, printer=printer)

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert printer.ensure_calls == 1


def test_prepare_recovers_paid_order_marked_printing_before_generation(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        order_id = create_paid_order(client, "printing-before-generation")
        app.state.database.update_order_status(order_id, "printing")
        seed_printed_blank_batch(app)

        queue = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS)
        order = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"][0]

    assert queue.status_code == 200
    assert queue.json()["recovered_orders"]["order_ids"] == [order_id]
    assert queue.json()["generation"]["generated"] == 1
    assert queue.json()["batching"]["batches_created"] == 1
    assert order["status"] == "queued"
    assert order["batch_id"] is not None
    assert Path(order["stl_path"]).exists()


def test_admin_cannot_manually_mark_order_printing(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        order_id = create_paid_order(client, "manual-printing-blocked")
        response = client.post(
            f"/api/admin/orders/{order_id}/status",
            headers=ADMIN_HEADERS,
            json={"status": "printing"},
        )

    assert response.status_code == 422
    assert "can not be set manually" in json.dumps(response.json())


def test_order_flow_prepares_stl_and_batch_without_printing(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        assert client.get("/api/designs").status_code == 200
        order_id = create_paid_order(client)

        duplicate = client.post("/api/orders", json=order_payload(idempotency_key="test-order-flow-1"))
        assert duplicate.status_code == 201
        assert duplicate.json()["order"]["id"] == order_id
        seed_printed_blank_batch(app)

        queue = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS)
        assert queue.status_code == 200
        assert queue.json()["generation"]["generated"] == 1
        assert queue.json()["batching"]["batches_created"] == 1

        public_current = client.get(f"/api/orders/{order_id}").json()["order"]
        current_orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]

    assert "stl_path" not in public_current
    assert len(current_orders) == 1
    current = current_orders[0]
    assert current["status"] == "queued"
    assert Path(current["stl_path"]).exists()
    assert current["gcode_path"] is None


def test_order_payloads_do_not_expose_phone(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        order_response = client.post("/api/orders", json=order_payload(idempotency_key="no-phone-order"))
        assert order_response.status_code == 201
        order_id = order_response.json()["order"]["id"]

        public_order = client.get(f"/api/orders/{order_id}").json()["order"]
        admin_order = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"][0]

    assert "phone" not in order_response.json()["order"]
    assert "phone" not in public_order
    assert "phone" not in admin_order


def test_orders_get_readable_order_numbers(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        first = client.post("/api/orders", json=order_payload(idempotency_key="readable-number-1"))
        duplicate = client.post("/api/orders", json=order_payload(idempotency_key="readable-number-1"))
        second = client.post("/api/orders", json=order_payload(idempotency_key="readable-number-2"))
        public_first = client.get(f"/api/orders/{first.json()['order']['id']}").json()["order"]
        admin_orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["order"]["order_number"] == "001"
    assert duplicate.json()["order"]["order_number"] == "001"
    assert second.json()["order"]["order_number"] == "002"
    assert public_first["order_number"] == "001"
    assert {order["order_number"] for order in admin_orders} == {"001", "002"}


def test_prepare_adds_new_overlay_batch_without_rebuilding_existing_queue(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        first_order_id = create_paid_order(client, "first-queued-order", customer_name="First")
        first_prepare = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        old_batch_id = first_prepare["batching"]["batches"][0]["id"]

        second_order_id = create_paid_order(client, "second-queued-order", customer_name="Second")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    active_batches = [batch for batch in batches if batch["status"] == "queued"]
    old_batch = next(batch for batch in batches if batch["id"] == old_batch_id)

    assert prepared["rebuilt_queued_batches"]["batch_ids"] == []
    assert old_batch["status"] == "queued"
    assert len(active_batches) == 2
    order_ids_by_batch = sorted([item["order_id"] for item in batch["items"]] for batch in active_batches)
    assert order_ids_by_batch == sorted([[first_order_id], [second_order_id]])
    assert all(batch["kind"] == "overlay" for batch in active_batches)


def test_admin_batches_preview_shows_planned_blank_table(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        preview = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["blank_preview"]

    assert preview["state"] == "planned"
    assert preview["slot_count"] == 18
    assert preview["assigned_slots"] == 0
    assert preview["free_slots"] == 0
    assert preview["empty_slots"] == 18
    assert preview["state_counts"]["1"] == 18
    assert preview["can_accept_orders"] is False
    assert preview["needs_new_blanks"] is False
    assert [slot["index"] for slot in preview["slots"]] == list(range(18))
    assert preview["blank_size_mm"] == [56.0, 24.0]
    assert preview["slots"][0]["y_mm"] == 13.0
    assert preview["slots"][-1]["x_mm"] == 136.0
    assert preview["slots"][-1]["y_mm"] == 173.0
    assert all(slot["state_code"] == 1 for slot in preview["slots"])
    assert all(slot["assigned"] is False for slot in preview["slots"])


def test_admin_batches_preview_recalculates_blank_table_for_medium_startup_size(tmp_path):
    settings = make_settings(tmp_path, blank_size_id="medium")
    app = queue_app(settings)

    with TestClient(app) as client:
        preview = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["blank_preview"]

    assert settings.queue.blank_size_id == "standard"
    assert preview["state"] == "planned"
    assert preview["blank_size_id"] == "standard"
    assert preview["blank_size_label"] == "Стандартный"
    assert preview["blank_size_mm"] == [64.0, 30.0]
    assert preview["slot_count"] == 10
    assert preview["empty_slots"] == 10
    assert preview["state_counts"]["1"] == 10
    assert [slot["index"] for slot in preview["slots"]] == list(range(10))
    assert preview["slots"][0]["y_mm"] == 13.0
    assert preview["slots"][-1]["x_mm"] == 80.0
    assert preview["slots"][-1]["y_mm"] == 165.0


def test_blank_size_setting_rejects_unknown_size(tmp_path):
    assert make_settings(tmp_path, blank_size_id="standard").queue.blank_size_id == "standard"
    with pytest.raises(ValueError, match="blank_size_id"):
        make_settings(tmp_path, blank_size_id="large")


def test_cleared_blank_table_uses_configured_startup_size_for_new_plan(tmp_path):
    settings = make_settings(tmp_path, blank_size_id="medium")
    app = queue_app(settings)

    with TestClient(app) as client:
        blank_id = seed_printed_blank_batch(app)
        for slot_index in range(18):
            response = client.patch(
                f"/api/admin/blanks/{blank_id}/slots/{slot_index}",
                headers=ADMIN_HEADERS,
                json={"state_code": 1},
            )
            assert response.status_code == 200
        preview = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["blank_preview"]

    assert preview["active_blank_batch"] is None
    assert preview["blank_size_id"] == "standard"
    assert preview["blank_size_mm"] == [64.0, 30.0]
    assert preview["slot_count"] == 10
    assert preview["empty_slots"] == 10


def test_admin_batches_preview_marks_assigned_overlay_order(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        blank_id = seed_printed_blank_batch(app)
        order_id = create_paid_order(
            client,
            "preview-assigned-overlay-order",
            customer_name="Preview Customer",
            print_line_1="PREVIEW",
            print_line_2="ORDER",
        )
        client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS)
        preview = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["blank_preview"]

    assigned_slot = preview["slots"][0]

    assert preview["state"] == "active"
    assert preview["active_blank_batch"]["id"] == blank_id
    assert preview["assigned_slots"] == 1
    assert preview["free_slots"] == 17
    assert preview["can_accept_orders"] is True
    assert assigned_slot["assigned"] is True
    assert assigned_slot["state_code"] == 5
    assert assigned_slot["state"] == "overlay_queued"
    assert assigned_slot["assignment"]["order_id"] == order_id
    assert assigned_slot["assignment"]["customer_name"] == "Preview Customer"
    assert assigned_slot["assignment"]["print_line_1"] == "PREVIEW ORDER"
    assert assigned_slot["assignment"]["print_line_2"] == ""


def test_prepare_keeps_order_waiting_when_size_does_not_match_blank_table(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        order_id = create_paid_order(client, "medium-order-on-compact-blanks", size_id="standard")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        payload = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()
        orders = {order["id"]: order for order in client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]}

    overlay_batches = [batch for batch in payload["batches"] if batch["kind"] == "overlay"]
    diagnostics = " ".join(prepared["batching"]["diagnostics"])

    assert prepared["batching"]["batches_created"] == 0
    assert overlay_batches == []
    assert orders[order_id]["status"] == "stl_ready"
    assert "does not match available blank slots" in diagnostics


def test_prepare_skips_disabled_blank_slots(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        blank_id = seed_printed_blank_batch(app, slot_count=3)
        disabled = client.patch(
            f"/api/admin/blanks/{blank_id}/slots/1",
            headers=ADMIN_HEADERS,
            json={"print_enabled": False},
        )
        order_ids = [
            create_paid_order(client, f"skip-disabled-slot-{index}", customer_name=name)
            for index, name in enumerate(["Alpha", "Beta"])
        ]
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        payload = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()

    overlay_batches = [batch for batch in payload["batches"] if batch["kind"] == "overlay" and batch["status"] != "error"]
    preview = payload["blank_preview"]

    assert disabled.status_code == 200
    assert prepared["batching"]["batches_created"] == 2
    assert sorted(batch["slot_index"] for batch in overlay_batches) == [0, 2]
    assert {batch["items"][0]["order_id"] for batch in overlay_batches} == set(order_ids)
    assert preview["disabled_slots"] == 1
    assert preview["state_counts"]["7"] == 1
    assert preview["slots"][1]["print_enabled"] is False
    assert preview["slots"][1]["state_code"] == 7
    assert preview["slots"][1]["assigned"] is False


def test_disabling_queued_blank_slot_releases_order(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        blank_id = seed_printed_blank_batch(app, slot_count=2)
        order_id = create_paid_order(client, "disable-assigned-slot")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = prepared["batching"]["batches"][0]["id"]
        disabled = client.patch(
            f"/api/admin/blanks/{blank_id}/slots/0",
            headers=ADMIN_HEADERS,
            json={"print_enabled": False},
        )
        orders = {order["id"]: order for order in client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]}
        batches = {batch["id"]: batch for batch in client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]}
        preview = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["blank_preview"]

    assert disabled.status_code == 200
    assert orders[order_id]["status"] == "stl_ready"
    assert orders[order_id]["batch_id"] is None
    assert batches[batch_id]["status"] == "error"
    assert preview["slots"][0]["print_enabled"] is False
    assert preview["slots"][0]["state_code"] == 7
    assert preview["slots"][0]["assigned"] is False


def test_prepare_creates_five_sequential_overlay_batches_for_five_paid_orders(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        order_ids = [
            create_paid_order(client, f"five-overlay-orders-{index}", customer_name=f"Customer {letter}")
            for index, letter in enumerate(["Alpha", "Beta", "Gamma", "Delta", "Epsilon"])
        ]
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    overlay_batches = [batch for batch in batches if batch["kind"] == "overlay"]
    queued_order_ids = {batch["items"][0]["order_id"] for batch in overlay_batches}

    assert prepared["batching"]["batches_created"] == 5
    assert len(overlay_batches) == 5
    assert queued_order_ids == set(order_ids)
    assert sorted(batch["slot_index"] for batch in overlay_batches) == [0, 1, 2, 3, 4]
    assert all(batch["status"] == "queued" for batch in overlay_batches)
    assert all(len(batch["items"]) == 1 for batch in overlay_batches)


def test_prepare_stops_assigning_orders_when_blank_table_is_full(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        seed_printed_blank_batch(app, slot_count=2)
        order_ids = [
            create_paid_order(client, f"full-blank-table-order-{index}", customer_name=name)
            for index, name in enumerate(["Alpha", "Beta", "Gamma"])
        ]
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        second_prepare = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        payload = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()
        orders = {order["id"]: order for order in client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]}

    preview = payload["blank_preview"]
    overlay_batches = [batch for batch in payload["batches"] if batch["kind"] == "overlay" and batch["status"] != "error"]
    assigned_order_ids = {batch["items"][0]["order_id"] for batch in overlay_batches}
    waiting_order_id = next(order_id for order_id in order_ids if order_id not in assigned_order_ids)

    assert prepared["batching"]["batches_created"] == 2
    assert second_prepare["batching"]["batches_created"] == 0
    assert "no free overlay slots" in " ".join(second_prepare["batching"]["diagnostics"])
    assert len(overlay_batches) == 2
    assert preview["state"] == "full"
    assert preview["assigned_slots"] == 2
    assert preview["free_slots"] == 0
    assert preview["needs_new_blanks"] is True
    assert orders[waiting_order_id]["status"] == "stl_ready"


def test_prepare_does_not_rebuild_queued_batch_without_new_ready_orders(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        order_id = create_paid_order(client, "stable-queued-order")
        first_prepare = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        first_batch_id = first_prepare["batching"]["batches"][0]["id"]

        second_prepare = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    active_batches = [batch for batch in batches if batch["status"] == "queued"]

    assert second_prepare["rebuilt_queued_batches"]["batch_ids"] == []
    assert second_prepare["batching"]["batches_created"] == 0
    assert len(active_batches) == 1
    assert active_batches[0]["id"] == first_batch_id
    assert [item["order_id"] for item in active_batches[0]["items"]] == [order_id]


def test_internal_order_requires_token_and_stores_telegram_metadata(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)
    payload = {
        **order_payload(idempotency_key="tg:42:draft1"),
        "source": "telegram",
        "telegram_chat_id": "42",
        "telegram_user_id": "7",
        "telegram_username": "anna",
    }

    with TestClient(app) as client:
        blocked = client.post("/api/internal/orders", json=payload)
        created = client.post(
            "/api/internal/orders",
            headers={"X-Internal-Token": "test-internal-token"},
            json=payload,
        )
        duplicate = client.post(
            "/api/internal/orders",
            headers={"X-Internal-Token": "test-internal-token"},
            json=payload,
        )
        admin_orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]

    assert blocked.status_code == 401
    assert created.status_code == 201
    assert duplicate.status_code == 201
    assert duplicate.json()["order"]["id"] == created.json()["order"]["id"]
    assert "telegram_chat_id" not in created.json()["order"]
    order = admin_orders[0]
    assert order["source"] == "telegram"
    assert order["telegram_chat_id"] == "42"
    assert order["telegram_user_id"] == "7"
    assert order["telegram_username"] == "anna"


def test_custom_order_keeps_one_short_print_line(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json=order_payload(
                design_id="classic_plate",
                size_id="standard",
                print_line_1="PAPA",
                print_line_2="",
                idempotency_key="custom-one-line",
            ),
        )
        order = response.json()["order"]

    assert response.status_code == 201
    assert order["print_line_1"] == "PAPA"
    assert order["print_line_2"] == ""


def test_custom_order_keeps_one_long_print_line_for_model_layout(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
                json=order_payload(
                    design_id="classic_plate",
                    size_id="standard",
                    print_line_1="LONG WORDS",
                    print_line_2="",
                    idempotency_key="custom-long-line",
                ),
        )
        order = response.json()["order"]

    assert response.status_code == 201
    assert order["print_line_1"] == "LONG WORDS"
    assert order["print_line_2"] == ""


def test_custom_order_combines_legacy_second_print_line(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json=order_payload(
                design_id="classic_plate",
                size_id="standard",
                print_line_1="1234 AB-7",
                print_line_2="+375291234567",
                idempotency_key="custom-two-lines",
            ),
        )
        order = response.json()["order"]

    assert response.status_code == 201
    assert order["print_line_1"] == "1234 AB-7 +375291234567"
    assert order["print_line_2"] == ""


def test_custom_order_rejects_text_over_size_limit(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
                json=order_payload(
                    design_id="classic_plate",
                    size_id="standard",
                    print_line_1="X" * 29,
                    idempotency_key="custom-too-long",
                ),
            )

    assert response.status_code == 400
    assert "Maximum length is 28" in response.json()["detail"]


def test_web_user_can_have_only_two_unpaid_orders_until_payment(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        first = client.post("/api/orders", json=order_payload(idempotency_key="quota-01", client_id="browser-1"))
        second = client.post("/api/orders", json=order_payload(idempotency_key="quota-02", client_id="browser-1"))
        third = client.post("/api/orders", json=order_payload(idempotency_key="quota-03", client_id="browser-1"))

        paid = client.post(
            f"/api/admin/orders/{first.json()['order']['id']}/status",
            headers=ADMIN_HEADERS,
            json={"status": "paid"},
        )
        fourth = client.post("/api/orders", json=order_payload(idempotency_key="quota-04", client_id="browser-1"))

    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 429
    assert "only 2 unpaid orders" in third.json()["detail"]["message"]
    assert paid.status_code == 200
    assert fourth.status_code == 201


def test_profanity_blocks_order_and_mutes_user(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        blocked = client.post(
            "/api/orders",
            json=order_payload(
                customer_name="\u0410\u043d\u043d\u0430 \u0445\u0443\u0439",
                idempotency_key="bad-language-1",
                client_id="browser-bad",
            ),
        )
        muted = client.post(
            "/api/orders",
            json=order_payload(idempotency_key="bad-language-2", client_id="browser-bad"),
        )
        orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]

    assert blocked.status_code == 429
    assert blocked.json()["detail"]["retry_after_seconds"] == 300
    assert muted.status_code == 429
    assert muted.json()["detail"]["retry_after_seconds"] > 0
    assert orders == []


def test_unpaid_order_is_not_prepared_or_placed_on_bed(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        order_id = create_unpaid_order(client, "fresh-unpaid-order")
        queue = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS)
        orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    assert queue.status_code == 200
    assert queue.json()["generation"]["generated"] == 0
    assert queue.json()["batching"]["batches_created"] == 0
    assert batches == []
    order = next(item for item in orders if item["id"] == order_id)
    assert order["status"] == "unpaid"
    assert order["batch_id"] is None
    assert order["stl_path"] is None


def test_marking_queued_order_unpaid_invalidates_bed_and_rebuilds_without_it(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        unpaid_id = create_paid_order(client, "will-become-unpaid")
        paid_id = create_paid_order(client, "stays-paid")
        first = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        old_batch_id = next(
            batch["id"]
            for batch in first["batching"]["batches"]
            if batch["items"][0]["order_id"] == unpaid_id
        )

        unpaid = client.post(
            f"/api/admin/orders/{unpaid_id}/status",
            headers=ADMIN_HEADERS,
            json={"status": "unpaid"},
        )
        rebuilt = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    active_batches = [item for item in batches if item["status"] == "queued"]
    old_batch = next(item for item in batches if item["id"] == old_batch_id)
    by_id = {order["id"]: order for order in orders}

    assert unpaid.status_code == 200
    assert unpaid.json()["order"]["status"] == "unpaid"
    assert unpaid.json()["order"]["batch_id"] is None
    assert old_batch["status"] == "error"
    assert rebuilt["batching"]["batches_created"] == 0
    assert len(active_batches) == 1
    assert [item["order_id"] for item in active_batches[0]["items"]] == [paid_id]
    assert all(item["status"] != "unpaid" for item in active_batches[0]["items"])
    assert by_id[unpaid_id]["status"] == "unpaid"
    assert by_id[paid_id]["status"] == "queued"


def test_delete_order_archives_and_removes_from_active_bed(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        seed_printed_blank_batch(app)
        deleted_order_id = create_paid_order(client, "delete-removes-from-bed")
        remaining_order_id = create_paid_order(client, "delete-keeps-other-orders")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = next(
            batch["id"]
            for batch in prepared["batching"]["batches"]
            if batch["items"][0]["order_id"] == deleted_order_id
        )

        deleted = client.delete(f"/api/admin/orders/{deleted_order_id}", headers=ADMIN_HEADERS)
        assert deleted.status_code == 200
        assert deleted.json()["order"]["archived_at"] is not None
        assert deleted.json()["order"]["batch_id"] is None

        active_orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]
        rebuilt = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        rebuilt_batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    assert [order["id"] for order in active_orders] == [remaining_order_id]
    assert active_orders[0]["status"] == "queued"
    assert active_orders[0]["batch_id"] is not None
    batch = next(item for item in batches if item["id"] == batch_id)
    assert batch["status"] == "error"
    assert batch["items"][0]["order_id"] == deleted_order_id
    assert batch["items"][0]["archived_at"] is not None
    assert rebuilt["batching"]["batches_created"] == 0
    active_batches = [item for item in rebuilt_batches if item["status"] == "queued"]
    assert len(active_batches) == 1
    assert [item["order_id"] for item in active_batches[0]["items"]] == [remaining_order_id]
    assert all(item["order_id"] != deleted_order_id for item in active_batches[0]["items"])


def test_admin_pin_required(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.get("/api/admin/orders")

    assert response.status_code == 401


def test_system_info_prefers_lan_address_for_qr(tmp_path, monkeypatch):
    monkeypatch.setattr("keychain_fair.main.local_ipv4_addresses", lambda: ["192.168.0.50", "127.0.0.1"])
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.get("/api/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["site_url"] == "http://192.168.0.50:8080/"
    assert payload["admin_url"] == "http://192.168.0.50:8080/admin"
    assert "http://127.0.0.1:8080/" in payload["fallback_urls"]


def test_system_info_uses_public_url_for_stable_qr(tmp_path, monkeypatch):
    monkeypatch.setattr("keychain_fair.main.local_ipv4_addresses", lambda: ["192.168.0.50", "127.0.0.1"])
    settings = replace(make_settings(tmp_path), public_url="http://192.168.137.1:8080")
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.get("/api/system/info")

    assert response.status_code == 200
    payload = response.json()
    assert payload["site_url"] == "http://192.168.137.1:8080/"
    assert payload["admin_url"] == "http://192.168.137.1:8080/admin"
    assert payload["wifi_ssid"] == "KeychainFair"
    assert payload["qr"] == {
        "wifi": "/static/qr/customer_wifi.png",
        "order": "/static/qr/customer_order.png",
        "admin": "/static/qr/admin.png",
    }
    assert payload["site_qr"].startswith("data:image/png;base64,")
    assert payload["admin_qr"].startswith("data:image/png;base64,")
    assert payload["wifi_qr"].startswith("data:image/png;base64,")


def test_admin_pin_rate_limit_escalates_after_failed_attempts(tmp_path, monkeypatch):
    now = 1000.0
    monkeypatch.setattr("keychain_fair.main.time.monotonic", lambda: now)

    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        assert client.get("/api/admin/orders", headers={"X-Admin-Pin": "bad"}).status_code == 401
        assert client.get("/api/admin/orders", headers={"X-Admin-Pin": "bad"}).status_code == 401

        locked = client.get("/api/admin/orders", headers={"X-Admin-Pin": "bad"})
        assert locked.status_code == 429
        assert locked.json()["detail"]["retry_after_seconds"] == 60

        still_locked = client.get("/api/admin/orders", headers=ADMIN_HEADERS)
        assert still_locked.status_code == 429

        now += 61
        assert client.get("/api/admin/orders", headers={"X-Admin-Pin": "bad"}).status_code == 401
        assert client.get("/api/admin/orders", headers={"X-Admin-Pin": "bad"}).status_code == 401

        locked_again = client.get("/api/admin/orders", headers={"X-Admin-Pin": "bad"})
        assert locked_again.status_code == 429
        assert locked_again.json()["detail"]["retry_after_seconds"] == 15 * 60
