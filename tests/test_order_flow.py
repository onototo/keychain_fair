from dataclasses import replace
import json
from pathlib import Path

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


def test_prepare_rebuilds_existing_queued_batch_with_new_ready_orders(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        first_order_id = create_paid_order(client, "first-queued-order", customer_name="First")
        first_prepare = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        old_batch_id = first_prepare["batching"]["batches"][0]["id"]

        second_order_id = create_paid_order(client, "second-queued-order", customer_name="Second")
        rebuilt = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    active_batches = [batch for batch in batches if batch["status"] == "queued"]
    old_batch = next(batch for batch in batches if batch["id"] == old_batch_id)

    assert rebuilt["rebuilt_queued_batches"]["batch_ids"] == [old_batch_id]
    assert rebuilt["rebuilt_queued_batches"]["order_ids"] == [first_order_id]
    assert old_batch["status"] == "error"
    assert len(active_batches) == 1
    assert [item["order_id"] for item in active_batches[0]["items"]] == [first_order_id, second_order_id]
    assert {round(item["thickness_mm"], 3) for item in active_batches[0]["items"]} == {2.1}


def test_prepare_does_not_rebuild_queued_batch_without_new_ready_orders(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
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


def test_custom_order_splits_one_long_print_line(tmp_path):
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
    assert order["print_line_1"] == "LONG"
    assert order["print_line_2"] == "WORDS"


def test_custom_order_keeps_two_print_lines(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json=order_payload(
                design_id="rounded_tag",
                size_id="standard",
                print_line_1="1234 AB-7",
                print_line_2="+375291234567",
                idempotency_key="custom-two-lines",
            ),
        )
        order = response.json()["order"]

    assert response.status_code == 201
    assert order["print_line_1"] == "1234 AB-7"
    assert order["print_line_2"] == "+375291234567"


def test_custom_order_rejects_text_over_size_limit(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
                json=order_payload(
                    design_id="classic_plate",
                    size_id="standard",
                    print_line_1="X" * 19,
                    idempotency_key="custom-too-long",
                ),
            )

    assert response.status_code == 400
    assert "Maximum length is 18" in response.json()["detail"]


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
        unpaid_id = create_paid_order(client, "will-become-unpaid")
        paid_id = create_paid_order(client, "stays-paid")
        first = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        old_batch_id = first["batching"]["batches"][0]["id"]

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
    assert rebuilt["batching"]["batches_created"] == 1
    assert len(active_batches) == 1
    assert [item["order_id"] for item in active_batches[0]["items"]] == [paid_id]
    assert all(item["status"] != "unpaid" for item in active_batches[0]["items"])
    assert by_id[unpaid_id]["status"] == "unpaid"
    assert by_id[paid_id]["status"] == "queued"


def test_delete_order_archives_and_removes_from_active_bed(tmp_path):
    settings = make_settings(tmp_path)
    app = queue_app(settings)

    with TestClient(app) as client:
        deleted_order_id = create_paid_order(client, "delete-removes-from-bed")
        remaining_order_id = create_paid_order(client, "delete-keeps-other-orders")
        prepared = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        batch_id = prepared["batching"]["batches"][0]["id"]

        deleted = client.delete(f"/api/admin/orders/{deleted_order_id}", headers=ADMIN_HEADERS)
        assert deleted.status_code == 200
        assert deleted.json()["order"]["archived_at"] is not None
        assert deleted.json()["order"]["batch_id"] is None

        active_orders = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"]
        batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]
        rebuilt = client.post("/api/admin/print-queue/prepare", headers=ADMIN_HEADERS).json()
        rebuilt_batches = client.get("/api/admin/batches", headers=ADMIN_HEADERS).json()["batches"]

    assert [order["id"] for order in active_orders] == [remaining_order_id]
    assert active_orders[0]["status"] == "stl_ready"
    assert active_orders[0]["batch_id"] is None
    batch = next(item for item in batches if item["id"] == batch_id)
    assert batch["status"] == "error"
    assert batch["items"][0]["order_id"] == deleted_order_id
    assert batch["items"][0]["archived_at"] is not None
    assert rebuilt["batching"]["batches_created"] == 1
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
