from fastapi.testclient import TestClient

from tests.support import (
    ADMIN_HEADERS,
    CASHIER_HEADERS,
    create_paid_order,
    create_test_app,
    create_unpaid_order,
    make_settings,
)


def test_cashier_page_and_pin_are_required(tmp_path):
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        assert client.get("/cashier").status_code == 200
        assert client.get("/api/cashier/orders").status_code == 401
        assert client.get("/api/cashier/orders", headers=CASHIER_HEADERS).status_code == 200
        assert client.get("/api/cashier/orders", headers=ADMIN_HEADERS).status_code == 401
        assert client.get("/api/admin/tools", headers=CASHIER_HEADERS).status_code == 401


def test_cashier_lists_active_orders_and_uses_paid_at(tmp_path):
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        unpaid_id = create_unpaid_order(client, "cashier-unpaid")
        paid_id = create_paid_order(client, "cashier-paid")
        app.state.database.update_order_status(paid_id, "printed")
        archived_id = create_unpaid_order(client, "cashier-archived")
        client.delete(f"/api/admin/orders/{archived_id}", headers=ADMIN_HEADERS)

        orders = client.get("/api/cashier/orders", headers=CASHIER_HEADERS).json()["orders"]

    by_id = {order["id"]: order for order in orders}
    assert set(by_id) == {unpaid_id, paid_id}
    assert by_id[unpaid_id]["paid_at"] is None
    assert by_id[unpaid_id]["price"] == 15
    assert by_id[paid_id]["status"] == "printed"
    assert by_id[paid_id]["paid_at"] is not None


def test_cashier_can_only_change_payment_status(tmp_path):
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        order_id = create_unpaid_order(client, "cashier-payment")
        paid = client.post(
            f"/api/cashier/orders/{order_id}/payment",
            headers=CASHIER_HEADERS,
            json={"status": "paid"},
        )
        rejected = client.post(
            f"/api/cashier/orders/{order_id}/payment",
            headers=CASHIER_HEADERS,
            json={"status": "printed"},
        )
        unpaid = client.post(
            f"/api/cashier/orders/{order_id}/payment",
            headers=CASHIER_HEADERS,
            json={"status": "unpaid"},
        )

    assert paid.status_code == 200
    assert paid.json()["order"]["paid_at"] is not None
    assert rejected.status_code == 422
    assert unpaid.status_code == 200
    assert unpaid.json()["order"]["status"] == "unpaid"
    assert unpaid.json()["order"]["paid_at"] is None
