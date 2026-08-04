from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from typing import Any
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .adapters import AdapterResult, CuraEngineSlicer, NullNotifier, OctoPrintController, OpenScadModelGenerator
from .config import AppSettings, load_settings
from .database import Database
from .designs import DesignCatalog, DesignCatalogError
from .editor import (
    bounds_for_params,
    custom_text_layout_for_order,
    designs_for_editor,
    normalize_editor_params,
    preview_order_payload,
    save_design_preset,
)
from .models import BlankPrintRequest, BlankSlotUpdate, InternalOrderCreate, OrderCreate, PaymentStatusUpdate, StatusUpdate
from .network import local_ipv4_addresses, qr_data_uri, wifi_qr_payload
from .services import OrderBlockedError, OrderService
from .statistics import XLSX_MEDIA_TYPE, build_statistics_workbook
from .worker import worker_loop


logger = logging.getLogger(__name__)


def _public_order(order: dict[str, Any]) -> dict[str, Any]:
    public = dict(order)
    for key in [
        "stl_path",
        "wrapper_scad_path",
        "gcode_path",
        "batch_id",
        "model_params",
        "client_id",
        "phone",
        "telegram_chat_id",
        "telegram_user_id",
        "telegram_username",
    ]:
        public.pop(key, None)
    return public


def _operator_order(order: dict[str, Any]) -> dict[str, Any]:
    public = dict(order)
    public.pop("phone", None)
    return public


def _order_with_catalog_price(order: dict[str, Any], catalog: DesignCatalog) -> dict[str, Any]:
    if order.get("price") is not None:
        return order

    enriched = dict(order)
    try:
        design = catalog.get_design(str(order.get("design_id") or ""))
    except DesignCatalogError:
        return enriched

    size = next((item for item in design.get("sizes", []) if item.get("id") == order.get("size_id")), None)
    if size is not None and size.get("price") is not None:
        enriched["price"] = size["price"]
    return enriched


PRINTER_STATUS_LABELS = {
    "offline": "offline",
    "waiting": "waiting",
    "heating": "heating",
    "printing": "printing",
    "done_printing": "done printing",
}

ADMIN_PIN_LOCK_SECONDS = [60, 15 * 60, 60 * 60, 24 * 60 * 60]


@dataclass
class AdminPinAttempt:
    failures: int = 0
    lock_level: int = 0
    locked_until: float = 0.0


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


def _temperature_rows(printer_data: dict[str, Any]) -> list[dict[str, Any]]:
    temperatures = printer_data.get("temperature")
    if not isinstance(temperatures, dict):
        return []

    rows: list[dict[str, Any]] = []
    for name, reading in temperatures.items():
        if not isinstance(reading, dict):
            continue
        rows.append(
            {
                "name": str(name),
                "actual": _number(reading.get("actual")),
                "target": _number(reading.get("target")),
            }
        )
    return rows


def _printer_state_text(printer_data: dict[str, Any]) -> str:
    state = printer_data.get("state")
    if isinstance(state, dict):
        return str(state.get("text") or "")
    if state is not None:
        return str(state)
    return ""


def _printer_flags(printer_data: dict[str, Any]) -> dict[str, Any]:
    state = printer_data.get("state")
    if not isinstance(state, dict):
        return {}
    flags = state.get("flags")
    return flags if isinstance(flags, dict) else {}


def _printer_status_payload(print_controller: OctoPrintController) -> dict[str, Any]:
    job_result = print_controller.get_job()
    printer_result = print_controller.get_printer()

    job_data = job_result.extra if job_result.success and isinstance(job_result.extra, dict) else {}
    printer_data = printer_result.extra if printer_result.success and isinstance(printer_result.extra, dict) else {}
    progress = job_data.get("progress") if isinstance(job_data.get("progress"), dict) else {}

    completion = _number(progress.get("completion"))
    elapsed_seconds = _seconds(progress.get("printTime"))
    remaining_seconds = _seconds(progress.get("printTimeLeft"))
    temperatures = _temperature_rows(printer_data)
    flags = _printer_flags(printer_data)

    raw_state = _printer_state_text(printer_data) or str(job_data.get("state") or "")
    raw_lower = raw_state.lower()
    has_error_flag = bool(flags.get("closedOrError") or flags.get("error"))
    looks_disconnected = any(word in raw_lower for word in ["offline", "closed", "error", "disconnected"])
    has_connected_flag = bool(
        flags.get("operational")
        or flags.get("ready")
        or flags.get("printing")
        or flags.get("paused")
        or flags.get("pausing")
        or flags.get("cancelling")
    )

    online = (job_result.success or printer_result.success) and not has_error_flag and not looks_disconnected
    if printer_result.success and flags:
        online = has_connected_flag and not has_error_flag and not looks_disconnected

    job_state_lower = str(job_data.get("state") or "").lower()
    is_busy = bool(
        flags.get("printing")
        or flags.get("paused")
        or flags.get("pausing")
        or flags.get("cancelling")
        or any(word in job_state_lower for word in ["printing", "paused", "pausing", "cancelling"])
    )
    is_paused = bool(flags.get("paused") or "paused" in job_state_lower or "paused" in raw_lower)
    is_printing = bool(flags.get("printing") or "printing" in job_state_lower)
    has_print_progress = bool(
        (completion is not None and completion > 0)
        or (elapsed_seconds is not None and elapsed_seconds > 0)
    )
    is_done = bool(
        not is_busy
        and (
            (completion is not None and completion >= 99.9)
            or (elapsed_seconds is not None and elapsed_seconds > 0 and remaining_seconds == 0)
        )
    )
    is_heating = any(
        row["target"] is not None
        and row["target"] > 0
        and (row["actual"] is None or row["actual"] + 2 < row["target"])
        for row in temperatures
    )

    if not online:
        state = "offline"
    elif is_busy:
        state = "printing"
    elif is_done:
        state = "done_printing"
    elif is_heating:
        state = "heating"
    else:
        state = "waiting"

    messages = [result.message for result in [job_result, printer_result] if not result.success]
    return {
        "online": online,
        "online_label": "online" if online else "offline",
        "state": state,
        "label": PRINTER_STATUS_LABELS[state],
        "raw_state": raw_state,
        "progress_percent": completion,
        "elapsed_seconds": elapsed_seconds,
        "remaining_seconds": remaining_seconds,
        "message": " | ".join(messages),
        "temperatures": temperatures,
        "busy": is_busy,
        "paused": is_paused,
        "filament_change_required": is_paused and has_print_progress,
    }


def _is_running_inside_docker() -> bool:
    if Path("/.dockerenv").exists():
        return True
    cgroup_path = Path("/proc/1/cgroup")
    if not cgroup_path.exists():
        return False
    try:
        cgroup = cgroup_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(marker in cgroup for marker in ["docker", "containerd", "kubepods"])


def _docker_status_payload(base_dir: Path) -> dict[str, Any]:
    if _is_running_inside_docker():
        return {
            "online": True,
            "online_label": "online",
            "state": "running",
            "label": "docker server",
            "message": "app runs in Docker container",
        }

    docker_path = shutil.which("docker")
    if not docker_path:
        return {
            "online": False,
            "online_label": "offline",
            "state": "offline",
            "label": "offline",
            "message": "Docker CLI not found",
        }

    run_kwargs: dict[str, Any] = {
        "cwd": base_dir,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 2.0,
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        completed = subprocess.run(
            [docker_path, "info", "--format", "{{json .ServerVersion}}"],
            **run_kwargs,
        )
    except subprocess.TimeoutExpired:
        return {
            "online": False,
            "online_label": "offline",
            "state": "offline",
            "label": "offline",
            "message": "Docker daemon response timed out",
        }
    except OSError as exc:
        return {
            "online": False,
            "online_label": "offline",
            "state": "offline",
            "label": "offline",
            "message": f"Docker check failed: {exc}",
        }

    raw_stdout = (completed.stdout or "").strip()
    raw_stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        message = (raw_stderr or raw_stdout or "docker info failed").splitlines()[0]
        return {
            "online": False,
            "online_label": "offline",
            "state": "offline",
            "label": "offline",
            "message": message,
        }

    try:
        server_version = json.loads(raw_stdout) if raw_stdout else ""
    except json.JSONDecodeError:
        server_version = raw_stdout.strip('"')
    server_version = str(server_version or "").strip()
    return {
        "online": True,
        "online_label": "online",
        "state": "running",
        "label": "docker server",
        "message": f"server {server_version}" if server_version else "Docker daemon reachable",
        "server_version": server_version or None,
    }


def create_app(
    settings: AppSettings | None = None,
    generator: OpenScadModelGenerator | None = None,
    slicer: CuraEngineSlicer | None = None,
    printer: OctoPrintController | None = None,
    notifier: NullNotifier | None = None,
    start_worker: bool = True,
) -> FastAPI:
    app_settings = settings or load_settings()
    database = Database(app_settings.database_path, app_settings.database_url)
    catalog = DesignCatalog(app_settings.designs_dir)
    model_generator = generator or OpenScadModelGenerator(app_settings)
    cura_slicer = slicer or CuraEngineSlicer(app_settings)
    print_controller = printer or OctoPrintController(app_settings)
    app_notifier = notifier or NullNotifier()
    service = OrderService(app_settings, database, catalog, model_generator, cura_slicer, print_controller, app_notifier)
    admin_pin_attempts: dict[str, AdminPinAttempt] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_settings.ensure_directories()
        database.init_schema()
        startup_connection = AdapterResult(True, "Printer connection check scheduled", extra={"pending": True})
        app.state.printer_connection = startup_connection

        async def record_startup_connection() -> None:
            result = await asyncio.to_thread(service.ensure_printer_connection, "server startup")
            if not result.success:
                logger.warning(result.message)
            app.state.printer_connection = result

        startup_connection_task = asyncio.create_task(record_startup_connection())
        task: asyncio.Task[Any] | None = None
        if start_worker:
            task = asyncio.create_task(worker_loop(service, app_settings.worker_poll_seconds))
        app.state.settings = app_settings
        app.state.database = database
        app.state.catalog = catalog
        app.state.service = service
        try:
            yield
        finally:
            if not startup_connection_task.done():
                startup_connection_task.cancel()
                try:
                    await startup_connection_task
                except asyncio.CancelledError:
                    pass
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Keychain Fair", version="0.1.0", lifespan=lifespan)
    static_dir = app_settings.base_dir / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(Exception)
    async def unhandled_exception_json(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error during %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    def admin_attempt_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def pin_lock_response(attempt: AdminPinAttempt) -> HTTPException:
        retry_after = max(1, int(attempt.locked_until - time.monotonic()))
        return HTTPException(
            status_code=429,
            detail={
                "message": f"Too many invalid PIN attempts. Try again in {retry_after} seconds.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    def require_admin(
        request: Request,
        x_admin_pin: str | None = Header(default=None, alias="X-Admin-Pin"),
    ) -> None:
        key = admin_attempt_key(request)
        attempt = admin_pin_attempts.setdefault(key, AdminPinAttempt())
        now = time.monotonic()

        if attempt.locked_until > now:
            raise pin_lock_response(attempt)

        if x_admin_pin and secrets.compare_digest(x_admin_pin, app_settings.admin_pin):
            admin_pin_attempts.pop(key, None)
            return

        attempt.failures += 1
        if attempt.failures >= 3:
            duration = ADMIN_PIN_LOCK_SECONDS[min(attempt.lock_level, len(ADMIN_PIN_LOCK_SECONDS) - 1)]
            attempt.failures = 0
            attempt.lock_level += 1
            attempt.locked_until = now + duration
            raise pin_lock_response(attempt)

        remaining = 3 - attempt.failures
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Invalid admin PIN",
                "attempts_remaining": remaining,
            },
        )

    def require_cashier(
        request: Request,
        x_cashier_pin: str | None = Header(default=None, alias="X-Cashier-Pin"),
    ) -> None:
        key = admin_attempt_key(request)
        attempt = admin_pin_attempts.setdefault(key, AdminPinAttempt())
        now = time.monotonic()

        if attempt.locked_until > now:
            raise pin_lock_response(attempt)

        if x_cashier_pin and secrets.compare_digest(x_cashier_pin, app_settings.admin_pin):
            admin_pin_attempts.pop(key, None)
            return

        attempt.failures += 1
        if attempt.failures >= 3:
            duration = ADMIN_PIN_LOCK_SECONDS[min(attempt.lock_level, len(ADMIN_PIN_LOCK_SECONDS) - 1)]
            attempt.failures = 0
            attempt.lock_level += 1
            attempt.locked_until = now + duration
            raise pin_lock_response(attempt)

        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid cashier PIN", "attempts_remaining": 3 - attempt.failures},
        )

    def require_internal(
        x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
    ) -> None:
        if not app_settings.internal_api_token:
            raise HTTPException(status_code=503, detail="Internal API token is not configured")
        if x_internal_token and secrets.compare_digest(x_internal_token, app_settings.internal_api_token):
            return
        raise HTTPException(status_code=401, detail="Invalid internal API token")

    @app.get("/")
    def buyer_page() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/admin")
    def admin_page() -> FileResponse:
        return FileResponse(static_dir / "admin.html")

    @app.get("/cashier")
    def cashier_page() -> FileResponse:
        return FileResponse(static_dir / "cashier.html")

    @app.get("/editor")
    def editor_page() -> FileResponse:
        return FileResponse(static_dir / "editor.html")

    @app.get("/admin/orders/{order_id}/3d")
    def admin_order_preview_page(order_id: str) -> FileResponse:
        return FileResponse(static_dir / "order-preview.html")

    @app.get("/status/{order_id}")
    def status_page(order_id: str) -> FileResponse:
        return FileResponse(static_dir / "status.html")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/designs")
    def get_designs() -> dict[str, Any]:
        return {"designs": catalog.load_public_designs()}

    @app.post("/api/orders", status_code=201)
    def create_order(payload: OrderCreate) -> dict[str, Any]:
        try:
            order = service.create_order(payload)
        except OrderBlockedError as exc:
            detail: dict[str, Any] = {"message": exc.message}
            headers = None
            if exc.retry_after_seconds is not None:
                detail["retry_after_seconds"] = exc.retry_after_seconds
                headers = {"Retry-After": str(exc.retry_after_seconds)}
            raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers) from exc
        except DesignCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"order": _public_order(order)}

    @app.post("/api/internal/orders", status_code=201, dependencies=[Depends(require_internal)])
    def create_internal_order(payload: InternalOrderCreate) -> dict[str, Any]:
        try:
            order = service.create_order(payload)
        except OrderBlockedError as exc:
            detail: dict[str, Any] = {"message": exc.message}
            headers = None
            if exc.retry_after_seconds is not None:
                detail["retry_after_seconds"] = exc.retry_after_seconds
                headers = {"Retry-After": str(exc.retry_after_seconds)}
            raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers) from exc
        except DesignCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"order": _public_order(order)}

    @app.post("/api/internal/order-preview/render.png", dependencies=[Depends(require_internal)])
    def render_internal_order_preview(payload: InternalOrderCreate) -> FileResponse:
        try:
            preview_order, design = service.build_order_preview(payload)
        except OrderBlockedError as exc:
            detail: dict[str, Any] = {"message": exc.message}
            headers = None
            if exc.retry_after_seconds is not None:
                detail["retry_after_seconds"] = exc.retry_after_seconds
                headers = {"Retry-After": str(exc.retry_after_seconds)}
            raise HTTPException(status_code=exc.status_code, detail=detail, headers=headers) from exc
        except DesignCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        cache_payload = {
            "render_version": "telegram-preview-colors-v1",
            "payload": payload.model_dump(mode="json"),
            "model_params": preview_order.get("model_params"),
            "selected_elements": preview_order.get("selected_elements"),
        }
        cache_key = hashlib.sha256(json.dumps(cache_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        result = model_generator.render_order_preview_png(cache_key, preview_order, design)
        if not result.success or not result.output_path:
            raise HTTPException(status_code=503, detail=result.message)
        return FileResponse(result.output_path, media_type="image/png", filename="order-preview.png")

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: str) -> dict[str, Any]:
        try:
            order = database.get_order(order_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc
        return {"order": _public_order(order)}

    @app.get("/api/admin/orders", dependencies=[Depends(require_admin)])
    def admin_orders(status: str | None = None) -> dict[str, Any]:
        return {"orders": [_operator_order(order) for order in database.list_orders(status)]}

    @app.get("/api/admin/statistics.xlsx", dependencies=[Depends(require_admin)])
    def admin_statistics_xlsx() -> Response:
        workbook = build_statistics_workbook(database.list_orders())
        return Response(
            content=workbook,
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": 'attachment; filename="keychain-fair-statistics.xlsx"'},
        )

    @app.get("/api/cashier/orders", dependencies=[Depends(require_cashier)])
    def cashier_orders() -> dict[str, Any]:
        return {"orders": [_operator_order(_order_with_catalog_price(order, catalog)) for order in database.list_orders()]}

    @app.post("/api/cashier/orders/{order_id}/payment", dependencies=[Depends(require_cashier)])
    def cashier_set_payment(order_id: str, payload: PaymentStatusUpdate) -> dict[str, Any]:
        try:
            order = service.set_status(order_id, payload.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"order": _operator_order(order)}

    @app.post("/api/admin/orders/{order_id}/status", dependencies=[Depends(require_admin)])
    def admin_set_status(order_id: str, payload: StatusUpdate) -> dict[str, Any]:
        try:
            order = service.set_status(order_id, payload.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"order": _operator_order(order)}

    @app.delete("/api/admin/orders/{order_id}", dependencies=[Depends(require_admin)])
    def admin_delete_order(order_id: str) -> dict[str, Any]:
        try:
            order = database.archive_order(order_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"order": _operator_order(order)}

    @app.post("/api/admin/orders/{order_id}/preview-3d", dependencies=[Depends(require_admin)])
    def admin_order_preview_3d(order_id: str) -> dict[str, Any]:
        try:
            order = database.get_order(order_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc

        try:
            design = catalog.get_design(str(order.get("design_id") or ""))
        except DesignCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        params = order.get("model_params")
        if not isinstance(params, dict):
            raise HTTPException(status_code=409, detail="Order has no saved model parameters")

        preview_id = f"prv_order_{uuid.uuid4().hex[:16]}"
        preview_order = dict(order)
        preview_order["id"] = preview_id
        result = model_generator.generate_preview_stl(preview_id, preview_order, design)
        if not result.success or not result.output_path:
            raise HTTPException(status_code=503, detail=result.message)

        return {
            "preview_id": preview_id,
            "bounds": bounds_for_params(design, params, order["selected_elements"]),
            "filament_change_height_mm": params.get("thickness_mm", order["thickness_mm"]),
            "stl_url": f"/api/admin/model-editor/previews/{preview_id}.stl",
            "text_layout": (result.extra or {}).get("text_layout") or custom_text_layout_for_order(order, design),
            "order": {
                "id": order["id"],
                "order_number": order.get("order_number") or "",
                "customer_name": order["customer_name"],
                "car_number": order["car_number"],
                "print_line_1": order["print_line_1"],
                "print_line_2": order["print_line_2"],
                "design_name": order["design_name"],
                "size_label": order["size_label"],
                "status": order["status"],
                "status_label": order.get("status_label", order["status"]),
            },
        }

    @app.post("/api/admin/print-queue/prepare", dependencies=[Depends(require_admin)])
    def admin_prepare_queue() -> dict[str, Any]:
        try:
            return service.prepare_queue()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/print-queue/run", dependencies=[Depends(require_admin)])
    def admin_run_queue() -> dict[str, Any]:
        try:
            return service.run_overlay_queue()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/blanks/print", dependencies=[Depends(require_admin)])
    def admin_print_blanks(payload: BlankPrintRequest | None = None) -> dict[str, Any]:
        try:
            return service.print_blank_batch(payload.target_count if payload else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/admin/blanks/{blank_batch_id}/slots/{slot_index}", dependencies=[Depends(require_admin)])
    def admin_update_blank_slot(blank_batch_id: str, slot_index: int, payload: BlankSlotUpdate) -> dict[str, Any]:
        try:
            batch = service.set_blank_slot_state(blank_batch_id, slot_index, payload.state_code, payload.print_enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Blank slot not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"batch": batch, "blank_preview": service.blank_table_preview()}

    @app.post("/api/admin/batches/{batch_id}/print", dependencies=[Depends(require_admin)])
    def admin_print_batch(batch_id: str) -> dict[str, Any]:
        try:
            return service.print_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Batch not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/printer/bed/heat", dependencies=[Depends(require_admin)])
    def admin_heat_bed() -> dict[str, Any]:
        try:
            return service.preheat_bed()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/printer/bed/cool", dependencies=[Depends(require_admin)])
    def admin_cool_bed() -> dict[str, Any]:
        try:
            return service.cool_bed()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/printer/stop", dependencies=[Depends(require_admin)])
    def admin_stop_print() -> dict[str, Any]:
        try:
            return service.stop_print()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/admin/printer/resume", dependencies=[Depends(require_admin)])
    def admin_resume_print() -> dict[str, Any]:
        try:
            return service.resume_print()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/admin/batches", dependencies=[Depends(require_admin)])
    def admin_batches() -> dict[str, Any]:
        return {
            "bed_size_mm": app_settings.queue.bed_size_mm,
            "batches": database.list_batches(),
            "blank_preview": service.blank_table_preview(),
        }

    @app.get("/api/admin/tools", dependencies=[Depends(require_admin)])
    def admin_tools() -> dict[str, Any]:
        open_scad = model_generator.check_ready()
        return {
            "openscad": {"ok": open_scad.success, "message": open_scad.message},
            "slicer": {"enabled": app_settings.slicer.enabled, "profile_path": str(app_settings.slicer.profile_path)},
            "octoprint": {"enabled": app_settings.octoprint.enabled, "base_url": app_settings.octoprint.base_url},
        }

    @app.get("/api/admin/model-editor/designs", dependencies=[Depends(require_admin)])
    def admin_model_editor_designs() -> dict[str, Any]:
        return {"designs": designs_for_editor(catalog.load_designs(), app_settings.model.base_height_mm)}

    def _editor_design_and_size(design_id: str, size_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        design = catalog.get_design(design_id)
        size = next((item for item in design.get("sizes", []) if item.get("id") == size_id), None)
        if size is None:
            raise DesignCatalogError(f"Unknown size '{size_id}' for design '{design_id}'")
        return design, size

    @app.post("/api/admin/model-editor/preview", dependencies=[Depends(require_admin)])
    def admin_model_editor_preview(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            design, size = _editor_design_and_size(str(payload.get("design_id") or ""), str(payload.get("size_id") or ""))
            params = normalize_editor_params(design, size, payload.get("editor_params"), app_settings.model.base_height_mm)
            elements = payload.get("elements")
            element_ids = [str(item) for item in elements] if isinstance(elements, list) else None
            sample = payload.get("sample_text") if isinstance(payload.get("sample_text"), dict) else None
            preview_id = f"prv_{uuid.uuid4().hex[:16]}"
            order = preview_order_payload(design, size, params, sample, element_ids)
            order["id"] = preview_id
            result = model_generator.generate_preview_stl(preview_id, order, design)
        except DesignCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not result.success or not result.output_path:
            raise HTTPException(status_code=503, detail=result.message)

        return {
            "preview_id": preview_id,
            "bounds": bounds_for_params(design, params, order["selected_elements"]),
            "filament_change_height_mm": params["thickness_mm"],
            "stl_url": f"/api/admin/model-editor/previews/{preview_id}.stl",
            "text_layout": (result.extra or {}).get("text_layout") or custom_text_layout_for_order(order, design),
        }

    @app.get("/api/admin/model-editor/previews/{preview_id}.stl", dependencies=[Depends(require_admin)])
    def admin_model_editor_preview_stl(preview_id: str) -> FileResponse:
        if not preview_id.startswith("prv_") or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in preview_id):
            raise HTTPException(status_code=404, detail="Preview not found")
        stl_path = app_settings.generated_dir / "editor" / "previews" / preview_id / f"{preview_id}.stl"
        if not stl_path.exists():
            raise HTTPException(status_code=404, detail="Preview not found")
        return FileResponse(stl_path, media_type="model/stl", filename=f"{preview_id}.stl")

    @app.post("/api/admin/model-editor/presets", dependencies=[Depends(require_admin)])
    def admin_model_editor_save_preset(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            design, size = _editor_design_and_size(str(payload.get("design_id") or ""), str(payload.get("size_id") or ""))
            params = normalize_editor_params(design, size, payload.get("editor_params"), app_settings.model.base_height_mm)
            paths = save_design_preset(
                design,
                size["id"],
                params,
                app_settings.base_dir / "data" / "design_backups",
                app_settings.model.base_height_mm,
            )
        except DesignCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Size not found") from exc
        return {"ok": True, "paths": paths, "editor_params": params}

    @app.get("/api/admin/printer/status", dependencies=[Depends(require_admin)])
    def admin_printer_status() -> dict[str, Any]:
        payload = _printer_status_payload(print_controller)
        payload["completed_prints"] = service.complete_printing_batches_if_done(payload)
        payload["overlay_autorun"] = service.start_next_overlay_if_ready()
        return payload

    @app.get("/api/admin/docker/status", dependencies=[Depends(require_admin)])
    def admin_docker_status() -> dict[str, Any]:
        return _docker_status_payload(app_settings.base_dir)

    @app.get("/api/system/info")
    def system_info() -> dict[str, Any]:
        addresses = local_ipv4_addresses()
        fallback_urls = [f"http://{address}:{app_settings.port}/" for address in addresses]
        configured_url = app_settings.public_url.strip() if app_settings.public_url else ""
        preferred = next((address for address in addresses if not address.startswith("127.")), "127.0.0.1")
        site_url = configured_url.rstrip("/") + "/" if configured_url else f"http://{preferred}:{app_settings.port}/"
        admin_url = f"{site_url.rstrip('/')}/admin"
        wifi_payload = wifi_qr_payload(app_settings.wifi_ssid, app_settings.wifi_password)
        return {
            "addresses": addresses,
            "fallback_urls": fallback_urls,
            "site_url": site_url,
            "admin_url": admin_url,
            "site_qr": qr_data_uri(site_url),
            "admin_qr": qr_data_uri(admin_url),
            "wifi_ssid": app_settings.wifi_ssid,
            "wifi_qr": qr_data_uri(wifi_payload),
            "qr": {
                "wifi": "/static/qr/customer_wifi.png",
                "order": "/static/qr/customer_order.png",
                "admin": "/static/qr/admin.png",
            },
        }

    return app


app = create_app()
