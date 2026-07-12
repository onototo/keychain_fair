from dataclasses import replace
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from keychain_fair.adapters import AdapterResult
from keychain_fair.config import TelegramSettings
from keychain_fair.telegram import TelegramAuthError, TelegramNotifier, validate_init_data
from tests.support import ADMIN_HEADERS, create_test_app, make_settings, order_payload


BOT_TOKEN = "123456:test-token"
AUTH_DATE = 1_800_000_000


def telegram_settings(**overrides):
    data = {
        "bot_token": BOT_TOKEN,
        "webhook_secret": "webhook-secret",
        "bot_username": "keychain_fair_bot",
        "webapp_url": None,
    }
    data.update(overrides)
    return TelegramSettings(**data)


def signed_init_data(user_id=1001, *, bot_token=BOT_TOKEN, auth_date=None, username="buyer"):
    if auth_date is None:
        auth_date = int(time.time())
    params = {
        "auth_date": str(auth_date),
        "query_id": "AA-test-query",
        "user": json.dumps(
            {
                "id": user_id,
                "first_name": "Nikita",
                "last_name": "Test",
                "username": username,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(params)


def test_telegram_init_data_validation_accepts_signed_user():
    identity = validate_init_data(signed_init_data(auth_date=AUTH_DATE), BOT_TOKEN, now=AUTH_DATE + 10)

    assert identity.user_id == "1001"
    assert identity.username == "buyer"
    assert identity.first_name == "Nikita"


def test_telegram_init_data_validation_rejects_bad_hash_and_expired_data():
    with pytest.raises(TelegramAuthError):
        validate_init_data(signed_init_data(auth_date=AUTH_DATE).replace("hash=", "hash=bad"), BOT_TOKEN, now=AUTH_DATE + 10)

    with pytest.raises(TelegramAuthError):
        validate_init_data(signed_init_data(auth_date=AUTH_DATE), BOT_TOKEN, max_age_seconds=60, now=AUTH_DATE + 120)

    with pytest.raises(TelegramAuthError):
        validate_init_data(signed_init_data(auth_date=AUTH_DATE), "", now=AUTH_DATE + 10)


def test_telegram_webhook_requires_secret_and_sends_web_app_button(tmp_path, monkeypatch):
    settings = replace(
        make_settings(tmp_path),
        public_url="https://fair.example",
        telegram=telegram_settings(),
    )
    sent_messages = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, json, timeout):
        sent_messages.append({"url": url, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("keychain_fair.telegram.httpx.post", fake_post)
    app = create_test_app(settings)

    with TestClient(app) as client:
        rejected = client.post("/api/telegram/webhook", json={"message": {"chat": {"id": 123}, "text": "/start"}})
        accepted = client.post(
            "/api/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
            json={"message": {"chat": {"id": 123}, "text": "/start"}},
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert sent_messages[0]["json"]["chat_id"] == 123
    button = sent_messages[0]["json"]["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == "Сделать заказ"
    assert button["web_app"]["url"] == "https://fair.example/"


def test_third_unpaid_order_by_same_telegram_user_is_rejected(tmp_path):
    settings = replace(make_settings(tmp_path), telegram=telegram_settings())
    app = create_test_app(settings)

    with TestClient(app) as client:
        responses = [
            client.post(
                "/api/orders",
                json=order_payload(
                    phone=f"37529123456{index}",
                    idempotency_key=f"telegram-limit-{index}",
                )
                | {"telegram_init_data": signed_init_data(user_id=333)},
            )
            for index in range(3)
        ]

    assert [response.status_code for response in responses] == [201, 201, 429]
    assert "2 неоплаченных" in responses[2].json()["detail"]


def test_third_unpaid_order_by_same_fallback_phone_is_rejected(tmp_path):
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        responses = [
            client.post(
                "/api/orders",
                json=order_payload(idempotency_key=f"phone-limit-{index}"),
            )
            for index in range(3)
        ]

    assert [response.status_code for response in responses] == [201, 201, 429]


def test_order_limit_resets_after_payment_and_duplicate_retry_does_not_consume_slot(tmp_path):
    app = create_test_app(make_settings(tmp_path))

    with TestClient(app) as client:
        first = client.post("/api/orders", json=order_payload(idempotency_key="limit-duplicate"))
        duplicate = client.post("/api/orders", json=order_payload(idempotency_key="limit-duplicate"))
        second = client.post("/api/orders", json=order_payload(idempotency_key="limit-second"))
        blocked = client.post("/api/orders", json=order_payload(idempotency_key="limit-blocked"))

        client.post(f"/api/admin/orders/{first.json()['order']['id']}/status", headers=ADMIN_HEADERS, json={"status": "paid"})
        after_payment = client.post("/api/orders", json=order_payload(idempotency_key="limit-after-payment"))

    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert duplicate.json()["order"]["id"] == first.json()["order"]["id"]
    assert second.status_code == 201
    assert blocked.status_code == 429
    assert after_payment.status_code == 201


def test_telegram_order_metadata_is_saved_but_hidden_from_public_response(tmp_path):
    settings = replace(make_settings(tmp_path), telegram=telegram_settings())
    app = create_test_app(settings)

    with TestClient(app) as client:
        created = client.post(
            "/api/orders",
            json=order_payload(idempotency_key="telegram-metadata")
            | {"telegram_init_data": signed_init_data(user_id=444, username="visiblebuyer")},
        )
        order_id = created.json()["order"]["id"]
        public = client.get(f"/api/orders/{order_id}").json()["order"]
        admin_order = client.get("/api/admin/orders", headers=ADMIN_HEADERS).json()["orders"][0]

    assert created.status_code == 201
    assert "telegram_user_id" not in public
    assert admin_order["telegram_user_id"] == "444"
    assert admin_order["telegram_username"] == "visiblebuyer"


class FakeBot:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return AdapterResult(True, "fake sent")


def test_telegram_ready_notifier_sends_once_and_skips_local_orders(tmp_path):
    settings = replace(make_settings(tmp_path), public_url="https://fair.example", telegram=telegram_settings())
    app = create_test_app(settings)
    fake_bot = FakeBot()

    with TestClient(app) as client:
        telegram_order_id = client.post(
            "/api/orders",
            json=order_payload(idempotency_key="notify-telegram")
            | {"telegram_init_data": signed_init_data(user_id=555)},
        ).json()["order"]["id"]
        local_order_id = client.post(
            "/api/orders",
            json=order_payload(phone="375291234568", idempotency_key="notify-local"),
        ).json()["order"]["id"]

    notifier = TelegramNotifier(settings, app.state.database, fake_bot)
    telegram_order = app.state.database.get_order(telegram_order_id)
    local_order = app.state.database.get_order(local_order_id)

    first = notifier.notify_ready(telegram_order)
    updated_order = app.state.database.get_order(telegram_order_id)
    second = notifier.notify_ready(updated_order)
    skipped = notifier.notify_ready(local_order)

    assert first.success is True
    assert second.success is True
    assert skipped.success is True
    assert len(fake_bot.messages) == 1
    assert fake_bot.messages[0]["chat_id"] == "555"
    assert f"/status/{telegram_order_id}" in fake_bot.messages[0]["text"]
    assert updated_order["telegram_ready_notified_at"] is not None
