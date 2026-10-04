from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class OrderItemIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=160)
    quantity: int = Field(ge=1, le=999)


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=6, max_length=20)
    payment_method: str
    office_id: str = Field(min_length=1, max_length=32)
    items: list[OrderItemIn] = Field(min_length=1, max_length=99)
    idempotency_key: str = Field(min_length=8, max_length=120)
    telegram_chat_id: str | None = Field(default=None, max_length=32)
    telegram_user_id: str | None = Field(default=None, max_length=32)
    telegram_username: str | None = Field(default=None, max_length=64)

    @field_validator("payment_method")
    @classmethod
    def validate_payment_method(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"cod", "transfer", "online"}:
            raise ValueError("payment_method must be cod, transfer or online")
        return cleaned

    @field_validator("idempotency_key", "office_id", "telegram_chat_id", "telegram_user_id", "telegram_username")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class SessionUpdate(BaseModel):
    state: dict[str, Any]


class ShipUpdate(BaseModel):
    tracking_number: str = Field(default="", max_length=64)

    @field_validator("tracking_number")
    @classmethod
    def clean_tracking(cls, value: str) -> str:
        return " ".join(value.strip().split())
