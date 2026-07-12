from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Iterable

from . import statuses
from .editor import final_dimensions_mm


CONTENT_SHAPES = {"name", "car", "phone"}
DEFAULT_MODEL_SCALE = 1.0


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


class Database:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT,
                    customer_name TEXT NOT NULL,
                    car_number TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    design_id TEXT NOT NULL,
                    design_name TEXT NOT NULL,
                    size_id TEXT NOT NULL,
                    size_label TEXT NOT NULL,
                    width_mm REAL NOT NULL,
                    height_mm REAL NOT NULL,
                    thickness_mm REAL NOT NULL,
                    font_size_mm REAL NOT NULL,
                    selected_elements_json TEXT NOT NULL,
                    model_params_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    paid_at TEXT,
                    ready_for_pickup_at TEXT,
                    archived_at TEXT,
                    telegram_user_id TEXT,
                    telegram_username TEXT,
                    telegram_first_name TEXT,
                    telegram_last_name TEXT,
                    client_ip TEXT,
                    telegram_ready_notified_at TEXT,
                    stl_path TEXT,
                    wrapper_scad_path TEXT,
                    batch_id TEXT,
                    gcode_path TEXT,
                    error_message TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_orders_status_created
                    ON orders(status, created_at);

                CREATE TABLE IF NOT EXISTS print_batches (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    manifest_path TEXT,
                    plate_scad_path TEXT,
                    plate_stl_path TEXT,
                    gcode_path TEXT,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS print_batch_items (
                    batch_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    position_x_mm REAL NOT NULL,
                    position_y_mm REAL NOT NULL,
                    PRIMARY KEY(batch_id, order_id),
                    FOREIGN KEY(batch_id) REFERENCES print_batches(id) ON DELETE CASCADE,
                    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
                );
                """
            )
            order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
            if "idempotency_key" not in order_columns:
                conn.execute("ALTER TABLE orders ADD COLUMN idempotency_key TEXT")
            if "archived_at" not in order_columns:
                conn.execute("ALTER TABLE orders ADD COLUMN archived_at TEXT")
            if "model_params_json" not in order_columns:
                conn.execute("ALTER TABLE orders ADD COLUMN model_params_json TEXT")
            if "ready_for_pickup_at" not in order_columns:
                conn.execute("ALTER TABLE orders ADD COLUMN ready_for_pickup_at TEXT")
                conn.execute(
                    """
                    UPDATE orders
                    SET ready_for_pickup_at = updated_at
                    WHERE status = ? AND ready_for_pickup_at IS NULL
                    """,
                    (statuses.READY_FOR_PICKUP,),
                )
            for column in [
                "telegram_user_id",
                "telegram_username",
                "telegram_first_name",
                "telegram_last_name",
                "client_ip",
                "telegram_ready_notified_at",
            ]:
                if column not in order_columns:
                    conn.execute(f"ALTER TABLE orders ADD COLUMN {column} TEXT")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_idempotency_key
                    ON orders(idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_orders_unpaid_identity
                    ON orders(status, paid_at, telegram_user_id, phone, client_ip)
                """
            )

    def create_order(
        self,
        payload: dict[str, Any],
        selection: Any,
        model_params: dict[str, Any] | None = None,
        identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        identity = identity or {}

        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO orders (
                        id, idempotency_key, customer_name, car_number, phone, design_id, design_name,
                        size_id, size_label, width_mm, height_mm, thickness_mm, font_size_mm,
                        selected_elements_json, model_params_json, status, created_at, updated_at,
                        telegram_user_id, telegram_username, telegram_first_name, telegram_last_name, client_ip
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        idempotency_key,
                        payload["customer_name"],
                        payload["car_number"],
                        payload["phone"],
                        design["id"],
                        design["name"],
                        size["id"],
                        size["label"],
                        width_mm,
                        height_mm,
                        float(model_params.get("thickness_mm", size["thickness_mm"])) if model_params else float(size["thickness_mm"]),
                        font_size_mm,
                        elements_json,
                        model_params_json,
                        statuses.UNPAID,
                        created,
                        created,
                        identity.get("telegram_user_id"),
                        identity.get("telegram_username"),
                        identity.get("telegram_first_name"),
                        identity.get("telegram_last_name"),
                        identity.get("client_ip"),
                    ),
                )
        except sqlite3.IntegrityError:
            if idempotency_key:
                existing = self.get_order_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise
        return self.get_order(order_id)

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(order_id)
        return self._order_from_row(row)

    def get_order_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return self._order_from_row(row) if row is not None else None

    def count_active_unpaid_orders(
        self,
        *,
        telegram_user_id: str | None = None,
        phone: str | None = None,
        client_ip: str | None = None,
    ) -> int:
        conditions: list[str] = []
        args: list[Any] = []
        if telegram_user_id:
            conditions.append("telegram_user_id = ?")
            args.append(telegram_user_id)
        if phone:
            conditions.append("phone = ?")
            args.append(phone)
        if client_ip:
            conditions.append("client_ip = ?")
            args.append(client_ip)
        if not conditions:
            return 0

        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM orders
                WHERE archived_at IS NULL
                  AND status = ?
                  AND paid_at IS NULL
                  AND ({" OR ".join(conditions)})
                """,
                [statuses.UNPAID, *args],
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def list_orders(self, status: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM orders"
        conditions: list[str] = []
        args: list[Any] = []
        if not include_archived:
            conditions.append("archived_at IS NULL")
        if status:
            conditions.append("status = ?")
            args.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [self._order_from_row(row) for row in rows]

    def list_orders_by_statuses(self, order_statuses: Iterable[str], ascending: bool = True) -> list[dict[str, Any]]:
        values = list(order_statuses)
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        direction = "ASC" if ascending else "DESC"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM orders
                WHERE archived_at IS NULL AND status IN ({placeholders})
                ORDER BY created_at {direction}
                """,
                values,
            ).fetchall()
        return [self._order_from_row(row) for row in rows]

    def update_order_status(self, order_id: str, status: str, error_message: str | None = None) -> dict[str, Any]:
        statuses.ensure_known_status(status)
        updated = now_iso()
        paid_at_sql = ", paid_at = COALESCE(paid_at, ?)" if status == statuses.PAID else ""
        ready_at_sql = ", ready_for_pickup_at = COALESCE(ready_for_pickup_at, ?)" if status == statuses.READY_FOR_PICKUP else ""
        if status == statuses.UNPAID:
            paid_at_sql = ", paid_at = NULL, ready_for_pickup_at = NULL, batch_id = NULL"
        args: list[Any] = [status, updated]
        if status == statuses.PAID:
            args.append(updated)
        if status == statuses.READY_FOR_PICKUP:
            args.append(updated)
        args.extend([error_message, order_id])

        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE orders
                SET status = ?, updated_at = ? {paid_at_sql} {ready_at_sql}, error_message = ?
                WHERE id = ?
                """,
                args,
            )
            if conn.total_changes == 0:
                raise KeyError(order_id)
        return self.get_order(order_id)

    def mark_telegram_ready_notified(self, order_id: str) -> dict[str, Any]:
        updated = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET telegram_ready_notified_at = COALESCE(telegram_ready_notified_at, ?)
                WHERE id = ?
                """,
                (updated, order_id),
            )
            if conn.total_changes == 0:
                raise KeyError(order_id)
        return self.get_order(order_id)

    def reset_orphan_printing_orders(self) -> dict[str, Any]:
        updated = now_iso()
        with self.connect() as conn:
            paid_rows = conn.execute(
                """
                SELECT id FROM orders
                WHERE archived_at IS NULL
                  AND status = ?
                  AND paid_at IS NOT NULL
                  AND batch_id IS NULL
                  AND (stl_path IS NULL OR stl_path = '')
                ORDER BY created_at
                """,
                (statuses.PRINTING,),
            ).fetchall()
            stl_rows = conn.execute(
                """
                SELECT id FROM orders
                WHERE archived_at IS NULL
                  AND status = ?
                  AND paid_at IS NOT NULL
                  AND batch_id IS NULL
                  AND stl_path IS NOT NULL
                  AND stl_path != ''
                ORDER BY created_at
                """,
                (statuses.PRINTING,),
            ).fetchall()
            paid_ids = [row["id"] for row in paid_rows]
            stl_ids = [row["id"] for row in stl_rows]

            if paid_ids:
                placeholders = ",".join("?" for _ in paid_ids)
                conn.execute(
                    f"""
                    UPDATE orders
                    SET status = ?, updated_at = ?, error_message = NULL
                    WHERE id IN ({placeholders})
                    """,
                    [statuses.PAID, updated, *paid_ids],
                )
            if stl_ids:
                placeholders = ",".join("?" for _ in stl_ids)
                conn.execute(
                    f"""
                    UPDATE orders
                    SET status = ?, updated_at = ?, error_message = NULL
                    WHERE id IN ({placeholders})
                    """,
                    [statuses.STL_READY, updated, *stl_ids],
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

        updated = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE print_batches
                SET status = ?, updated_at = ?, error_message = ?
                WHERE id = ?
                """,
                (statuses.ERROR, updated, message, batch_id),
            )
            for item in batch["items"]:
                if item.get("archived_at") or item.get("status") != statuses.QUEUED:
                    continue
                conn.execute(
                    """
                    UPDATE orders
                    SET status = ?, batch_id = NULL, updated_at = ?, error_message = NULL
                    WHERE id = ?
                    """,
                    (statuses.STL_READY, updated, item["order_id"]),
                )

    def archive_order(self, order_id: str) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order["status"] == statuses.PRINTING:
            raise ValueError("Printing orders can not be deleted")
        if order.get("batch_id"):
            self.invalidate_batch_for_order(order_id, "Batch invalidated because an order was deleted.")

        updated = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET archived_at = COALESCE(archived_at, ?), batch_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (updated, updated, order_id),
            )
            if conn.total_changes == 0:
                raise KeyError(order_id)
        return self.get_order(order_id)

    def set_order_generated(self, order_id: str, stl_path: Path, wrapper_scad_path: Path) -> dict[str, Any]:
        updated = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET status = ?, updated_at = ?, stl_path = ?, wrapper_scad_path = ?, error_message = NULL
                WHERE id = ?
                """,
                (statuses.STL_READY, updated, str(stl_path), str(wrapper_scad_path), order_id),
            )
            if conn.total_changes == 0:
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
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO print_batches (id, status, created_at, updated_at, manifest_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (batch_id, statuses.QUEUED, created, created, str(manifest_path) if manifest_path else None),
            )
            for item in layout_items:
                order = item["order"]
                conn.execute(
                    """
                    INSERT INTO print_batch_items (batch_id, order_id, position_x_mm, position_y_mm)
                    VALUES (?, ?, ?, ?)
                    """,
                    (batch_id, order["id"], float(item["x_mm"]), float(item["y_mm"])),
                )
                conn.execute(
                    """
                    UPDATE orders
                    SET status = ?, batch_id = ?, updated_at = ?, error_message = NULL
                    WHERE id = ?
                    """,
                    (statuses.QUEUED, batch_id, created, order["id"]),
                )
        return self.get_batch(batch_id)

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
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE print_batches
                SET status = ?, updated_at = ?,
                    manifest_path = COALESCE(?, manifest_path),
                    plate_scad_path = COALESCE(?, plate_scad_path),
                    plate_stl_path = COALESCE(?, plate_stl_path),
                    gcode_path = COALESCE(?, gcode_path),
                    error_message = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    updated,
                    str(manifest_path) if manifest_path else None,
                    str(plate_scad_path) if plate_scad_path else None,
                    str(plate_stl_path) if plate_stl_path else None,
                    str(gcode_path) if gcode_path else None,
                    error_message,
                    batch_id,
                ),
            )
        return self.get_batch(batch_id)

    def reset_printing_to_queue(self) -> dict[str, Any]:
        updated = now_iso()
        with self.connect() as conn:
            batch_rows = conn.execute(
                "SELECT id FROM print_batches WHERE status = ? ORDER BY created_at",
                (statuses.PRINTING,),
            ).fetchall()
            order_rows = conn.execute(
                """
                SELECT id FROM orders
                WHERE archived_at IS NULL AND status = ?
                ORDER BY created_at
                """,
                (statuses.PRINTING,),
            ).fetchall()
            batch_ids = [row["id"] for row in batch_rows]
            order_ids = [row["id"] for row in order_rows]

            conn.execute(
                """
                UPDATE print_batches
                SET status = ?, updated_at = ?, error_message = NULL
                WHERE status = ?
                """,
                (statuses.QUEUED, updated, statuses.PRINTING),
            )
            conn.execute(
                """
                UPDATE orders
                SET status = ?, updated_at = ?, error_message = NULL
                WHERE archived_at IS NULL AND status = ?
                """,
                (statuses.QUEUED, updated, statuses.PRINTING),
            )

        return {
            "batches": [self.get_batch(batch_id) for batch_id in batch_ids],
            "orders_updated": len(order_ids),
        }

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM print_batches WHERE id = ?", (batch_id,)).fetchone()
            items = conn.execute(
                """
                SELECT
                    i.*,
                    o.customer_name,
                    o.car_number,
                    o.phone,
                    o.status,
                    o.design_name,
                    o.size_label,
                    o.width_mm,
                    o.height_mm,
                    o.thickness_mm,
                    o.archived_at
                FROM print_batch_items i
                JOIN orders o ON o.id = i.order_id
                WHERE i.batch_id = ?
                ORDER BY i.position_y_mm, i.position_x_mm
                """,
                (batch_id,),
            ).fetchall()
        if row is None:
            raise KeyError(batch_id)
        return self._batch_from_row(row, items)

    def list_batches(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id FROM print_batches ORDER BY created_at DESC").fetchall()
        return [self.get_batch(row["id"]) for row in rows]

    def _order_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        order = dict(row)
        order["selected_elements"] = json.loads(order.pop("selected_elements_json") or "[]")
        model_params_json = order.pop("model_params_json", None)
        order["model_params"] = json.loads(model_params_json) if model_params_json else None
        order["status_label"] = statuses.STATUS_LABELS.get(order["status"], order["status"])
        order["paid_to_ready_seconds"] = _elapsed_seconds(order.get("paid_at"), order.get("ready_for_pickup_at"))
        return order

    def _batch_from_row(self, row: sqlite3.Row, items: list[sqlite3.Row]) -> dict[str, Any]:
        batch = dict(row)
        batch["status_label"] = statuses.STATUS_LABELS.get(batch["status"], batch["status"])
        batch["items"] = [dict(item) for item in items]
        return batch
