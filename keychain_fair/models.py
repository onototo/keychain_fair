from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from . import statuses
from .validation import normalize_car_number, normalize_customer_name, normalize_phone


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=30)
    car_number: str = Field(default="", max_length=16)
    phone: str = Field(min_length=11, max_length=12)
    design_id: str
    size_id: str
    elements: list[str] = Field(default_factory=list, max_length=6)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=96)
    telegram_init_data: str | None = Field(default=None, max_length=4096)

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, value: str) -> str:
        return normalize_customer_name(value)

    @field_validator("car_number")
    @classmethod
    def validate_car_number(cls, value: str) -> str:
        if not value or not value.strip():
            return ""
        return normalize_car_number(value)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("elements")
    @classmethod
    def deduplicate_elements(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            if item not in result:
                result.append(item)
        return result

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if any(char not in allowed for char in cleaned):
            raise ValueError("idempotency_key may only contain letters, numbers, '-' and '_'")
        return cleaned

    @field_validator("telegram_init_data")
    @classmethod
    def validate_telegram_init_data(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return statuses.ensure_manual_status(value)


class PaymentStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {statuses.PAID, statuses.UNPAID}:
            raise ValueError("Cashier can only set paid or unpaid status")
        return value


class ApiMessage(BaseModel):
    ok: bool
    message: str
    data: dict[str, Any] | None = None
