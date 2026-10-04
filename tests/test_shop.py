from pathlib import Path

from fastapi.testclient import TestClient

from keychain_fair.catalog import load_catalog
from keychain_fair.config import PROJECT_ROOT, AppSettings
from keychain_fair.main import create_app
from keychain_fair.shipping import ManualShipmentGateway


TOKEN = "test-internal-token"
HEADERS = {"X-Internal-Token": TOKEN}


def make_client(tmp_path: Path, max_units: int = 99) -> TestClient:
    category = tmp_path / "catalog" / "toys"
    product = category / "skull"
    product.mkdir(parents=True)
    (category / "category.yaml").write_text("title: Фигурки\n", encoding="utf-8")
    (product / "product.yaml").write_text(
        "title: Череп\nprice_byn: 15.5\ndescription: Маленький\n",
        encoding="utf-8",
    )
    bad = category / "bad"
    bad.mkdir()
    (bad / "product.yaml").write_text("title: Пустой\nprice_byn: 0\n", encoding="utf-8")
    offices = tmp_path / "offices.json"
    offices.write_text(
        '[{"id":"12","number":"12","city":"Минск","address":"ул. Ленина, 1"},'
        '{"id":"20","number":"20","city":"Брест","address":"ул. Советская, 3"}]',
        encoding="utf-8",
    )
    settings = AppSettings(
        host="127.0.0.1",
        port=8120,
        database_path=tmp_path / "shop.sqlite3",
        catalog_dir=tmp_path / "catalog",
        offices_path=offices,
        cart_max_units=max_units,
        internal_token=TOKEN,
    )
    return TestClient(create_app(settings))


def order_payload(**overrides):
    payload = {
        "customer_name": "Иван Петров",
        "phone": "80291234567",
        "payment_method": "cod",
        "office_id": "12",
        "items": [{"product_id": "toys/skull", "quantity": 2}],
        "idempotency_key": "tg:1:draft-1",
        "telegram_chat_id": "42",
        "telegram_user_id": "7",
        "telegram_username": "ivan",
    }
    payload.update(overrides)
    return payload


def test_catalog_skips_invalid_product_and_reports_limit(tmp_path: Path):
    client = make_client(tmp_path)
    payload = client.get("/api/catalog").json()
    assert payload["max_units"] == 99
    assert payload["categories"] == [
        {
            "id": "toys",
            "title": "Фигурки",
            "products": [
                {
                    "id": "toys/skull",
                    "title": "Череп",
                    "description": "Маленький",
                    "price_kopecks": 1550,
                    "price_byn": "15.50 BYN",
                    "has_image": False,
                }
            ],
        }
    ]
    assert client.get("/api/catalog/toys/skull/cover").status_code == 404


def test_office_search_finds_city(tmp_path: Path):
    client = make_client(tmp_path)
    offices = client.get("/api/offices", params={"city": "минск"}).json()["offices"]
    assert offices[0]["number"] == "12"
    assert client.get("/api/offices", params={"city": "м"}).status_code == 422


def test_cod_order_stores_manual_shipment_and_replays_idempotency_key(tmp_path: Path):
    client = make_client(tmp_path)
    created = client.post("/api/internal/orders", headers=HEADERS, json=order_payload())
    assert created.status_code == 200
    order = created.json()["order"]
    assert created.json()["created"] is True
    assert order["order_number_label"] == "001"
    assert order["status"] == "new"
    assert order["phone"] == "+375291234567"
    assert order["total_kopecks"] == 3100
    assert order["cod_amount_kopecks"] == 3100
    assert order["declared_value_kopecks"] == 3100
    assert order["shipment_status"] == "manual"
    assert order["shipment_id"] is None
    assert order["items"][0]["quantity"] == 2

    replay = client.post("/api/internal/orders", headers=HEADERS, json=order_payload())
    assert replay.json()["created"] is False
    assert replay.json()["order"]["id"] == order["id"]
    assert client.get("/api/internal/orders", headers=HEADERS).json()["orders"][0]["id"] == order["id"]


def test_cart_limit_is_total_units(tmp_path: Path):
    client = make_client(tmp_path, max_units=99)
    rejected = client.post(
        "/api/internal/orders",
        headers=HEADERS,
        json=order_payload(items=[{"product_id": "toys/skull", "quantity": 100}], idempotency_key="tg:1:too-many"),
    )
    assert rejected.status_code == 400
    assert "99" in rejected.json()["detail"]

    tiny = make_client(tmp_path / "tiny", max_units=3)
    over = tiny.post(
        "/api/internal/orders",
        headers=HEADERS,
        json=order_payload(
            items=[
                {"product_id": "toys/skull", "quantity": 2},
                {"product_id": "toys/skull", "quantity": 2},
            ],
            idempotency_key="tg:1:merged",
        ),
    )
    assert over.status_code == 400


def test_transfer_payment_and_admin_actions(tmp_path: Path):
    client = make_client(tmp_path)
    created = client.post(
        "/api/internal/orders",
        headers=HEADERS,
        json=order_payload(payment_method="transfer", idempotency_key="tg:1:transfer"),
    )
    order_id = created.json()["order"]["id"]
    assert created.json()["order"]["status"] == "awaiting_transfer"
    assert created.json()["order"]["cod_amount_kopecks"] == 0

    early_ship = client.post(f"/api/internal/orders/{order_id}/shipment", headers=HEADERS, json={"tracking_number": "EP1"})
    assert early_ship.status_code == 409

    paid = client.post(f"/api/internal/orders/{order_id}/payment", headers=HEADERS)
    assert paid.json()["order"]["status"] == "paid"
    shipped = client.post(f"/api/internal/orders/{order_id}/shipment", headers=HEADERS, json={"tracking_number": "EP1"})
    assert shipped.json()["order"]["status"] == "shipped"
    assert shipped.json()["order"]["tracking_number"] == "EP1"
    assert client.post(f"/api/internal/orders/{order_id}/cancel", headers=HEADERS).status_code == 409


def test_cod_can_ship_and_unauthorized_is_rejected(tmp_path: Path):
    client = make_client(tmp_path)
    assert client.get("/api/internal/orders").status_code == 401
    created = client.post("/api/internal/orders", headers=HEADERS, json=order_payload())
    order_id = created.json()["order"]["id"]
    shipped = client.post(f"/api/internal/orders/{order_id}/shipment", headers=HEADERS, json={})
    assert shipped.json()["order"]["status"] == "shipped"
    assert shipped.json()["order"]["tracking_number"] is None


def test_session_roundtrip(tmp_path: Path):
    client = make_client(tmp_path)
    assert client.get("/api/internal/sessions/42", headers=HEADERS).json()["state"] is None
    saved = client.put("/api/internal/sessions/42", headers=HEADERS, json={"state": {"cart": [{"productId": "toys/skull", "quantity": 1}]}})
    assert saved.status_code == 200
    assert client.get("/api/internal/sessions/42", headers=HEADERS).json()["state"]["cart"][0]["quantity"] == 1


def test_example_catalog_and_office_snapshot_load():
    catalog = load_catalog(PROJECT_ROOT / "catalog")
    product = catalog.find("example/sample")
    assert product is not None
    assert product.price_kopecks == 1000
    offices = (PROJECT_ROOT / "config" / "europost-offices.json").read_text(encoding="utf-8")
    assert "Минск" in offices


def test_manual_gateway_does_not_create_remote_shipment():
    draft = ManualShipmentGateway().prepare({"total_kopecks": 100})
    assert draft.shipment_status == "manual"
    assert draft.shipment_id is None
