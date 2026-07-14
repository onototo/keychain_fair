from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from typing import Any, Iterable, Mapping

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from . import statuses
from .editor import final_dimensions_mm


CONTENT_SHAPES = {"name", "car", "phone"}
DEFAULT_MODEL_SCALE = 1.0

metadata = MetaData()

orders = Table(
    "orders",
    metadata,
    Column("id", String, primary_key=True),
    Column("idempotency_key", String),
    Column("customer_name", String, nullable=False),
    Column("car_number", String, nullable=False),
    Column("phone", String, nullable=False),
    Column("print_line_1", String, nullable=False, default=""),
    Column("print_line_2", String, nullable=False, default=""),
    Column("design_id", String, nullable=False),
    Column("design_name", String, nullable=False),
    Column("size_id", String, nullable=False),
    Column("size_label", String, nullable=False),
    Column("price", Float),
    Column("width_mm", Float, nullable=False),
    Column("height_mm", Float, nullable=False),
    Column("thickness_mm", Float, nullable=False),
    Column("font_size_mm", Float, nullable=False),
    Column("selected_elements_json", Text, nullable=False),
    Column("model_params_json", Text),
    Column("status", String, nullable=False),
    Column("source", String),
    Column("client_id", String),
    Column("telegram_chat_id", String),
    Column("telegram_user_id", String),
    Column("telegram_username", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("paid_at", String),
    Column("ready_for_pickup_at", String),
    Column("archived_at", String),
    Column("stl_path", Text),
    Column("wrapper_scad_path", Text),
    Column("batch_id", String),
    Column("gcode_path", Text),
    Column("error_message", Text),
)

order_user_mutes = Table(
    "order_user_mutes",
    metadata,
    Column("identity_kind", String, primary_key=True),
    Column("identity_value", String, primary_key=True),
    Column("muted_until", String, nullable=False),
    Column("reason", Text),
    Column("updated_at", String, nullable=False),
)

print_batches = Table(
    "print_batches",
    metadata,
    Column("id", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("manifest_path", Text),
    Column("plate_scad_path", Text),
    Column("plate_stl_path", Text),
    Column("gcode_path", Text),
    Column("error_message", Text),
)

print_batch_items = Table(
    "print_batch_items",
    metadata,
    Column("batch_id", String, ForeignKey("print_batches.id", ondelete="CASCADE"), primary_key=True),
    Column("order_id", String, ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True),
    Column("position_x_mm", Float, nullable=False),
    Column("position_y_mm", Float, nullable=False),
)

Index("idx_orders_status_created", orders.c.status, orders.c.created_at)
Index(
    "idx_orders_idempotency_key",
    orders.c.idempotency_key,
    unique=True,
    sqlite_where=orders.c.idempotency_key.is_not(None),
    postgresql_where=orders.c.idempotency_key.is_not(None),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _elapsed_seconds(start: str | None, end: str | None) -> int | None:
    start_at = _parse_iso(start)
    end_at = _parse_iso(end)
    if start_at is None or end_at is None:
        return None
    try:
        return max(0, int((end_at - start_at).total_seconds()))
    except TypeError:
        return None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _content_element_count(selection: Any) -> int:
    if selection.design.get("layout") != "stacked_plate":
        return 0
    return len([item for item in selection.elements if item.get("shape") in CONTENT_SHAPES])


def _model_scale(size: dict[str, Any]) -> float:
    return float(size.get("model_scale", DEFAULT_MODEL_SCALE))


def _selected_dimensions(selection: Any) -> tuple[float, float]:
    size = selection.size
    width = float(size["width_mm"])
    height = float(size["height_mm"])

    if selection.design.get("layout") == "stacked_plate":
        count = max(1, _content_element_count(selection))
        row_height = float(size.get("row_height_mm", size["height_mm"]))
        top_ratio = float(selection.design.get("top_block_ratio", 0.56))
        bottom_ratio = float(selection.design.get("bottom_block_ratio", 0.56))
        overlap = float(size.get("section_overlap_mm", selection.design.get("section_overlap_mm", 1.4)))
        if count == 1:
            height = row_height
        elif count == 2:
            height = row_height + row_height * bottom_ratio - overlap
        else:
            height = row_height + row_height * top_ratio + row_height * bottom_ratio - 2 * overlap

    scale = _model_scale(size)
    return width * scale, height * scale


def _sqlite_url(database_path: Path) -> str:
    return f"sqlite:///{database_path.as_posix()}"


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite:")


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Database:
    def __init__(self, database_path: Path, database_url: str | None = None):
        self.database_path = database_path
        self.database_url = _normalize_database_url(database_url or _sqlite_url(database_path))
        connect_args = {"check_same_thread": False} if _is_sqlite_url(self.database_url) else {}
        self.engine = create_engine(self.database_url, future=True, connect_args=connect_args, pool_pre_ping=True)

    def connect(self) -> Connection:
        return self.engine.connect()

    def init_schema(self) -> None:
        if self.engine.dialect.name == "sqlite":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)

        metadata.create_all(self.engine)
        self._migrate_existing_schema()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_orders_status_created
                    ON orders(status, created_at)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_idempotency_key
                    ON orders(idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE orders
                    SET ready_for_pickup_at = updated_at
                    WHERE status = :status AND ready_for_pickup_at IS NULL
                    """
                ),
                {"status": statuses.READY_FOR_PICKUP},
            )
            conn.execute(text("UPDATE orders SET source = 'web' WHERE source IS NULL OR source = ''"))
            conn.execute(text("UPDATE orders SET print_line_1 = '' WHERE print_line_1 IS NULL"))
            conn.execute(text("UPDATE orders SET print_line_2 = '' WHERE print_line_2 IS NULL"))

    def _migrate_existing_schema(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("orders")}
        additions = {
            "idempotency_key": "TEXT",
            "archived_at": "TEXT",
            "model_params_json": "TEXT",
            "ready_for_pickup_at": "TEXT",
            "source": "TEXT",
            "client_id": "TEXT",
            "telegram_chat_id": "TEXT",
            "telegram_user_id": "TEXT",
            "telegram_username": "TEXT",
            "print_line_1": "TEXT",
            "print_line_2": "TEXT",
            "price": "FLOAT",
        }
        missing = [(name, ddl_type) for name, ddl_type in additions.items() if name not in columns]
        if not missing:
            return
        with self.engine.begin() as conn:
            for name, ddl_type in missing:
                conn.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {ddl_type}"))

    def create_order(self, payload: dict[str, Any], selection: Any, model_params: dict[str, Any] | None = None) -> dict[str, Any]:
        idempotency_key = payload.get("idempotency_key") or None
        if idempotency_key:
            existing = self.get_order_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        order_id = _new_id("ord")
        created = now_iso()
        elements_json = json.dumps(selection.elements, ensure_ascii=False)
        model_params_json = json.dumps(model_params, ensure_ascii=False) if model_params else None
        size = selection.size
        design = selection.design
        width_mm, height_mm = final_dimensions_mm(design, model_params, selection.elements) if model_params else _selected_dimensions(selection)
        scale = _model_scale(size)
        if model_params:
            car_block = model_params.get("text_blocks", {}).get("car", {})
            font_size_mm = float(car_block.get("font_size_mm", size["font_size_mm"]))
        else:
            font_size_mm = float(size["font_size_mm"]) * scale

        values = {
            "id": order_id,
            "idempotency_key": idempotency_key,
            "customer_name": payload["customer_name"],
            "car_number": payload["car_number"],
            "phone": payload["phone"],
            "print_line_1": payload.get("print_line_1", ""),
            "print_line_2": payload.get("print_line_2", ""),
            "design_id": design["id"],
            "design_name": design["name"],
            "size_id": size["id"],
            "size_label": size["label"],
            "price": _optional_float(size.get("price")),
            "width_mm": width_mm,
            "height_mm": height_mm,
            "thickness_mm": float(model_params.get("thickness_mm", size["thickness_mm"])) if model_params else float(size["thickness_mm"]),
            "font_size_mm": font_size_mm,
            "selected_elements_json": elements_json,
            "model_params_json": model_params_json,
            "status": statuses.UNPAID,
            "source": _clean_optional_text(payload.get("source")) or "web",
            "client_id": _clean_optional_text(payload.get("client_id")),
            "telegram_chat_id": _clean_optional_text(payload.get("telegram_chat_id")),
            "telegram_user_id": _clean_optional_text(payload.get("telegram_user_id")),
            "telegram_username": _clean_optional_text(payload.get("telegram_username")),
            "created_at": created,
            "updated_at": created,
        }

        try:
            with self.engine.begin() as conn:
                conn.execute(orders.insert().values(**values))
        except IntegrityError:
            if idempotency_key:
                existing = self.get_order_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise
        return self.get_order(order_id)

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(select(orders).where(orders.c.id == order_id)).mappings().first()
        if row is None:
            raise KeyError(order_id)
        return self._order_from_row(row)

    def get_order_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(orders).where(orders.c.idempotency_key == idempotency_key)).mappings().first()
        return self._order_from_row(row) if row is not None else None

    def count_unpaid_orders_for_identity(self, identity_kind: str, identity_value: str) -> int:
        column = getattr(orders.c, identity_kind, None)
        if column is None:
            return 0
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(orders.c.id).where(
                    and_(
                        column == identity_value,
                        orders.c.archived_at.is_(None),
                        orders.c.paid_at.is_(None),
                    )
                )
            ).scalars().all()
        return len(rows)

    def get_active_mute(self, identity_kind: str, identity_value: str) -> dict[str, Any] | None:
        now = now_iso()
        with self.engine.connect() as conn:
            row = conn.execute(
                select(order_user_mutes).where(
                    and_(
                        order_user_mutes.c.identity_kind == identity_kind,
                        order_user_mutes.c.identity_value == identity_value,
                        order_user_mutes.c.muted_until > now,
                    )
                )
            ).mappings().first()
        return dict(row) if row is not None else None

    def set_user_mute(self, identity_kind: str, identity_value: str, muted_until: str, reason: str) -> dict[str, Any]:
        updated = now_iso()
        values = {
            "identity_kind": identity_kind,
            "identity_value": identity_value,
            "muted_until": muted_until,
            "reason": reason,
            "updated_at": updated,
        }
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(order_user_mutes.c.identity_kind).where(
                    and_(
                        order_user_mutes.c.identity_kind == identity_kind,
                        order_user_mutes.c.identity_value == identity_value,
                    )
                )
            ).first()
            if existing:
                conn.execute(
                    order_user_mutes.update()
                    .where(
                        and_(
                            order_user_mutes.c.identity_kind == identity_kind,
                            order_user_mutes.c.identity_value == identity_value,
                        )
                    )
                    .values(**values)
                )
            else:
                conn.execute(order_user_mutes.insert().values(**values))
        return values

    def list_orders(self, status: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
        conditions = []
        if not include_archived:
            conditions.append(orders.c.archived_at.is_(None))
        if status:
            conditions.append(orders.c.status == status)
        query = select(orders)
        if conditions:
            query = query.where(and_(*conditions))
        query = query.order_by(orders.c.created_at.desc())
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [self._order_from_row(row) for row in rows]

    def list_orders_by_statuses(self, order_statuses: Iterable[str], ascending: bool = True) -> list[dict[str, Any]]:
        values = list(order_statuses)
        if not values:
            return []
        query = (
            select(orders)
            .where(and_(orders.c.archived_at.is_(None), orders.c.status.in_(values)))
            .order_by(orders.c.created_at.asc() if ascending else orders.c.created_at.desc())
        )
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [self._order_from_row(row) for row in rows]

    def update_order_status(self, order_id: str, status: str, error_message: str | None = None) -> dict[str, Any]:
        statuses.ensure_known_status(status)
        updated = now_iso()
        values: dict[str, Any] = {"status": status, "updated_at": updated, "error_message": error_message}
        if status == statuses.PAID:
            current = self.get_order(order_id)
            values["paid_at"] = current.get("paid_at") or updated
        if status == statuses.READY_FOR_PICKUP:
            current = self.get_order(order_id)
            values["ready_for_pickup_at"] = current.get("ready_for_pickup_at") or updated
        if status == statuses.UNPAID:
            values["paid_at"] = None
            values["ready_for_pickup_at"] = None
            values["batch_id"] = None

        with self.engine.begin() as conn:
            result = conn.execute(orders.update().where(orders.c.id == order_id).values(**values))
            if result.rowcount == 0:
                raise KeyError(order_id)
        return self.get_order(order_id)

    def reset_orphan_printing_orders(self) -> dict[str, Any]:
        updated = now_iso()
        with self.engine.begin() as conn:
            paid_ids = list(
                conn.execute(
                    select(orders.c.id)
                    .where(
                        and_(
                            orders.c.archived_at.is_(None),
                            orders.c.status == statuses.PRINTING,
                            orders.c.paid_at.is_not(None),
                            orders.c.batch_id.is_(None),
                            (orders.c.stl_path.is_(None) | (orders.c.stl_path == "")),
                        )
                    )
                    .order_by(orders.c.created_at.asc())
                ).scalars()
            )
            stl_ids = list(
                conn.execute(
                    select(orders.c.id)
                    .where(
                        and_(
                            orders.c.archived_at.is_(None),
                            orders.c.status == statuses.PRINTING,
                            orders.c.paid_at.is_not(None),
                            orders.c.batch_id.is_(None),
                            orders.c.stl_path.is_not(None),
                            orders.c.stl_path != "",
                        )
                    )
                    .order_by(orders.c.created_at.asc())
                ).scalars()
            )

            if paid_ids:
                conn.execute(
                    orders.update()
                    .where(orders.c.id.in_(paid_ids))
                    .values(status=statuses.PAID, updated_at=updated, error_message=None)
                )
            if stl_ids:
                conn.execute(
                    orders.update()
                    .where(orders.c.id.in_(stl_ids))
                    .values(status=statuses.STL_READY, updated_at=updated, error_message=None)
                )

        return {
            "paid": len(paid_ids),
            "stl_ready": len(stl_ids),
            "order_ids": [*paid_ids, *stl_ids],
        }

    def invalidate_batch_for_order(self, order_id: str, message: str) -> None:
        order = self.get_order(order_id)
        batch_id = order.get("batch_id")
        if not batch_id:
            return

        batch = self.get_batch(str(batch_id))
        if batch["status"] == statuses.PRINTING:
            raise ValueError("Printing batches can not be changed")
        if batch["status"] != statuses.QUEUED:
            return

        updated = now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                print_batches.update()
                .where(print_batches.c.id == batch_id)
                .values(status=statuses.ERROR, updated_at=updated, error_message=message)
            )
            for item in batch["items"]:
                if item.get("archived_at") or item.get("status") != statuses.QUEUED:
                    continue
                conn.execute(
                    orders.update()
                    .where(orders.c.id == item["order_id"])
                    .values(status=statuses.STL_READY, batch_id=None, updated_at=updated, error_message=None)
                )

    def archive_order(self, order_id: str) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] == statuses.PRINTING:
            raise ValueError("Printing orders can not be deleted")
        if order.get("batch_id"):
            self.invalidate_batch_for_order(order_id, "Batch invalidated because an order was deleted.")

        updated = now_iso()
        archived_at = order.get("archived_at") or updated
        with self.engine.begin() as conn:
            result = conn.execute(
                orders.update()
                .where(orders.c.id == order_id)
                .values(archived_at=archived_at, batch_id=None, updated_at=updated)
            )
            if result.rowcount == 0:
                raise KeyError(order_id)
        return self.get_order(order_id)

    def has_printing_batches(self) -> bool:
        with self.engine.connect() as conn:
            batch_id = conn.execute(
                select(print_batches.c.id).where(print_batches.c.status == statuses.PRINTING).limit(1)
            ).scalar()
        return batch_id is not None

    def set_order_generated(self, order_id: str, stl_path: Path, wrapper_scad_path: Path) -> dict[str, Any]:
        updated = now_iso()
        with self.engine.begin() as conn:
            result = conn.execute(
                orders.update()
                .where(orders.c.id == order_id)
                .values(
                    status=statuses.STL_READY,
                    updated_at=updated,
                    stl_path=str(stl_path),
                    wrapper_scad_path=str(wrapper_scad_path),
                    error_message=None,
                )
            )
            if result.rowcount == 0:
                raise KeyError(order_id)
        return self.get_order(order_id)

    def create_batch(self, layout_items: list[dict[str, Any]], manifest_path: Path | None = None) -> dict[str, Any]:
        invalid_orders = [
            item["order"]["id"]
            for item in layout_items
            if item["order"].get("status") != statuses.STL_READY or not item["order"].get("paid_at")
        ]
        if invalid_orders:
            raise ValueError("Only paid STL-ready orders can be placed on the print bed: " + ", ".join(invalid_orders))

        batch_id = _new_id("batch")
        created = now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                print_batches.insert().values(
                    id=batch_id,
                    status=statuses.QUEUED,
                    created_at=created,
                    updated_at=created,
                    manifest_path=str(manifest_path) if manifest_path else None,
                )
            )
            for item in layout_items:
                order = item["order"]
                conn.execute(
                    print_batch_items.insert().values(
                        batch_id=batch_id,
                        order_id=order["id"],
                        position_x_mm=float(item["x_mm"]),
                        position_y_mm=float(item["y_mm"]),
                    )
                )
                conn.execute(
                    orders.update()
                    .where(orders.c.id == order["id"])
                    .values(status=statuses.QUEUED, batch_id=batch_id, updated_at=created, error_message=None)
                )
        return self.get_batch(batch_id)

    def reset_queued_batches_for_rebuild(self, message: str) -> dict[str, Any]:
        updated = now_iso()
        with self.engine.begin() as conn:
            batch_ids = list(
                conn.execute(
                    select(print_batches.c.id)
                    .where(print_batches.c.status == statuses.QUEUED)
                    .order_by(print_batches.c.created_at.asc())
                ).scalars()
            )
            if not batch_ids:
                return {"batch_ids": [], "order_ids": [], "orders_reset": 0}

            order_ids = list(
                conn.execute(
                    select(print_batch_items.c.order_id)
                    .select_from(print_batch_items.join(orders, orders.c.id == print_batch_items.c.order_id))
                    .where(
                        and_(
                            print_batch_items.c.batch_id.in_(batch_ids),
                            orders.c.archived_at.is_(None),
                            orders.c.status == statuses.QUEUED,
                        )
                    )
                    .order_by(orders.c.created_at.asc())
                ).scalars()
            )

            conn.execute(
                print_batches.update()
                .where(print_batches.c.id.in_(batch_ids))
                .values(status=statuses.ERROR, updated_at=updated, error_message=message)
            )
            if order_ids:
                conn.execute(
                    orders.update()
                    .where(orders.c.id.in_(order_ids))
                    .values(status=statuses.STL_READY, batch_id=None, updated_at=updated, error_message=None)
                )

        return {"batch_ids": batch_ids, "order_ids": order_ids, "orders_reset": len(order_ids)}

    def update_batch_files(
        self,
        batch_id: str,
        manifest_path: Path | None = None,
        plate_scad_path: Path | None = None,
        plate_stl_path: Path | None = None,
        gcode_path: Path | None = None,
        status: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        batch = self.get_batch(batch_id)
        next_status = status or batch["status"]
        updated = now_iso()
        values = {
            "status": next_status,
            "updated_at": updated,
            "error_message": error_message,
        }
        if manifest_path is not None:
            values["manifest_path"] = str(manifest_path)
        if plate_scad_path is not None:
            values["plate_scad_path"] = str(plate_scad_path)
        if plate_stl_path is not None:
            values["plate_stl_path"] = str(plate_stl_path)
        if gcode_path is not None:
            values["gcode_path"] = str(gcode_path)

        with self.engine.begin() as conn:
            conn.execute(print_batches.update().where(print_batches.c.id == batch_id).values(**values))
        return self.get_batch(batch_id)

    def reset_printing_to_queue(self) -> dict[str, Any]:
        updated = now_iso()
        with self.engine.begin() as conn:
            batch_ids = list(
                conn.execute(
                    select(print_batches.c.id)
                    .where(print_batches.c.status == statuses.PRINTING)
                    .order_by(print_batches.c.created_at.asc())
                ).scalars()
            )
            order_ids = list(
                conn.execute(
                    select(orders.c.id)
                    .where(and_(orders.c.archived_at.is_(None), orders.c.status == statuses.PRINTING))
                    .order_by(orders.c.created_at.asc())
                ).scalars()
            )

            conn.execute(
                print_batches.update()
                .where(print_batches.c.status == statuses.PRINTING)
                .values(status=statuses.QUEUED, updated_at=updated, error_message=None)
            )
            conn.execute(
                orders.update()
                .where(and_(orders.c.archived_at.is_(None), orders.c.status == statuses.PRINTING))
                .values(status=statuses.QUEUED, updated_at=updated, error_message=None)
            )

        return {
            "batches": [self.get_batch(batch_id) for batch_id in batch_ids],
            "orders_updated": len(order_ids),
        }

    def complete_printing_batches(self) -> dict[str, Any]:
        updated = now_iso()
        with self.engine.begin() as conn:
            batch_ids = list(
                conn.execute(
                    select(print_batches.c.id)
                    .where(print_batches.c.status == statuses.PRINTING)
                    .order_by(print_batches.c.created_at.asc())
                ).scalars()
            )
            order_ids = list(
                conn.execute(
                    select(orders.c.id)
                    .where(and_(orders.c.archived_at.is_(None), orders.c.status == statuses.PRINTING))
                    .order_by(orders.c.created_at.asc())
                ).scalars()
            )

            if batch_ids:
                conn.execute(
                    print_batches.update()
                    .where(print_batches.c.id.in_(batch_ids))
                    .values(status=statuses.PRINTED, updated_at=updated, error_message=None)
                )
            if order_ids:
                conn.execute(
                    orders.update()
                    .where(orders.c.id.in_(order_ids))
                    .values(status=statuses.PRINTED, updated_at=updated, error_message=None)
                )

        return {
            "batches": [self.get_batch(batch_id) for batch_id in batch_ids],
            "orders_updated": len(order_ids),
        }

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            batch_row = conn.execute(select(print_batches).where(print_batches.c.id == batch_id)).mappings().first()
            item_rows = conn.execute(
                select(
                    print_batch_items.c.batch_id,
                    print_batch_items.c.order_id,
                    print_batch_items.c.position_x_mm,
                    print_batch_items.c.position_y_mm,
                    orders.c.customer_name,
                    orders.c.car_number,
                    orders.c.phone,
                    orders.c.status,
                    orders.c.design_name,
                    orders.c.size_label,
                    orders.c.width_mm,
                    orders.c.height_mm,
                    orders.c.thickness_mm,
                    orders.c.archived_at,
                )
                .select_from(print_batch_items.join(orders, orders.c.id == print_batch_items.c.order_id))
                .where(print_batch_items.c.batch_id == batch_id)
                .order_by(print_batch_items.c.position_y_mm.asc(), print_batch_items.c.position_x_mm.asc())
            ).mappings().all()
        if batch_row is None:
            raise KeyError(batch_id)
        return self._batch_from_row(batch_row, item_rows)

    def list_batches(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            batch_ids = list(conn.execute(select(print_batches.c.id).order_by(print_batches.c.created_at.desc())).scalars())
        return [self.get_batch(batch_id) for batch_id in batch_ids]

    def _order_from_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        order = dict(row)
        order["selected_elements"] = json.loads(order.pop("selected_elements_json") or "[]")
        model_params_json = order.pop("model_params_json", None)
        order["model_params"] = json.loads(model_params_json) if model_params_json else None
        order["source"] = order.get("source") or "web"
        order["status_label"] = statuses.STATUS_LABELS.get(order["status"], order["status"])
        order["paid_to_ready_seconds"] = _elapsed_seconds(order.get("paid_at"), order.get("ready_for_pickup_at"))
        return order

    def _batch_from_row(self, row: Mapping[str, Any], items: list[Mapping[str, Any]]) -> dict[str, Any]:
        batch = dict(row)
        batch["status_label"] = statuses.STATUS_LABELS.get(batch["status"], batch["status"])
        batch["items"] = [dict(item) for item in items]
        return batch
