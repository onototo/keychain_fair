from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse

from .catalog import load_catalog
from .config import AppSettings, load_settings
from .database import Database, OrderActionError
from .models import OrderCreate, SessionUpdate, ShipUpdate
from .offices import load_offices, search_offices
from .shipping import ManualShipmentGateway
from .shop import ShopError, prepare_order


logger = logging.getLogger(__name__)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    app_settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(app_settings.database_url)
    gateway = ManualShipmentGateway()
    logged_warnings: list[tuple[str, ...]] = []

    app = FastAPI(title="Toy shop")
    app.state.settings = app_settings
    app.state.database = database

    def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
        if not x_internal_token or x_internal_token != app_settings.internal_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    def catalog():
        loaded = load_catalog(app_settings.catalog_dir)
        signature = loaded.warnings
        if signature != tuple(logged_warnings):
            logged_warnings.clear()
            logged_warnings.extend(signature)
            for warning in signature:
                logger.warning("%s", warning)
        return loaded

    def offices():
        return load_offices(app_settings.offices_path)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/catalog")
    def get_catalog() -> dict:
        return catalog().public(app_settings.cart_max_units)

    @app.get("/api/catalog/{category_id}/{product_id}/cover")
    def product_cover(category_id: str, product_id: str) -> FileResponse:
        if _unsafe_slug(category_id) or _unsafe_slug(product_id):
            raise HTTPException(status_code=404, detail="Not found")
        product = catalog().find(f"{category_id}/{product_id}")
        if product is None or not product.images:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(product.images[0])

    @app.get("/api/offices")
    def find_offices(city: str = Query(min_length=2, max_length=40)) -> dict:
        found = search_offices(offices(), city)
        return {"offices": [office.public() for office in found]}

    @app.post("/api/internal/orders", dependencies=[Depends(require_internal)])
    def create_order(payload: OrderCreate) -> dict:
        try:
            prepared = prepare_order(payload, catalog(), offices(), app_settings.cart_max_units, gateway)
        except ShopError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        order, created = database.create_order(prepared)
        return {"order": order, "created": created}

    @app.get("/api/internal/orders", dependencies=[Depends(require_internal)])
    def list_orders(
        status: str | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=30),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        if status and status not in {"new", "awaiting_transfer", "paid", "shipped", "cancelled"}:
            raise HTTPException(status_code=400, detail="Unknown status")
        return {"orders": database.list_orders(status, limit, offset)}

    @app.get("/api/internal/orders/{order_id}", dependencies=[Depends(require_internal)])
    def get_order(order_id: str) -> dict:
        order = database.get_order(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        return {"order": order}

    @app.post("/api/internal/orders/{order_id}/payment", dependencies=[Depends(require_internal)])
    def mark_paid(order_id: str) -> dict:
        return {"order": _action(lambda: database.mark_paid(order_id))}

    @app.post("/api/internal/orders/{order_id}/shipment", dependencies=[Depends(require_internal)])
    def mark_shipped(order_id: str, payload: ShipUpdate) -> dict:
        return {"order": _action(lambda: database.mark_shipped(order_id, payload.tracking_number))}

    @app.post("/api/internal/orders/{order_id}/cancel", dependencies=[Depends(require_internal)])
    def cancel_order(order_id: str) -> dict:
        return {"order": _action(lambda: database.cancel(order_id))}

    @app.get("/api/internal/sessions/{chat_id}", dependencies=[Depends(require_internal)])
    def get_session(chat_id: str) -> dict:
        return {"state": database.get_session(chat_id)}

    @app.put("/api/internal/sessions/{chat_id}", dependencies=[Depends(require_internal)])
    def save_session(chat_id: str, payload: SessionUpdate) -> dict:
        database.save_session(chat_id, payload.state)
        return {"ok": True}

    @app.delete("/api/internal/sessions/{chat_id}", dependencies=[Depends(require_internal)])
    def clear_session(chat_id: str) -> dict:
        database.clear_session(chat_id)
        return {"ok": True}

    return app


def _action(callback):
    try:
        return callback()
    except OrderActionError as error:
        status = 404 if str(error) == "Заказ не найден" else 409
        raise HTTPException(status_code=status, detail=str(error)) from error


def _unsafe_slug(value: str) -> bool:
    return not value or value in {".", ".."} or "/" in value or "\\" in value or ".." in value
