from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from . import statuses
from .adapters import AdapterResult, CuraEngineSlicer, NullNotifier, OctoPrintController, OpenScadModelGenerator
from .batching import layout_orders, write_batch_manifest
from .config import AppSettings
from .database import Database
from .designs import DesignCatalog, DesignCatalogError
from .editor import model_params_for_selection
from .models import OrderCreate


CONTENT_SHAPES = {"name", "car", "phone"}
LOOP_SHAPES = {"loop_left", "loop_right"}


def _filament_change_height(order_or_item: dict[str, Any], fallback: float) -> float:
    try:
        return round(float(order_or_item.get("thickness_mm", fallback)), 3)
    except (TypeError, ValueError):
        return round(fallback, 3)


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

    def create_order(self, payload: OrderCreate) -> dict[str, Any]:
        selection = self.catalog.validate_selection(payload.design_id, payload.size_id, payload.elements)
        self._validate_print_content(payload, selection)
        model_params = model_params_for_selection(selection, self.settings.model.base_height_mm)
        return self.database.create_order(payload.model_dump(), selection, model_params)

    def _validate_print_content(self, payload: OrderCreate, selection: Any) -> None:
        if selection.design.get("layout") != "stacked_plate":
            return

        selected_content = {item.get("shape") for item in selection.elements if item.get("shape") in CONTENT_SHAPES}
        selected_loop_sides = [item.get("shape") for item in selection.elements if item.get("shape") in LOOP_SHAPES]
        if not selected_content:
            raise DesignCatalogError("Выберите хотя бы один блок для печати: имя, номер авто или телефон")
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

    def build_print_batches(self) -> dict[str, Any]:
        candidates = self.database.list_orders_by_statuses([statuses.STL_READY], ascending=True)
        ready_orders = [order for order in candidates if order.get("paid_at")]
        batches: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        skipped = len(candidates) - len(ready_orders)
        if skipped:
            diagnostics.append(f"Skipped {skipped} unpaid STL-ready order(s).")

        while ready_orders:
            active_height = _filament_change_height(ready_orders[0], self.settings.model.base_height_mm)
            height_group = [
                order
                for order in ready_orders
                if _filament_change_height(order, self.settings.model.base_height_mm) == active_height
            ]
            layout = layout_orders(
                height_group,
                self.settings.queue.bed_size_mm,
                self.settings.queue.item_spacing_mm,
                self.settings.queue.max_items_per_plate,
            )
            if not layout:
                diagnostics.append("No STL-ready orders fit on the configured bed size.")
                break

            manifest_seed = self.settings.generated_dir / "batches" / "pending" / "manifest.json"
            batch = self.database.create_batch(layout, manifest_seed)
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
            else:
                batch = self.database.update_batch_files(batch["id"], status=statuses.ERROR, error_message=plate_result.message)
                for item in layout:
                    self.database.update_order_status(item["order"]["id"], statuses.ERROR, plate_result.message)
                diagnostics.append(plate_result.message)

            batches.append(batch)
            used_ids = {item["order"]["id"] for item in layout}
            ready_orders = [order for order in ready_orders if order["id"] not in used_ids]

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
            pending_ready_orders = [
                order
                for order in self.database.list_orders_by_statuses([statuses.STL_READY], ascending=True)
                if order.get("paid_at")
            ]
            if pending_ready_orders:
                rebuilt_queued_batches = self.database.reset_queued_batches_for_rebuild(
                    "Batch rebuilt because the print bed was prepared again."
                )
            else:
                rebuilt_queued_batches = {"batch_ids": [], "order_ids": [], "orders_reset": 0}
            batching = self.build_print_batches()
            if rebuilt_queued_batches["batch_ids"]:
                batching["diagnostics"].append(
                    f"Rebuilt {len(rebuilt_queued_batches['batch_ids'])} queued batch(es) with "
                    f"{rebuilt_queued_batches['orders_reset']} queued order(s)."
                )
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
            return {"batch": self.database.get_batch(batch_id), "message": print_result.message}

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
            connection = self.ensure_printer_connection("print stop")
            if not connection.success:
                raise ValueError(connection.message)

            cancel_result = self.printer.cancel_job()
            if not cancel_result.success:
                raise ValueError(cancel_result.message)

            bed_result = self.printer.set_bed_target(0)
            reset = self.database.reset_printing_to_queue()
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

    def run_queue(self) -> dict[str, Any]:
        return self.prepare_queue(fail_on_connection_error=False)
