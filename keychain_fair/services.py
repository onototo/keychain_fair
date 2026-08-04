from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import struct
import threading
import time
from typing import Any

from . import statuses
from .adapters import AdapterResult, CuraEngineSlicer, NullNotifier, OctoPrintController, OpenScadModelGenerator, OverlaySafetySpec, stl_bounds
from .batching import layout_orders, write_batch_manifest
from .config import AppSettings
from .database import Database
from .designs import DesignCatalog, DesignCatalogError
from .editor import final_dimensions_mm, model_params_for_selection, relief_height_for_params
from .moderation import MUTE_SECONDS, contains_profanity
from .models import OrderCreate
from .validation import normalize_by_car_number, split_by_car_number


MAX_UNPAID_ORDERS_PER_USER = 2
CONTENT_SHAPES = {"name", "car"}
LOOP_SHAPES = {"loop_left", "loop_right"}
OVERLAY_AUTORUN_DELAY_SECONDS = 20
OVERLAY_SAFETY_CLEARANCE_MM = 2.0
BLANK_DESIGN_ID = "classic_plate"
BLANK_TABLE_Y_OFFSET_MM = 5.0
BLANK_STATE_EMPTY = 1
BLANK_STATE_QUEUED = 2
BLANK_STATE_PRINTING = 3
BLANK_STATE_PRINTED = 4
BLANK_STATE_OVERLAY_QUEUED = 5
BLANK_STATE_OVERLAY_PRINTING = 6
BLANK_STATE_OVERLAY_DONE = 7
BLANK_PRINTING_STATES = {BLANK_STATE_PRINTING, BLANK_STATE_OVERLAY_PRINTING}
BLANK_OCCUPIED_STATES = {
    BLANK_STATE_QUEUED,
    BLANK_STATE_PRINTING,
    BLANK_STATE_PRINTED,
    BLANK_STATE_OVERLAY_QUEUED,
    BLANK_STATE_OVERLAY_PRINTING,
    BLANK_STATE_OVERLAY_DONE,
}
BLANK_SLOT_STATES = {
    BLANK_STATE_EMPTY: ("empty", "пустое место"),
    BLANK_STATE_QUEUED: ("blank_queued", "заготовка будет напечатана"),
    BLANK_STATE_PRINTING: ("blank_printing", "заготовка печатается"),
    BLANK_STATE_PRINTED: ("blank_printed", "заготовка напечатана"),
    BLANK_STATE_OVERLAY_QUEUED: ("overlay_queued", "текст будет напечатан"),
    BLANK_STATE_OVERLAY_PRINTING: ("overlay_printing", "текст печатается"),
    BLANK_STATE_OVERLAY_DONE: ("overlay_printed_needs_clear", "текст напечатан, освободите место"),
}


class OrderBlockedError(ValueError):
    def __init__(self, message: str, status_code: int = 400, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def _filament_change_height(order_or_item: dict[str, Any], fallback: float) -> float:
    try:
        return round(float(order_or_item.get("thickness_mm", fallback)), 3)
    except (TypeError, ValueError):
        return round(fallback, 3)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return max(0, int(number))


def _flags(printer_data: dict[str, Any]) -> dict[str, Any]:
    state = printer_data.get("state")
    if not isinstance(state, dict):
        return {}
    flags = state.get("flags")
    return flags if isinstance(flags, dict) else {}


def _printer_reports_print_done(job_data: dict[str, Any], printer_data: dict[str, Any]) -> bool:
    progress = job_data.get("progress") if isinstance(job_data.get("progress"), dict) else {}
    completion = _number(progress.get("completion"))
    elapsed_seconds = _seconds(progress.get("printTime"))
    remaining_seconds = _seconds(progress.get("printTimeLeft"))
    flags = _flags(printer_data)
    job_state = str(job_data.get("state") or "").lower()

    is_busy = bool(
        flags.get("printing")
        or flags.get("paused")
        or flags.get("pausing")
        or flags.get("cancelling")
        or any(word in job_state for word in ["printing", "paused", "pausing", "cancelling"])
    )
    if is_busy:
        return False
    return bool(
        (completion is not None and completion >= 99.9)
        or (elapsed_seconds is not None and elapsed_seconds > 0 and remaining_seconds == 0)
    )


def _with_print_footprint(order: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(order)
    stl_path = order.get("stl_path")
    if not stl_path:
        return enriched

    try:
        bounds = stl_bounds(Path(stl_path))
    except (OSError, ValueError, struct.error, KeyError, TypeError, AttributeError):
        return enriched

    enriched["print_width_mm"] = max(float(order["width_mm"]), float(bounds["width_mm"]))
    enriched["print_height_mm"] = max(float(order["height_mm"]), float(bounds["height_mm"]))
    enriched["stl_bounds"] = bounds
    return enriched


def _blank_state_payload(state_code: int) -> dict[str, Any]:
    if state_code not in BLANK_SLOT_STATES:
        raise ValueError(f"Unknown blank slot state: {state_code}")
    state, label = BLANK_SLOT_STATES[state_code]
    return {
        "state_code": state_code,
        "state": state,
        "state_label": label,
        "print_enabled": state_code == BLANK_STATE_PRINTED,
    }


def _blank_state_code(value: Any, fallback: int) -> int:
    try:
        state_code = int(value)
    except (TypeError, ValueError):
        return fallback
    return state_code if state_code in BLANK_SLOT_STATES else fallback


def _state_for_overlay_status(status: str | None) -> int | None:
    if status == statuses.QUEUED:
        return BLANK_STATE_OVERLAY_QUEUED
    if status == statuses.PRINTING:
        return BLANK_STATE_OVERLAY_PRINTING
    if status == statuses.PRINTED:
        return BLANK_STATE_OVERLAY_DONE
    return None


def _split_custom_text(value: str, limits: dict[str, Any]) -> tuple[str, str]:
    cleaned = " ".join(str(value).strip().split())
    first_limit = int(limits.get("max_line_1_chars") or 14)
    second_limit = int(limits.get("max_line_2_chars") or 14)
    total_limit = int(limits.get("max_total_chars") or (first_limit + second_limit))

    if len(cleaned) > total_limit:
        raise DesignCatalogError(f"Custom text is too long. Maximum length is {total_limit} characters.")
    if len(cleaned) <= first_limit:
        return cleaned, ""

    midpoint = len(cleaned) / 2
    spaces = [
        index
        for index, char in enumerate(cleaned)
        if char == " " and index <= first_limit and len(cleaned[index + 1 :].strip()) <= second_limit
    ]
    if spaces:
        split_at = min(spaces, key=lambda index: abs(index - midpoint))
        return cleaned[:split_at].strip(), cleaned[split_at + 1 :].strip()

    split_at = min(first_limit, max(1, len(cleaned) - second_limit))
    return cleaned[:split_at].strip(), cleaned[split_at:].strip()


def _validate_custom_text_length(value: str, limits: dict[str, Any]) -> None:
    total_limit = int(limits.get("max_total_chars") or 28)
    if len(value) > total_limit:
        raise DesignCatalogError(f"Custom text is too long. Maximum length is {total_limit} characters.")


class OrderService:
    def __init__(
        self,
        settings: AppSettings,
        database: Database,
        catalog: DesignCatalog,
        generator: OpenScadModelGenerator,
        slicer: CuraEngineSlicer,
        printer: OctoPrintController,
        notifier: NullNotifier,
    ):
        self.settings = settings
        self.database = database
        self.catalog = catalog
        self.generator = generator
        self.slicer = slicer
        self.printer = printer
        self.notifier = notifier
        self._queue_lock = threading.Lock()
        self._overlay_autorun_enabled = False
        self._overlay_next_start_at = 0.0

    def create_order(self, payload: OrderCreate) -> dict[str, Any]:
        if payload.idempotency_key:
            existing = self.database.get_order_by_idempotency_key(payload.idempotency_key)
            if existing is not None:
                return existing

        selection = self.catalog.validate_selection(payload.design_id, payload.size_id, payload.elements)
        identity = self._order_identity(payload)
        if identity is not None:
            self._ensure_not_muted(*identity)
        if contains_profanity(
            payload.customer_name,
            payload.car_number,
            payload.print_line_1,
            payload.print_line_2,
        ):
            if identity is not None:
                self._mute_identity(*identity, reason="profanity")
            raise OrderBlockedError(
                "Order contains prohibited words. You can create a new order in 5 minutes.",
                status_code=429,
                retry_after_seconds=MUTE_SECONDS,
            )
        if identity is not None:
            self._ensure_order_quota(*identity)
        payload_data = self._print_payload(payload, selection.design)
        model_params = model_params_for_selection(selection, self.settings.model.base_height_mm)
        return self.database.create_order(payload_data, selection, model_params)

    def build_order_preview(self, payload: OrderCreate, preview_id: str = "telegram_preview") -> tuple[dict[str, Any], dict[str, Any]]:
        selection = self.catalog.validate_selection(payload.design_id, payload.size_id, payload.elements)
        identity = self._order_identity(payload)
        if identity is not None:
            self._ensure_not_muted(*identity)
            self._ensure_order_quota(*identity)
        if contains_profanity(
            payload.customer_name,
            payload.car_number,
            payload.print_line_1,
            payload.print_line_2,
        ):
            raise OrderBlockedError(
                "Order contains prohibited words. You can create a new order in 5 minutes.",
                status_code=429,
                retry_after_seconds=MUTE_SECONDS,
            )

        payload_data = self._print_payload(payload, selection.design)
        model_params = model_params_for_selection(selection, self.settings.model.base_height_mm)
        width_mm, height_mm = final_dimensions_mm(selection.design, model_params, selection.elements)
        size = selection.size
        car_block = model_params.get("text_blocks", {}).get("car", {})
        return (
            {
                "id": preview_id,
                "customer_name": payload_data["customer_name"],
                "car_number": payload_data["car_number"],
                "phone": payload_data.get("phone", ""),
                "print_line_1": payload_data.get("print_line_1", ""),
                "print_line_2": payload_data.get("print_line_2", ""),
                "design_id": selection.design["id"],
                "design_name": selection.design["name"],
                "size_id": size["id"],
                "size_label": size["label"],
                "selected_elements": selection.elements,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "thickness_mm": float(model_params.get("thickness_mm", size["thickness_mm"])),
                "font_size_mm": float(car_block.get("font_size_mm", size["font_size_mm"])),
                "model_params": model_params,
            },
            selection.design,
        )

    def _order_identity(self, payload: OrderCreate) -> tuple[str, str] | None:
        source = str(getattr(payload, "source", "") or "web")
        if source == "telegram":
            user_id = getattr(payload, "telegram_user_id", None)
            chat_id = getattr(payload, "telegram_chat_id", None)
            if user_id:
                return "telegram_user_id", str(user_id)
            if chat_id:
                return "telegram_chat_id", str(chat_id)
        if payload.client_id:
            return "client_id", payload.client_id
        return None

    def _ensure_not_muted(self, identity_kind: str, identity_value: str) -> None:
        mute = self.database.get_active_mute(identity_kind, identity_value)
        if mute is None:
            return
        muted_until = datetime.fromisoformat(str(mute["muted_until"]))
        retry_after = max(1, int((muted_until - datetime.now(timezone.utc)).total_seconds()))
        raise OrderBlockedError(
            f"You are temporarily muted. Try again in {retry_after} seconds.",
            status_code=429,
            retry_after_seconds=retry_after,
        )

    def _mute_identity(self, identity_kind: str, identity_value: str, reason: str) -> None:
        muted_until = datetime.now(timezone.utc) + timedelta(seconds=MUTE_SECONDS)
        self.database.set_user_mute(identity_kind, identity_value, muted_until.isoformat(), reason)

    def _ensure_order_quota(self, identity_kind: str, identity_value: str) -> None:
        count = self.database.count_unpaid_orders_for_identity(identity_kind, identity_value)
        if count >= MAX_UNPAID_ORDERS_PER_USER:
            raise OrderBlockedError(
                "You can have only 2 unpaid orders. Please pay for an existing order before creating another one.",
                status_code=429,
            )

    def _print_payload(self, payload: OrderCreate, design: dict[str, Any]) -> dict[str, Any]:
        data = payload.model_dump()
        data["elements"] = []
        mode = str(design.get("print_mode") or "")

        if mode == "by_number_square":
            try:
                first, second = split_by_car_number(payload.car_number)
            except ValueError as exc:
                raise DesignCatalogError(str(exc)) from exc
            data["car_number"] = normalize_by_car_number(payload.car_number)
            data["print_line_1"] = first
            data["print_line_2"] = second
            return data

        if mode == "by_number_single":
            try:
                normalized = normalize_by_car_number(payload.car_number)
            except ValueError as exc:
                raise DesignCatalogError(str(exc)) from exc
            data["car_number"] = normalized
            data["print_line_1"] = normalized
            data["print_line_2"] = ""
            return data

        if mode == "custom_text":
            first = " ".join(payload.print_line_1.strip().split())
            second = " ".join(payload.print_line_2.strip().split())
            text = " ".join(item for item in [first, second] if item)
            if not text:
                raise DesignCatalogError("Print text is required for custom designs")
            size = next((item for item in design.get("sizes", []) if item.get("id") == payload.size_id), None)
            limits = size.get("custom_text_limits", {}) if isinstance(size, dict) else {}
            _validate_custom_text_length(text, limits)
            data["print_line_1"] = text
            data["print_line_2"] = ""
            return data

        return data

    def _validate_print_content(self, payload: OrderCreate, selection: Any) -> None:
        if selection.design.get("layout") != "stacked_plate":
            return

        selected_content = {item.get("shape") for item in selection.elements if item.get("shape") in CONTENT_SHAPES}
        selected_loop_sides = [item.get("shape") for item in selection.elements if item.get("shape") in LOOP_SHAPES]
        if not selected_content:
            raise DesignCatalogError("Выберите хотя бы один блок для печати: имя или номер авто")
        if "car" in selected_content and not payload.car_number:
            raise DesignCatalogError("Укажите номер авто или снимите галочку с блока номера")
        if len(selected_loop_sides) > 1:
            raise DesignCatalogError("Выберите только одну сторону ушка для цепочки")

    def set_status(self, order_id: str, status: str) -> dict[str, Any]:
        statuses.ensure_manual_status(status)
        current = self.database.get_order(order_id)
        if status == statuses.UNPAID and current.get("batch_id"):
            self.database.invalidate_batch_for_order(
                order_id,
                "Batch invalidated because an order was marked unpaid.",
            )
        order = self.database.update_order_status(order_id, status)
        if status == statuses.READY_FOR_PICKUP:
            self.notifier.notify_ready(order)
        return order

    def process_paid_orders(self) -> dict[str, Any]:
        generated = 0
        errors: list[dict[str, str]] = []
        paid_orders = self.database.list_orders_by_statuses([statuses.PAID], ascending=True)

        for order in paid_orders:
            try:
                design = self.catalog.get_design(order["design_id"])
            except DesignCatalogError as exc:
                self.database.update_order_status(order["id"], statuses.ERROR, str(exc))
                errors.append({"order_id": order["id"], "message": str(exc)})
                continue

            result = self.generator.generate_order_stl(order, design)
            if result.success and result.output_path:
                wrapper = Path(result.extra["wrapper_scad_path"]) if result.extra and "wrapper_scad_path" in result.extra else (
                    self.settings.generated_dir / "orders" / order["id"] / f"{order['id']}.scad"
                )
                self.database.set_order_generated(order["id"], result.output_path, wrapper)
                generated += 1
            else:
                self.database.update_order_status(order["id"], statuses.ERROR, result.message)
                errors.append({"order_id": order["id"], "message": result.message})

        return {"generated": generated, "errors": errors}

    def _classic_selection(self, size_id: str | None = None) -> Any:
        return self.catalog.validate_selection(BLANK_DESIGN_ID, size_id or self.settings.queue.blank_size_id, [])

    def _blank_order(self, blank_id: str, size_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        selection = self._classic_selection(size_id)
        model_params = model_params_for_selection(selection, self.settings.model.base_height_mm)
        width_mm, height_mm = final_dimensions_mm(selection.design, model_params, selection.elements)
        car_block = model_params.get("text_blocks", {}).get("car", {})
        order = {
            "id": blank_id,
            "customer_name": "blank",
            "car_number": "",
            "print_line_1": "",
            "print_line_2": "",
            "design_id": selection.design["id"],
            "design_name": selection.design["name"],
            "size_id": selection.size["id"],
            "size_label": selection.size["label"],
            "selected_elements": selection.elements,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "thickness_mm": float(model_params.get("thickness_mm", selection.size["thickness_mm"])),
            "font_size_mm": float(car_block.get("font_size_mm", selection.size["font_size_mm"])),
            "model_params": model_params,
        }
        return order, selection.design

    def _blank_size_id_for_dimensions(self, width_mm: float, height_mm: float) -> str | None:
        try:
            design = self.catalog.get_design(BLANK_DESIGN_ID)
        except DesignCatalogError:
            return None
        for size in design.get("sizes", []):
            if abs(float(size.get("width_mm", 0.0)) - width_mm) <= 0.01 and abs(float(size.get("height_mm", 0.0)) - height_mm) <= 0.01:
                return str(size["id"])
        return None

    def _blank_batch_dimensions(
        self,
        blank_batch: dict[str, Any],
        fallback_width: float,
        fallback_height: float,
    ) -> tuple[float, float]:
        metadata = blank_batch.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        raw_slots = metadata.get("slots", []) if isinstance(metadata, dict) else []
        first_slot = raw_slots[0] if raw_slots and isinstance(raw_slots[0], dict) else {}
        return (
            float(metadata.get("blank_width_mm") or first_slot.get("width_mm") or fallback_width),
            float(metadata.get("blank_height_mm") or first_slot.get("height_mm") or fallback_height),
        )

    def _blank_batch_size_id(self, blank_batch: dict[str, Any], fallback_width: float, fallback_height: float) -> str:
        metadata = blank_batch.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("blank_size_id"):
            return str(metadata["blank_size_id"])
        blank_width, blank_height = self._blank_batch_dimensions(blank_batch, fallback_width, fallback_height)
        return self._blank_size_id_for_dimensions(blank_width, blank_height) or self.settings.queue.blank_size_id

    def _blank_slots(self, width_mm: float, height_mm: float) -> list[dict[str, Any]]:
        bed_width, bed_height = self.settings.queue.bed_size_mm
        spacing = self.settings.queue.item_spacing_mm
        slots: list[dict[str, Any]] = []
        y = spacing + BLANK_TABLE_Y_OFFSET_MM
        while y + height_mm + spacing <= bed_height + 0.001:
            x = spacing
            while x + width_mm + spacing <= bed_width + 0.001:
                slots.append(
                    {
                        "index": len(slots),
                        "x_mm": round(x, 3),
                        "y_mm": round(y, 3),
                        "width_mm": round(width_mm, 3),
                        "height_mm": round(height_mm, 3),
                        **_blank_state_payload(BLANK_STATE_EMPTY),
                    }
                )
                x += width_mm + spacing
            y += height_mm + spacing
        return slots

    def _normalize_blank_slot(
        self,
        slot: dict[str, Any],
        fallback_index: int,
        blank_width: float,
        blank_height: float,
        default_state_code: int = BLANK_STATE_EMPTY,
    ) -> dict[str, Any]:
        try:
            index = int(slot.get("index", fallback_index))
        except (TypeError, ValueError):
            index = fallback_index
        fallback_state = BLANK_STATE_OVERLAY_DONE if slot.get("print_enabled") is False else default_state_code
        state_code = _blank_state_code(slot.get("state_code"), fallback_state)
        return {
            "index": index,
            "x_mm": round(float(slot.get("x_mm", 0.0)), 3),
            "y_mm": round(float(slot.get("y_mm", 0.0)), 3),
            "width_mm": round(float(slot.get("width_mm", blank_width)), 3),
            "height_mm": round(float(slot.get("height_mm", blank_height)), 3),
            **_blank_state_payload(state_code),
        }

    def _blank_slot_fits_order(self, slot: dict[str, Any], order: dict[str, Any]) -> bool:
        width = float(order.get("print_width_mm") or order["width_mm"])
        height = float(order.get("print_height_mm") or order["height_mm"])
        return width <= float(slot["width_mm"]) + 0.001 and height <= float(slot["height_mm"]) + 0.001

    def _blank_slot_matches_order_size(self, slot: dict[str, Any], order: dict[str, Any]) -> bool:
        return (
            abs(float(order["width_mm"]) - float(slot["width_mm"])) <= 0.01
            and abs(float(order["height_mm"]) - float(slot["height_mm"])) <= 0.01
        )

    def _slot_index(self, slot: dict[str, Any], fallback_index: int) -> int:
        try:
            return int(slot.get("index", fallback_index))
        except (TypeError, ValueError):
            return fallback_index

    def _default_blank_slot_state(self, blank_batch: dict[str, Any] | None) -> int:
        if blank_batch is None:
            return BLANK_STATE_EMPTY
        if blank_batch["status"] == statuses.QUEUED:
            return BLANK_STATE_QUEUED
        if blank_batch["status"] == statuses.PRINTING:
            return BLANK_STATE_PRINTING
        if blank_batch["status"] == statuses.PRINTED:
            return BLANK_STATE_PRINTED
        return BLANK_STATE_EMPTY

    def _normalize_blank_slots(
        self,
        raw_slots: list[Any],
        blank_width: float,
        blank_height: float,
        default_state_code: int,
        assignments: dict[int, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        normalized = []
        for fallback_index, raw_slot in enumerate(raw_slots):
            slot = dict(raw_slot) if isinstance(raw_slot, dict) else {}
            slot_index = self._slot_index(slot, fallback_index)
            state_default = default_state_code
            if "state_code" not in slot:
                assignment = (assignments or {}).get(slot_index)
                overlay_state = _state_for_overlay_status(assignment.get("batch_status") if assignment else None)
                if overlay_state is not None:
                    state_default = overlay_state
            normalized.append(self._normalize_blank_slot(slot, fallback_index, blank_width, blank_height, state_default))
        return normalized

    def _blank_slots_payload(self, batch: dict[str, Any], slots: list[dict[str, Any]]) -> dict[str, Any]:
        payload = dict(batch.get("metadata") or {})
        payload["slots"] = slots
        payload["slot_count"] = len(slots)
        return payload

    def _set_slot_states(
        self,
        slots: list[dict[str, Any]],
        slot_indices: set[int],
        state_code: int,
    ) -> list[dict[str, Any]]:
        return [
            {**slot, **_blank_state_payload(state_code)} if int(slot["index"]) in slot_indices else slot
            for slot in slots
        ]

    def _update_blank_batch_slots(
        self,
        batch: dict[str, Any],
        slots: list[dict[str, Any]],
        *,
        status: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return self.database.update_batch_files(
            batch["id"],
            status=status,
            error_message=error_message,
            metadata_payload=self._blank_slots_payload(batch, slots),
        )

    def _set_parent_blank_slot_state(self, overlay_batch: dict[str, Any], state_code: int) -> dict[str, Any] | None:
        parent_id = overlay_batch.get("parent_batch_id")
        if not parent_id or overlay_batch.get("slot_index") is None:
            return None
        blank_batch = self.database.get_batch(str(parent_id))
        metadata = blank_batch.get("metadata", {})
        raw_slots = metadata.get("slots", []) if isinstance(metadata, dict) else []
        if not isinstance(raw_slots, list) or not raw_slots:
            return None
        blank_width = float(metadata.get("blank_width_mm") or 56.0)
        blank_height = float(metadata.get("blank_height_mm") or 24.0)
        slots = self._normalize_blank_slots(raw_slots, blank_width, blank_height, self._default_blank_slot_state(blank_batch))
        slots = self._set_slot_states(slots, {int(overlay_batch["slot_index"])}, state_code)
        return self._update_blank_batch_slots(blank_batch, slots)

    def _overlay_assignments(self, blank_batch: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
        assignments: dict[int, dict[str, Any]] = {}
        if blank_batch is None:
            return assignments
        for batch in self.database.overlay_batches_for_blank(blank_batch["id"]):
            if batch.get("slot_index") is None:
                continue
            try:
                slot_index = int(batch["slot_index"])
            except (TypeError, ValueError):
                continue
            item = batch["items"][0] if batch.get("items") else {}
            assignments[slot_index] = {
                "batch_id": batch["id"],
                "batch_status": batch["status"],
                "batch_status_label": batch.get("status_label", batch["status"]),
                "order_id": item.get("order_id"),
                "order_number": item.get("order_number") or "",
                "order_status": item.get("status"),
                "customer_name": item.get("customer_name") or "",
                "car_number": item.get("car_number") or "",
                "print_line_1": item.get("print_line_1") or "",
                "print_line_2": item.get("print_line_2") or "",
                "design_name": item.get("design_name") or "",
                "size_label": item.get("size_label") or "",
            }
        return assignments

    def blank_table_preview(self) -> dict[str, Any]:
        blank_order, _ = self._blank_order("blank_preview")
        blank_width = float(blank_order["width_mm"])
        blank_height = float(blank_order["height_mm"])
        blank_size_id = str(blank_order["size_id"])
        blank_size_label = str(blank_order["size_label"])
        planned_slots = self._blank_slots(blank_width, blank_height)
        blank_batch = self.database.latest_blank_batch([statuses.QUEUED, statuses.PRINTING, statuses.PRINTED])
        assignments = self._overlay_assignments(blank_batch)

        source_slots = planned_slots
        default_state = BLANK_STATE_EMPTY
        slots: list[dict[str, Any]] | None = None
        if blank_batch is not None:
            metadata = blank_batch.get("metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            saved_slots = metadata.get("slots", [])
            if isinstance(saved_slots, list) and saved_slots:
                batch_width, batch_height = self._blank_batch_dimensions(blank_batch, blank_width, blank_height)
                batch_slots = self._normalize_blank_slots(
                    saved_slots,
                    batch_width,
                    batch_height,
                    self._default_blank_slot_state(blank_batch),
                    assignments,
                )
                if any(slot["state_code"] in BLANK_OCCUPIED_STATES for slot in batch_slots) or assignments:
                    blank_width, blank_height = batch_width, batch_height
                    blank_size_id = self._blank_batch_size_id(blank_batch, blank_width, blank_height)
                    try:
                        batch_blank_order, _ = self._blank_order("blank_preview", blank_size_id)
                        blank_size_label = str(batch_blank_order["size_label"])
                    except DesignCatalogError:
                        blank_size_label = f"{blank_width:g}x{blank_height:g} мм"
                    slots = batch_slots
                else:
                    blank_batch = None
                    assignments = {}
            default_state = self._default_blank_slot_state(blank_batch)

        if slots is None:
            slots = self._normalize_blank_slots(source_slots, blank_width, blank_height, default_state, assignments)

        preview_slots = []
        for slot in slots:
            assignment = assignments.get(slot["index"])
            preview_slot = {
                **slot,
                "assigned": assignment is not None,
                "available_for_blank": slot["state_code"] == BLANK_STATE_EMPTY,
                "available_for_text": slot["state_code"] == BLANK_STATE_PRINTED,
            }
            if assignment is not None:
                preview_slot["assignment"] = assignment
            preview_slots.append(preview_slot)

        assigned_count = sum(1 for slot in preview_slots if slot["assigned"])
        state_counts = {
            str(state_code): sum(1 for slot in preview_slots if slot["state_code"] == state_code)
            for state_code in BLANK_SLOT_STATES
        }
        empty_count = state_counts[str(BLANK_STATE_EMPTY)]
        free_count = sum(1 for slot in preview_slots if slot["available_for_text"])
        active_batch_payload = None
        has_active_blanks = any(slot["state_code"] in BLANK_OCCUPIED_STATES for slot in preview_slots)
        state = "planned"
        message = f"План стола: {len(preview_slots)} заготовок {blank_size_label} {blank_width:g}x{blank_height:g} мм."

        if blank_batch is not None:
            active_batch_payload = {
                "id": blank_batch["id"],
                "status": blank_batch["status"],
                "status_label": blank_batch.get("status_label", blank_batch["status"]),
            }
            if state_counts[str(BLANK_STATE_PRINTING)]:
                state = "printing_blanks"
                message = f"Печатается заготовок: {state_counts[str(BLANK_STATE_PRINTING)]} из {len(preview_slots)}."
            elif state_counts[str(BLANK_STATE_QUEUED)]:
                state = "queued_blanks"
                message = f"Будет напечатано заготовок: {state_counts[str(BLANK_STATE_QUEUED)]}."
            elif free_count == 0 and has_active_blanks:
                state = "full"
                message = "Нет свободных напечатанных заготовок для текста. Освободите места или напечатайте заготовки."
            else:
                state = "active"
                message = f"Активный стол: заготовок для текста {free_count}, пустых мест {empty_count} из {len(preview_slots)}."

        return {
            "state": state,
            "message": message,
            "blank_size_id": blank_size_id,
            "blank_size_label": blank_size_label,
            "blank_size_mm": [blank_width, blank_height],
            "bed_size_mm": self.settings.queue.bed_size_mm,
            "slot_count": len(preview_slots),
            "assigned_slots": assigned_count,
            "free_slots": free_count,
            "empty_slots": empty_count,
            "disabled_slots": state_counts[str(BLANK_STATE_OVERLAY_DONE)],
            "state_counts": state_counts,
            "has_active_blanks": has_active_blanks,
            "can_accept_orders": has_active_blanks and free_count > 0,
            "needs_new_blanks": has_active_blanks and free_count == 0,
            "active_blank_batch": active_batch_payload,
            "slots": preview_slots,
        }

    def set_blank_slot_state(
        self,
        blank_batch_id: str,
        slot_index: int,
        state_code: int | None,
        legacy_print_enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self._queue_lock:
            blank_batch = self.database.get_batch(blank_batch_id)
            if blank_batch.get("kind") != "blank":
                raise ValueError("Batch is not a blank table")

            if state_code is None:
                if legacy_print_enabled is None:
                    raise ValueError("Blank slot state is required")
                state_code = BLANK_STATE_PRINTED if legacy_print_enabled else BLANK_STATE_OVERLAY_DONE
            state_code = _blank_state_code(state_code, 0)
            if state_code == 0:
                raise ValueError("Blank slot state must be between 1 and 7")

            metadata = blank_batch.get("metadata", {})
            raw_slots = metadata.get("slots", []) if isinstance(metadata, dict) else []
            if not isinstance(raw_slots, list) or not raw_slots:
                raise ValueError("Blank batch has no saved slots")

            blank_width = float(metadata.get("blank_width_mm") or 56.0)
            blank_height = float(metadata.get("blank_height_mm") or 24.0)
            assignments = self._overlay_assignments(blank_batch)
            slots = self._normalize_blank_slots(
                raw_slots,
                blank_width,
                blank_height,
                self._default_blank_slot_state(blank_batch),
                assignments,
            )
            found = False
            current_state = None
            updated_slots: list[dict[str, Any]] = []
            for slot in slots:
                if int(slot["index"]) == int(slot_index):
                    found = True
                    current_state = int(slot["state_code"])
                    if current_state == BLANK_STATE_PRINTING and blank_batch["status"] == statuses.PRINTING:
                        raise ValueError("Can not change a slot while it is printing")
                    updated_slots.append({**slot, **_blank_state_payload(state_code)})
                else:
                    updated_slots.append(slot)
            if not found:
                raise KeyError(slot_index)

            for batch in self.database.overlay_batches_for_blank(blank_batch_id):
                if batch.get("slot_index") != slot_index:
                    continue
                if batch["status"] == statuses.PRINTING:
                    raise ValueError("Can not change a slot while its overlay is printing")
                if batch["status"] == statuses.QUEUED and batch.get("items") and state_code != BLANK_STATE_OVERLAY_QUEUED:
                    self.database.invalidate_batch_for_order(
                        batch["items"][0]["order_id"],
                        "Blank slot state changed by operator.",
                    )

            return self._update_blank_batch_slots(blank_batch, updated_slots)

    def set_blank_slot_enabled(self, blank_batch_id: str, slot_index: int, print_enabled: bool) -> dict[str, Any]:
        return self.set_blank_slot_state(blank_batch_id, slot_index, None, print_enabled)

    def print_blank_batch(self, target_count: int | None = None) -> dict[str, Any]:
        with self._queue_lock:
            blank_order, design = self._blank_order("blank_template")
            blank_width = float(blank_order["width_mm"])
            blank_height = float(blank_order["height_mm"])
            planned_slots = self._blank_slots(blank_width, blank_height)
            blank_batch = self.database.latest_blank_batch([statuses.QUEUED, statuses.PRINTING, statuses.PRINTED])
            assignments = self._overlay_assignments(blank_batch)
            slots = planned_slots
            default_state = BLANK_STATE_EMPTY
            if blank_batch is not None:
                metadata = blank_batch.get("metadata", {})
                metadata = metadata if isinstance(metadata, dict) else {}
                raw_slots = metadata.get("slots", [])
                if isinstance(raw_slots, list) and raw_slots:
                    batch_width, batch_height = self._blank_batch_dimensions(blank_batch, blank_width, blank_height)
                    batch_default_state = self._default_blank_slot_state(blank_batch)
                    batch_slots = self._normalize_blank_slots(raw_slots, batch_width, batch_height, batch_default_state, assignments)
                    if any(slot["state_code"] in BLANK_OCCUPIED_STATES for slot in batch_slots) or assignments:
                        slots = batch_slots
                        blank_width, blank_height = batch_width, batch_height
                        batch_size_id = self._blank_batch_size_id(blank_batch, blank_width, blank_height)
                        blank_order, design = self._blank_order("blank_template", batch_size_id)
                    else:
                        blank_batch = None
                        assignments = {}
                default_state = self._default_blank_slot_state(blank_batch)
            if slots is not planned_slots:
                slots = self._normalize_blank_slots(slots, blank_width, blank_height, default_state, assignments)
            else:
                slots = self._normalize_blank_slots(planned_slots, blank_width, blank_height, default_state, assignments)
            if not slots:
                raise ValueError("Blank does not fit on the configured bed")
            target_total = len(slots) if target_count is None else min(int(target_count), len(slots))
            occupied_count = sum(1 for slot in slots if slot["state_code"] in BLANK_OCCUPIED_STATES)
            missing_count = max(0, target_total - occupied_count)
            selected_slots = [slot for slot in slots if slot["state_code"] == BLANK_STATE_EMPTY][:missing_count]
            if not selected_slots:
                return {
                    "batch": blank_batch,
                    "message": f"Заготовки не запускались: занято {occupied_count} из {len(slots)}, цель {target_total}.",
                    "slots": [],
                    "requested_count": target_total,
                    "selected_count": 0,
                    "blank_preview": self.blank_table_preview(),
                }
            if not self.settings.slicer.enabled:
                raise ValueError("Slicing is disabled in config/app.yaml")

            printer_ready = self._printer_ready_for_start()
            if not printer_ready.success:
                raise ValueError(f"Printer is not ready: {printer_ready.message}")

            selected_indices = {int(slot["index"]) for slot in selected_slots}
            queued_slots = self._set_slot_states(slots, selected_indices, BLANK_STATE_QUEUED)
            if blank_batch is None:
                batch = self.database.create_blank_batch(
                    queued_slots,
                    metadata_payload={
                        "blank_width_mm": blank_order["width_mm"],
                        "blank_height_mm": blank_order["height_mm"],
                        "blank_thickness_mm": blank_order["thickness_mm"],
                        "blank_size_id": blank_order["size_id"],
                        "blank_size_label": blank_order["size_label"],
                        "slot_count": len(queued_slots),
                    },
                )
            else:
                batch = self._update_blank_batch_slots(blank_batch, queued_slots, status=statuses.QUEUED)
            previous_batch = blank_batch
            previous_slots = slots

            try:
                blank_order["id"] = batch["id"]
                blank_result = self.generator.generate_blank_stl(batch["id"], blank_order, design)
                if not blank_result.success or not blank_result.output_path:
                    raise ValueError(blank_result.message)

                layout = [
                    {
                        "order": {
                            **blank_order,
                            "stl_path": str(blank_result.output_path),
                            "stl_origin_x_mm": 0.0,
                            "stl_origin_y_mm": 0.0,
                        },
                        "x_mm": slot["x_mm"],
                        "y_mm": slot["y_mm"],
                    }
                    for slot in selected_slots
                ]
                batch_dir = self.settings.generated_dir / "batches" / batch["id"]
                manifest_path = write_batch_manifest(batch["id"], layout, batch_dir / f"{batch['id']}_manifest.json")
                batch = self.database.update_batch_files(batch["id"], manifest_path=manifest_path)

                plate_result = self.generator.combine_batch_stl(batch["id"], layout)
                if not plate_result.success or not plate_result.output_path:
                    raise ValueError(plate_result.message)
                plate_scad = Path(plate_result.extra["plate_scad_path"]) if plate_result.extra else None
                batch = self.database.update_batch_files(batch["id"], plate_scad_path=plate_scad, plate_stl_path=plate_result.output_path)

                slicing_result = self.slicer.slice_plate(plate_result.output_path, batch["id"], insert_filament_change=False)
                if not slicing_result.success or not slicing_result.output_path:
                    raise ValueError(slicing_result.message)
                batch = self.database.update_batch_files(batch["id"], gcode_path=slicing_result.output_path)

                print_result = self.printer.upload_and_print(slicing_result.output_path)
                if not print_result.success:
                    raise ValueError(print_result.message)
            except ValueError as exc:
                if previous_batch is not None:
                    self._update_blank_batch_slots(previous_batch, previous_slots, status=previous_batch["status"], error_message=str(exc))
                else:
                    self.database.update_batch_files(batch["id"], status=statuses.ERROR, error_message=str(exc))
                raise

            printing_slots = self._set_slot_states(queued_slots, selected_indices, BLANK_STATE_PRINTING)
            batch = self._update_blank_batch_slots(batch, printing_slots, status=statuses.PRINTING)
            selected_printing_slots = [slot for slot in printing_slots if int(slot["index"]) in selected_indices]
            self._overlay_autorun_enabled = False
            return {
                "batch": batch,
                "message": print_result.message,
                "slots": selected_printing_slots,
                "requested_count": target_total,
                "selected_count": len(selected_printing_slots),
                "blank_preview": self.blank_table_preview(),
            }

    def build_print_batches(self) -> dict[str, Any]:
        blank_batch = self.database.latest_printed_blank_batch()
        if blank_batch is None:
            return {
                "batches_created": 0,
                "batches": [],
                "diagnostics": ["Print blanks first, then prepare paid orders for overlay printing."],
            }

        raw_slots = blank_batch.get("metadata", {}).get("slots", [])
        if not isinstance(raw_slots, list) or not raw_slots:
            return {
                "batches_created": 0,
                "batches": [],
                "diagnostics": [f"Blank batch {blank_batch['id']} has no saved slots."],
            }
        blank_width = float(blank_batch.get("metadata", {}).get("blank_width_mm") or 64.0)
        blank_height = float(blank_batch.get("metadata", {}).get("blank_height_mm") or 30.0)
        assignments = self._overlay_assignments(blank_batch)
        slots = self._normalize_blank_slots(raw_slots, blank_width, blank_height, self._default_blank_slot_state(blank_batch), assignments)

        candidates = self.database.list_orders_by_statuses([statuses.STL_READY], ascending=True)
        ready_orders = [order for order in candidates if order.get("paid_at")]
        batches: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        skipped = len(candidates) - len(ready_orders)
        if skipped:
            diagnostics.append(f"Skipped {skipped} unpaid STL-ready order(s).")

        used_slot_indices = {
            int(batch["slot_index"])
            for batch in self.database.overlay_batches_for_blank(blank_batch["id"])
            if batch.get("slot_index") is not None
        }
        available_slots = [
            slot
            for slot in slots
            if slot["state_code"] == BLANK_STATE_PRINTED and int(slot["index"]) not in used_slot_indices
        ]
        blocked_slots = [
            slot
            for slot in slots
            if slot["state_code"] != BLANK_STATE_PRINTED and int(slot["index"]) not in used_slot_indices
        ]
        if blocked_slots:
            diagnostics.append(f"Skipped {len(blocked_slots)} blank slot(s) that are not ready for overlay.")
        if ready_orders and not available_slots:
            diagnostics.append(f"Blank batch {blank_batch['id']} has no free overlay slots.")

        waiting_without_slot = 0
        for order in ready_orders:
            if not available_slots:
                waiting_without_slot += 1
                continue
            try:
                design = self.catalog.get_design(order["design_id"])
            except DesignCatalogError as exc:
                self.database.update_order_status(order["id"], statuses.ERROR, str(exc))
                diagnostics.append(str(exc))
                continue

            slot_position = next(
                (
                    index
                    for index, slot in enumerate(available_slots)
                    if self._blank_slot_matches_order_size(slot, order) and self._blank_slot_fits_order(slot, order)
                ),
                None,
            )
            if slot_position is None:
                order_label = order.get("order_number") or order["id"]
                if available_slots and order.get("design_id") == BLANK_DESIGN_ID and not any(
                    self._blank_slot_matches_order_size(slot, order) for slot in available_slots
                ):
                    size_label = order.get("size_label") or order.get("size_id") or ""
                    diagnostics.append(f"Order {order_label} size {size_label} does not match available blank slots.")
                else:
                    diagnostics.append(f"Order {order_label} does not fit any enabled blank slot.")
                continue
            slot = available_slots.pop(slot_position)
            overlay_result = self.generator.generate_overlay_stl(order, design)
            if not overlay_result.success or not overlay_result.output_path:
                self.database.update_order_status(order["id"], statuses.ERROR, overlay_result.message)
                diagnostics.append(overlay_result.message)
                continue

            overlay_order = {
                **order,
                "stl_path": str(overlay_result.output_path),
                "stl_origin_x_mm": 0.0,
                "stl_origin_y_mm": 0.0,
                "print_width_mm": float(order["width_mm"]),
                "print_height_mm": float(order["height_mm"]),
            }
            layout = [{"order": overlay_order, "x_mm": slot["x_mm"], "y_mm": slot["y_mm"]}]
            batch = self.database.create_batch(
                layout,
                kind="overlay",
                parent_batch_id=blank_batch["id"],
                slot_index=int(slot["index"]),
                metadata_payload={"blank_batch_id": blank_batch["id"], "slot": slot},
            )
            batch_dir = self.settings.generated_dir / "batches" / batch["id"]
            manifest_path = write_batch_manifest(batch["id"], layout, batch_dir / f"{batch['id']}_manifest.json")
            batch = self.database.update_batch_files(batch["id"], manifest_path=manifest_path)

            plate_result = self.generator.combine_batch_stl(batch["id"], layout)
            if plate_result.success and plate_result.output_path:
                plate_scad = Path(plate_result.extra["plate_scad_path"]) if plate_result.extra else None
                batch = self.database.update_batch_files(
                    batch["id"],
                    plate_scad_path=plate_scad,
                    plate_stl_path=plate_result.output_path,
                )
                slots = self._set_slot_states(slots, {int(slot["index"])}, BLANK_STATE_OVERLAY_QUEUED)
                blank_batch = self._update_blank_batch_slots(blank_batch, slots)
            else:
                batch = self.database.update_batch_files(batch["id"], status=statuses.ERROR, error_message=plate_result.message)
                self.database.update_order_status(order["id"], statuses.ERROR, plate_result.message)
                diagnostics.append(plate_result.message)

            batches.append(batch)

        if waiting_without_slot:
            diagnostics.append(f"Only {len(batches)} free blank slot(s) available for {len(ready_orders)} ready order(s).")

        return {"batches_created": len(batches), "batches": batches, "diagnostics": diagnostics}

    def ensure_printer_connection(self, reason: str = "operation") -> AdapterResult:
        if not self.settings.octoprint.enabled:
            return AdapterResult(True, "OctoPrint disabled; printer connection check skipped", extra={"skipped": True})
        result = self.printer.ensure_connected()
        if result.success:
            return result
        return AdapterResult(False, f"Printer connection check failed during {reason}: {result.message}", extra=result.extra)

    def prepare_queue(self, fail_on_connection_error: bool = True) -> dict[str, Any]:
        with self._queue_lock:
            recovered_orders = self.database.reset_orphan_printing_orders()
            generation = self.process_paid_orders()
            rebuilt_queued_batches = {"batch_ids": [], "order_ids": [], "orders_reset": 0}
            batching = self.build_print_batches()
            if recovered_orders["order_ids"]:
                batching["diagnostics"].append(
                    f"Recovered {len(recovered_orders['order_ids'])} paid order(s) that were marked printing before preparation."
                )
            return {
                "printer_connection": {
                    "ok": True,
                    "message": "Printer connection check skipped for table preparation",
                    "skipped": True,
                },
                "recovered_orders": recovered_orders,
                "rebuilt_queued_batches": rebuilt_queued_batches,
                "generation": generation,
                "batching": batching,
            }

    def _printer_ready_for_start(self) -> AdapterResult:
        connection = self.ensure_printer_connection("print start")
        if not connection.success:
            return connection

        printer_result = self.printer.get_printer()
        if not printer_result.success:
            return AdapterResult(False, printer_result.message)

        job_result = self.printer.get_job()
        if not job_result.success:
            return AdapterResult(False, job_result.message)

        printer_data = printer_result.extra if isinstance(printer_result.extra, dict) else {}
        job_data = job_result.extra if isinstance(job_result.extra, dict) else {}
        state = printer_data.get("state")
        flags = state.get("flags") if isinstance(state, dict) and isinstance(state.get("flags"), dict) else {}
        raw_state = str(state.get("text") if isinstance(state, dict) else state or "")
        job_state = str(job_data.get("state") or "")
        combined_state = f"{raw_state} {job_state}".lower()

        if flags.get("closedOrError") or flags.get("error"):
            return AdapterResult(False, raw_state or "Printer reports an error")
        if flags.get("printing") or flags.get("paused") or flags.get("pausing") or flags.get("cancelling"):
            return AdapterResult(False, raw_state or "Printer is busy")
        if flags and not (flags.get("operational") or flags.get("ready")):
            return AdapterResult(False, raw_state or "Printer is not operational")
        if any(word in combined_state for word in ["offline", "closed", "error", "disconnected"]):
            return AdapterResult(False, raw_state or job_state or "Printer is offline")
        if any(word in combined_state for word in ["printing", "paused", "pausing", "cancelling"]):
            return AdapterResult(False, raw_state or job_state or "Printer is busy")

        return AdapterResult(True, raw_state or job_state or "Printer ready")

    def print_batch(self, batch_id: str) -> dict[str, Any]:
        with self._queue_lock:
            batch = self.database.get_batch(batch_id)
            if batch["status"] == statuses.PRINTING:
                raise ValueError("Batch is already printing")
            if batch["status"] != statuses.QUEUED:
                raise ValueError("Only queued batches can be printed")
            if any(item.get("archived_at") for item in batch["items"]):
                raise ValueError("Batch contains deleted orders. Prepare a new table before printing.")
            if any(item.get("status") != statuses.QUEUED for item in batch["items"]):
                raise ValueError("Batch contains orders that are not queued for print. Prepare a new table before printing.")

            plate_stl = Path(batch["plate_stl_path"]) if batch.get("plate_stl_path") else None
            if plate_stl is None or not plate_stl.exists():
                raise ValueError("Batch plate STL is not ready")

            printer_ready = self._printer_ready_for_start()
            if not printer_ready.success:
                raise ValueError(f"Printer is not ready: {printer_ready.message}")

            gcode_path = Path(batch["gcode_path"]) if batch.get("gcode_path") else None
            if gcode_path is not None and not gcode_path.exists():
                gcode_path = None

            if gcode_path is None:
                if not self.settings.slicer.enabled:
                    raise ValueError("Slicing is disabled in config/app.yaml")
                if batch.get("kind") == "blank":
                    slicing_result = self.slicer.slice_plate(plate_stl, batch_id, insert_filament_change=False)
                elif batch.get("kind") == "overlay":
                    slicing_result = self.slicer.slice_overlay(plate_stl, batch_id, self._overlay_safety_for_batch(batch))
                else:
                    slicing_result = self.slicer.slice_plate(plate_stl, batch_id, self._batch_filament_change_height(batch))
                if not slicing_result.success or not slicing_result.output_path:
                    self.database.update_batch_files(batch_id, error_message=slicing_result.message)
                    raise ValueError(slicing_result.message)
                gcode_path = slicing_result.output_path
                batch = self.database.update_batch_files(batch_id, gcode_path=gcode_path)

            print_result = self.printer.upload_and_print(gcode_path)
            if not print_result.success:
                self.database.update_batch_files(batch_id, error_message=print_result.message)
                raise ValueError(print_result.message)

            batch = self.database.update_batch_files(batch_id, status=statuses.PRINTING)
            for item in batch["items"]:
                self.database.update_order_status(item["order_id"], statuses.PRINTING)
            if batch.get("kind") == "overlay":
                self._set_parent_blank_slot_state(batch, BLANK_STATE_OVERLAY_PRINTING)
            elif batch.get("kind") == "blank":
                metadata = batch.get("metadata", {})
                raw_slots = metadata.get("slots", []) if isinstance(metadata, dict) else []
                slots = self._normalize_blank_slots(
                    raw_slots,
                    float(metadata.get("blank_width_mm") or 56.0),
                    float(metadata.get("blank_height_mm") or 24.0),
                    BLANK_STATE_QUEUED,
                )
                queued_indices = {int(slot["index"]) for slot in slots if slot["state_code"] == BLANK_STATE_QUEUED}
                if queued_indices:
                    self._update_blank_batch_slots(batch, self._set_slot_states(slots, queued_indices, BLANK_STATE_PRINTING))
            return {"batch": self.database.get_batch(batch_id), "message": print_result.message}

    def _overlay_safety_for_batch(self, batch: dict[str, Any]) -> OverlaySafetySpec:
        if not batch.get("items"):
            raise ValueError("Overlay batch has no order")
        order = self.database.get_order(batch["items"][0]["order_id"])
        slot = batch.get("metadata", {}).get("slot")
        if not isinstance(slot, dict):
            raise ValueError("Overlay batch has no saved blank slot")
        relief_height = relief_height_for_params(order.get("model_params") or {}, order.get("selected_elements") or [])
        return OverlaySafetySpec(
            base_thickness_mm=float(order["thickness_mm"]),
            relief_height_mm=float(relief_height),
            safety_clearance_mm=OVERLAY_SAFETY_CLEARANCE_MM,
            slot_x_mm=float(slot["x_mm"]),
            slot_y_mm=float(slot["y_mm"]),
            slot_width_mm=float(slot["width_mm"]),
            slot_height_mm=float(slot["height_mm"]),
            bed_size_mm=self.settings.queue.bed_size_mm,
        )

    def _batch_filament_change_height(self, batch: dict[str, Any]) -> float:
        heights = {
            _filament_change_height(item, self.settings.model.base_height_mm)
            for item in batch.get("items", [])
            if item.get("archived_at") is None
        }
        if not heights:
            return round(self.settings.model.base_height_mm, 3)
        if len(heights) > 1:
            formatted = ", ".join(f"{height:g}" for height in sorted(heights))
            raise ValueError(f"Batch contains mixed base heights: {formatted} mm")
        return next(iter(heights))

    def run_overlay_queue(self) -> dict[str, Any]:
        prepared = self.prepare_queue()
        with self._queue_lock:
            self._overlay_autorun_enabled = True
            self._overlay_next_start_at = 0.0
        started = self.start_next_overlay_if_ready()
        return {"autorun_enabled": True, "prepared": prepared, "started": started}

    def start_next_overlay_if_ready(self) -> dict[str, Any]:
        with self._queue_lock:
            if not self._overlay_autorun_enabled:
                return {"started": False, "reason": "autorun_disabled"}
            remaining = self._overlay_next_start_at - time.monotonic()
            if remaining > 0:
                return {"started": False, "reason": "handoff_delay", "retry_after_seconds": int(remaining) + 1}
            if self.database.has_printing_batches():
                return {"started": False, "reason": "printer_busy"}
            batch = self.database.next_queued_overlay_batch()
            if batch is None:
                self._overlay_autorun_enabled = False
                return {"started": False, "reason": "empty_queue", "autorun_enabled": False}

        try:
            result = self.print_batch(batch["id"])
        except ValueError as exc:
            return {"started": False, "reason": "start_failed", "message": str(exc), "batch_id": batch["id"]}
        return {"started": True, "batch": result["batch"], "message": result["message"]}

    def _record_completed_batches_for_autorun(self, result: dict[str, Any]) -> None:
        completed_overlay = any(batch.get("kind") == "overlay" for batch in result.get("batches", []))
        if not completed_overlay:
            return
        with self._queue_lock:
            if self._overlay_autorun_enabled:
                self._overlay_next_start_at = time.monotonic() + OVERLAY_AUTORUN_DELAY_SECONDS

    def set_printer_heaters(self, bed_target_c: int, hotend_target_c: int) -> dict[str, Any]:
        connection = self.ensure_printer_connection("heater control")
        if not connection.success:
            raise ValueError(connection.message)

        bed_result = self.printer.set_bed_target(bed_target_c)
        tool_result = self.printer.set_tool_target(hotend_target_c)
        failures = [result.message for result in [bed_result, tool_result] if not result.success]
        if failures:
            raise ValueError(" | ".join(failures))
        return {
            "ok": True,
            "message": " | ".join([bed_result.message, tool_result.message]),
            "bed_target_c": int(bed_target_c),
            "hotend_target_c": int(hotend_target_c),
        }

    def preheat_bed(self) -> dict[str, Any]:
        return self.set_printer_heaters(
            self.settings.printer_control.bed_preheat_c,
            self.settings.printer_control.hotend_preheat_c,
        )

    def cool_bed(self) -> dict[str, Any]:
        return self.set_printer_heaters(0, 0)

    def stop_print(self) -> dict[str, Any]:
        with self._queue_lock:
            self._overlay_autorun_enabled = False
            self._overlay_next_start_at = 0.0
            connection = self.ensure_printer_connection("print stop")
            if not connection.success:
                raise ValueError(connection.message)

            cancel_result = self.printer.cancel_job()
            if not cancel_result.success:
                raise ValueError(cancel_result.message)

            bed_result = self.printer.set_bed_target(0)
            reset = self.database.reset_printing_to_queue()
            updated_batches = []
            for batch in reset["batches"]:
                if batch.get("kind") == "blank":
                    metadata = batch.get("metadata", {})
                    raw_slots = metadata.get("slots", []) if isinstance(metadata, dict) else []
                    slots = self._normalize_blank_slots(
                        raw_slots,
                        float(metadata.get("blank_width_mm") or 56.0),
                        float(metadata.get("blank_height_mm") or 24.0),
                        BLANK_STATE_QUEUED,
                    )
                    printing_indices = {int(slot["index"]) for slot in slots if slot["state_code"] == BLANK_STATE_PRINTING}
                    if printing_indices:
                        batch = self._update_blank_batch_slots(
                            batch,
                            self._set_slot_states(slots, printing_indices, BLANK_STATE_QUEUED),
                            status=statuses.QUEUED,
                        )
                elif batch.get("kind") == "overlay":
                    self._set_parent_blank_slot_state(batch, BLANK_STATE_OVERLAY_QUEUED)
                updated_batches.append(batch)
            reset["batches"] = updated_batches
            messages = [cancel_result.message]
            if bed_result.success:
                messages.append(bed_result.message)
            else:
                messages.append(f"Bed heater off failed: {bed_result.message}")

            return {
                "ok": True,
                "message": " | ".join(messages),
                "batches": reset["batches"],
                "orders_updated": reset["orders_updated"],
                "bed_off": bed_result.success,
            }

    def resume_print(self) -> dict[str, Any]:
        connection = self.ensure_printer_connection("print resume")
        if not connection.success:
            raise ValueError(connection.message)

        result = self.printer.resume_print()
        if not result.success:
            raise ValueError(result.message)
        return {"ok": True, "message": result.message}

    def _complete_printing_batches(self) -> dict[str, Any]:
        result = self.database.complete_printing_batches()
        updated_batches: list[dict[str, Any]] = []
        for batch in result.get("batches", []):
            if batch.get("kind") == "blank":
                metadata = batch.get("metadata", {})
                raw_slots = metadata.get("slots", []) if isinstance(metadata, dict) else []
                slots = self._normalize_blank_slots(
                    raw_slots,
                    float(metadata.get("blank_width_mm") or 56.0),
                    float(metadata.get("blank_height_mm") or 24.0),
                    BLANK_STATE_PRINTED,
                )
                completed_indices = {
                    int(slot["index"])
                    for slot in slots
                    if slot["state_code"] in {BLANK_STATE_QUEUED, BLANK_STATE_PRINTING}
                }
                if completed_indices:
                    batch = self._update_blank_batch_slots(
                        batch,
                        self._set_slot_states(slots, completed_indices, BLANK_STATE_PRINTED),
                        status=statuses.PRINTED,
                    )
            elif batch.get("kind") == "overlay":
                self._set_parent_blank_slot_state(batch, BLANK_STATE_OVERLAY_DONE)
            updated_batches.append(self.database.get_batch(batch["id"]))
        result["batches"] = updated_batches
        return result

    def complete_printing_batches_if_done(self, printer_status: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.database.has_printing_batches():
            return {"completed": False, "batches": [], "orders_updated": 0}

        if printer_status is not None:
            if printer_status.get("state") != "done_printing":
                return {"completed": False, "batches": [], "orders_updated": 0}
            result = self._complete_printing_batches()
            self._record_completed_batches_for_autorun(result)
            return {"completed": bool(result["batches"] or result["orders_updated"]), **result}

        if not self.settings.octoprint.enabled:
            return {"completed": False, "batches": [], "orders_updated": 0}

        job_result = self.printer.get_job()
        printer_result = self.printer.get_printer()
        job_data = job_result.extra if job_result.success and isinstance(job_result.extra, dict) else {}
        printer_data = printer_result.extra if printer_result.success and isinstance(printer_result.extra, dict) else {}

        if not _printer_reports_print_done(job_data, printer_data):
            return {"completed": False, "batches": [], "orders_updated": 0}

        result = self._complete_printing_batches()
        self._record_completed_batches_for_autorun(result)
        return {"completed": bool(result["batches"] or result["orders_updated"]), **result}

    def run_queue(self) -> dict[str, Any]:
        completion = self.complete_printing_batches_if_done()
        prepared = self.prepare_queue(fail_on_connection_error=False)
        started = self.start_next_overlay_if_ready()
        return {"completion": completion, "prepared": prepared, "started": started}
