from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from . import statuses
from .validation import normalize_car_number, normalize_customer_name, normalize_phone


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=30)
    car_number: str = Field(default="", max_length=16)
    phone: str = Field(min_length=11, max_length=13)
    design_id: str
    size_id: str
    elements: list[str] = Field(default_factory=list, max_length=6)
    print_line_1: str = Field(default="", max_length=48)
    print_line_2: str = Field(default="", max_length=48)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=96)
    client_id: str | None = Field(default=None, min_length=8, max_length=96)

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

    @field_validator("print_line_1", "print_line_2")
    @classmethod
    def clean_print_line(cls, value: str) -> str:
        return " ".join(value.strip().split())

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
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:")
        if any(char not in allowed for char in cleaned):
            raise ValueError("idempotency_key may only contain letters, numbers, '-', '_' and ':'")
        return cleaned

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:")
        if any(char not in allowed for char in cleaned):
            raise ValueError("client_id may only contain letters, numbers, '-', '_' and ':'")
        return cleaned


class InternalOrderCreate(OrderCreate):
    source: str = Field(default="telegram", max_length=24)
    telegram_chat_id: str | None = Field(default=None, max_length=32)
    telegram_user_id: str | None = Field(default=None, max_length=32)
    telegram_username: str | None = Field(default=None, max_length=64)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"telegram", "web"}:
            raise ValueError("source must be 'telegram' or 'web'")
        return cleaned

    @field_validator("telegram_chat_id", "telegram_user_id", "telegram_username")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
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
