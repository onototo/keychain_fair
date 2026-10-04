from __future__ import annotations

from dataclasses import dataclass
import re

from .catalog import Catalog, format_byn
from .models import OrderCreate
from .offices import Office
from .shipping import ShipmentGateway


class ShopError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedOrder:
    customer_name: str
    phone: str
    payment_method: str
    office: Office
    items: tuple[dict, ...]
    total_kopecks: int
    cod_amount_kopecks: int
    declared_value_kopecks: int
    shipment_status: str
    shipment_id: str | None
    idempotency_key: str
    telegram_chat_id: str | None
    telegram_user_id: str | None
    telegram_username: str | None


_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁёІіЎўҐґЎў\- ]{2,80}$")


def normalize_name(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not _NAME_RE.fullmatch(cleaned):
        raise ShopError("Имя: буквы, от 2 до 80 символов")
    return cleaned


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("80") and len(digits) == 11:
        digits = "375" + digits[2:]
    if len(digits) == 9:
        digits = "375" + digits
    if digits.startswith("375") and len(digits) == 12:
        return f"+{digits}"
    raise ShopError("Укажите телефон в формате +375291234567")


def prepare_order(
    payload: OrderCreate,
    catalog: Catalog,
    offices: tuple[Office, ...],
    max_units: int,
    gateway: ShipmentGateway,
) -> PreparedOrder:
    quantities: dict[str, int] = {}
    for item in payload.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    total_units = sum(quantities.values())
    if total_units < 1 or total_units > max_units:
        raise ShopError(f"Можно заказать не больше {max_units} штук")

    lines: list[dict] = []
    total = 0
    for product_id, quantity in quantities.items():
        product = catalog.find(product_id)
        if product is None:
            raise ShopError("Товар больше не доступен")
        line_total = product.price_kopecks * quantity
        total += line_total
        lines.append(
            {
                "product_id": product.id,
                "title": product.title,
                "unit_price_kopecks": product.price_kopecks,
                "quantity": quantity,
                "line_kopecks": line_total,
                "line_byn": format_byn(line_total),
            }
        )
    lines.sort(key=lambda line: line["title"].casefold())

    office = next((item for item in offices if item.id == payload.office_id), None)
    if office is None:
        raise ShopError("Отделение не найдено")

    prepared = {
        "customer_name": normalize_name(payload.customer_name),
        "phone": normalize_phone(payload.phone),
        "payment_method": payload.payment_method,
        "office_id": office.id,
        "total_kopecks": total,
    }
    shipment = gateway.prepare(prepared)
    cod_amount = total if payload.payment_method == "cod" else 0
    return PreparedOrder(
        customer_name=prepared["customer_name"],
        phone=prepared["phone"],
        payment_method=payload.payment_method,
        office=office,
        items=tuple(lines),
        total_kopecks=total,
        cod_amount_kopecks=cod_amount,
        declared_value_kopecks=total,
        shipment_status=shipment.shipment_status,
        shipment_id=shipment.shipment_id,
        idempotency_key=payload.idempotency_key or "",
        telegram_chat_id=payload.telegram_chat_id,
        telegram_user_id=payload.telegram_user_id,
        telegram_username=payload.telegram_username,
    )
