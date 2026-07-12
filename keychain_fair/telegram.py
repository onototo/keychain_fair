from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl

import httpx

from .adapters import AdapterResult
from .config import AppSettings


class TelegramAuthError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramIdentity:
    user_id: str
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    def metadata(self) -> dict[str, str | None]:
        return {
            "telegram_user_id": self.user_id,
            "telegram_username": self.username,
            "telegram_first_name": self.first_name,
            "telegram_last_name": self.last_name,
        }


def public_webapp_url(settings: AppSettings) -> str:
    configured = settings.telegram.webapp_url or settings.public_url
    if configured:
        return configured.rstrip("/") + "/"
    return f"http://127.0.0.1:{settings.port}/"


def telegram_deep_link(settings: AppSettings) -> str | None:
    if not settings.telegram.bot_username:
        return None
    return f"https://t.me/{settings.telegram.bot_username}?start=order"


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 24 * 60 * 60,
    now: float | None = None,
) -> TelegramIdentity:
    if not bot_token:
        raise TelegramAuthError("Telegram bot token is not configured")

    values = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise TelegramAuthError("Telegram init data misses hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramAuthError("Telegram init data hash is invalid")

    try:
        auth_date = int(values.get("auth_date", "0"))
    except ValueError as exc:
        raise TelegramAuthError("Telegram init data auth_date is invalid") from exc

    current_time = int(now if now is not None else time.time())
    if max_age_seconds > 0 and current_time - auth_date > max_age_seconds:
        raise TelegramAuthError("Telegram init data is too old")
    if auth_date - current_time > 300:
        raise TelegramAuthError("Telegram init data is from the future")

    raw_user = values.get("user")
    if not raw_user:
        raise TelegramAuthError("Telegram init data misses user")

    try:
        user = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise TelegramAuthError("Telegram init data user is invalid") from exc

    user_id = user.get("id")
    if user_id is None:
        raise TelegramAuthError("Telegram init data user id is missing")

    return TelegramIdentity(
        user_id=str(user_id),
        username=_optional_str(user.get("username")),
        first_name=_optional_str(user.get("first_name")),
        last_name=_optional_str(user.get("last_name")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class TelegramBotClient:
    def __init__(self, token: str):
        self.token = token

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> AdapterResult:
        if not self.token:
            return AdapterResult(False, "Telegram bot token is not configured")

        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json=payload,
                timeout=8,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # pragma: no cover - integration path
            return AdapterResult(False, f"Telegram sendMessage failed: {exc}")

        if not data.get("ok"):
            return AdapterResult(False, f"Telegram sendMessage failed: {data}")
        return AdapterResult(True, "Telegram message sent", extra=data)

    def send_web_app_button(self, chat_id: str | int, webapp_url: str) -> AdapterResult:
        return self.send_message(
            chat_id,
            "Откройте форму заказа брелока:",
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": "Сделать заказ",
                            "web_app": {"url": webapp_url},
                        }
                    ]
                ]
            },
        )


class TelegramUpdateHandler:
    def __init__(self, settings: AppSettings, bot: TelegramBotClient | None = None):
        self.settings = settings
        self.bot = bot or TelegramBotClient(settings.telegram.bot_token)

    def handle(self, update: dict[str, Any]) -> AdapterResult:
        message = update.get("message")
        if not isinstance(message, dict):
            return AdapterResult(True, "Telegram update ignored")

        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        if chat_id is None:
            return AdapterResult(True, "Telegram update has no chat id")

        return self.bot.send_web_app_button(chat_id, public_webapp_url(self.settings))


class TelegramNotifier:
    def __init__(self, settings: AppSettings, database: Any, bot: TelegramBotClient | None = None):
        self.settings = settings
        self.database = database
        self.bot = bot or TelegramBotClient(settings.telegram.bot_token)

    def notify_ready(self, order: dict[str, Any]) -> AdapterResult:
        chat_id = order.get("telegram_user_id")
        if not chat_id:
            return AdapterResult(True, "Order has no Telegram user", extra={"order_id": order["id"], "skipped": True})
        if order.get("telegram_ready_notified_at"):
            return AdapterResult(True, "Telegram ready notification already sent", extra={"order_id": order["id"], "skipped": True})

        status_url = f"{public_webapp_url(self.settings).rstrip('/')}/status/{order['id']}"
        result = self.bot.send_message(
            chat_id,
            f"Ваш брелок готов к выдаче. Статус заказа: {status_url}",
        )
        if result.success:
            self.database.mark_telegram_ready_notified(order["id"])
        return result
