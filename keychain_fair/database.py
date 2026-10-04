from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, Text, create_engine, select
from sqlalchemy.engine import Engine

from .catalog import format_byn
from .shop import PreparedOrder


metadata = MetaData()

orders = Table(
    "orders",
    metadata,
    Column("id", String, primary_key=True),
    Column("order_number", Integer, nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("payment_method", String, nullable=False),
    Column("customer_name", String, nullable=False),
    Column("phone", String, nullable=False),
    Column("telegram_chat_id", String),
    Column("telegram_user_id", String),
    Column("telegram_username", String),
    Column("office_id", String, nullable=False),
    Column("office_number", String, nullable=False),
    Column("office_city", String, nullable=False),
    Column("office_address", String, nullable=False),
    Column("cod_amount_kopecks", Integer, nullable=False),
    Column("declared_value_kopecks", Integer, nullable=False),
    Column("total_kopecks", Integer, nullable=False),
    Column("shipment_status", String, nullable=False),
    Column("shipment_id", String),
    Column("tracking_number", String),
    Column("idempotency_key", String, nullable=False, unique=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

order_items = Table(
    "order_items",
    metadata,
    Column("id", String, primary_key=True),
    Column("order_id", String, ForeignKey("orders.id"), nullable=False),
    Column("product_id", String, nullable=False),
    Column("title", String, nullable=False),
    Column("unit_price_kopecks", Integer, nullable=False),
    Column("quantity", Integer, nullable=False),
)

bot_sessions = Table(
    "bot_sessions",
    metadata,
    Column("chat_id", String, primary_key=True),
    Column("state_json", Text, nullable=False),
    Column("updated_at", String, nullable=False),
)


class OrderActionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _label(number: int) -> str:
    return f"{number:03d}" if number < 1000 else str(number)


class Database:
    def __init__(self, url: str):
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(url, future=True, connect_args=connect_args)
        metadata.create_all(self.engine)

    def create_order(self, prepared: PreparedOrder) -> tuple[dict, bool]:
        with self.engine.begin() as conn:
            existing = conn.execute(select(orders.c.id).where(orders.c.idempotency_key == prepared.idempotency_key)).scalar_one_or_none()
            if existing:
                return self._load(conn, existing), False
            number = (conn.execute(select(orders.c.order_number).order_by(orders.c.order_number.desc()).limit(1)).scalar_one_or_none() or 0) + 1
            order_id = str(uuid.uuid4())
            now = _now()
            status = "awaiting_transfer" if prepared.payment_method in {"transfer", "online"} else "new"
            conn.execute(
                orders.insert().values(
                    id=order_id,
                    order_number=number,
                    status=status,
                    payment_method=prepared.payment_method,
                    customer_name=prepared.customer_name,
                    phone=prepared.phone,
                    telegram_chat_id=prepared.telegram_chat_id,
                    telegram_user_id=prepared.telegram_user_id,
                    telegram_username=prepared.telegram_username,
                    office_id=prepared.office.id,
                    office_number=prepared.office.number,
                    office_city=prepared.office.city,
                    office_address=prepared.office.address,
                    cod_amount_kopecks=prepared.cod_amount_kopecks,
                    declared_value_kopecks=prepared.declared_value_kopecks,
                    total_kopecks=prepared.total_kopecks,
                    shipment_status=prepared.shipment_status,
                    shipment_id=prepared.shipment_id,
                    tracking_number=None,
                    idempotency_key=prepared.idempotency_key,
                    created_at=now,
                    updated_at=now,
                )
            )
            for item in prepared.items:
                conn.execute(
                    order_items.insert().values(
                        id=str(uuid.uuid4()),
                        order_id=order_id,
                        product_id=item["product_id"],
                        title=item["title"],
                        unit_price_kopecks=item["unit_price_kopecks"],
                        quantity=item["quantity"],
                    )
                )
            return self._load(conn, order_id), True

    def get_order(self, order_id: str) -> dict | None:
        with self.engine.connect() as conn:
            if not conn.execute(select(orders.c.id).where(orders.c.id == order_id)).scalar_one_or_none():
                return None
            return self._load(conn, order_id)

    def list_orders(self, status: str | None, limit: int, offset: int) -> list[dict]:
        query = select(orders.c.id).order_by(orders.c.created_at.desc(), orders.c.order_number.desc())
        if status:
            query = query.where(orders.c.status == status)
        query = query.limit(limit).offset(offset)
        with self.engine.connect() as conn:
            ids = [row[0] for row in conn.execute(query)]
            return [self._load(conn, order_id) for order_id in ids]

    def mark_paid(self, order_id: str) -> dict:
        return self._transition(order_id, {"awaiting_transfer"}, "paid", "Подтвердить оплату можно только для заказа, который её ждёт")

    def mark_shipped(self, order_id: str, tracking_number: str) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(select(orders).where(orders.c.id == order_id)).mappings().first()
            if row is None:
                raise OrderActionError("Заказ не найден")
            if row["status"] not in {"new", "paid"}:
                raise OrderActionError("Отметить отправку можно после оплаты или для наложенного платежа")
            conn.execute(
                orders.update()
                .where(orders.c.id == order_id)
                .values(status="shipped", tracking_number=tracking_number or None, updated_at=_now())
            )
            return self._load(conn, order_id)

    def cancel(self, order_id: str) -> dict:
        return self._transition(
            order_id,
            {"new", "awaiting_transfer", "paid"},
            "cancelled",
            "Этот заказ уже нельзя отменить",
        )

    def get_session(self, chat_id: str) -> dict | None:
        with self.engine.connect() as conn:
            raw = conn.execute(select(bot_sessions.c.state_json).where(bot_sessions.c.chat_id == chat_id)).scalar_one_or_none()
        if raw is None:
            return None
        return json.loads(raw)

    def save_session(self, chat_id: str, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False)
        now = _now()
        with self.engine.begin() as conn:
            existing = conn.execute(select(bot_sessions.c.chat_id).where(bot_sessions.c.chat_id == chat_id)).scalar_one_or_none()
            if existing:
                conn.execute(
                    bot_sessions.update().where(bot_sessions.c.chat_id == chat_id).values(state_json=payload, updated_at=now)
                )
            else:
                conn.execute(bot_sessions.insert().values(chat_id=chat_id, state_json=payload, updated_at=now))

    def clear_session(self, chat_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(bot_sessions.delete().where(bot_sessions.c.chat_id == chat_id))

    def _transition(self, order_id: str, allowed: set[str], status: str, error: str) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(select(orders.c.status).where(orders.c.id == order_id)).first()
            if row is None:
                raise OrderActionError("Заказ не найден")
            if row[0] not in allowed:
                raise OrderActionError(error)
            conn.execute(orders.update().where(orders.c.id == order_id).values(status=status, updated_at=_now()))
            return self._load(conn, order_id)

    def _load(self, conn, order_id: str) -> dict:
        row = conn.execute(select(orders).where(orders.c.id == order_id)).mappings().one()
        items = conn.execute(select(order_items).where(order_items.c.order_id == order_id).order_by(order_items.c.title)).mappings().all()
        item_payload = []
        for item in items:
            line_kopecks = item["unit_price_kopecks"] * item["quantity"]
            item_payload.append(
                {
                    "product_id": item["product_id"],
                    "title": item["title"],
                    "unit_price_kopecks": item["unit_price_kopecks"],
                    "quantity": item["quantity"],
                    "line_kopecks": line_kopecks,
                    "line_byn": format_byn(line_kopecks),
                }
            )
        return {
            "id": row["id"],
            "order_number": row["order_number"],
            "order_number_label": _label(row["order_number"]),
            "status": row["status"],
            "payment_method": row["payment_method"],
            "customer_name": row["customer_name"],
            "phone": row["phone"],
            "telegram_chat_id": row["telegram_chat_id"],
            "telegram_user_id": row["telegram_user_id"],
            "telegram_username": row["telegram_username"],
            "office_id": row["office_id"],
            "office_number": row["office_number"],
            "office_city": row["office_city"],
            "office_address": row["office_address"],
            "cod_amount_kopecks": row["cod_amount_kopecks"],
            "declared_value_kopecks": row["declared_value_kopecks"],
            "total_kopecks": row["total_kopecks"],
            "total_byn": format_byn(row["total_kopecks"]),
            "shipment_status": row["shipment_status"],
            "shipment_id": row["shipment_id"],
            "tracking_number": row["tracking_number"],
            "created_at": row["created_at"],
            "items": item_payload,
        }
