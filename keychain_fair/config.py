from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _resolve(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _env_int(key: str, default: int) -> int:
    value = os.environ.get(key)
    if value is None or not value.strip():
        return default
    return int(value)


@dataclass(frozen=True)
class AppSettings:
    host: str
    port: int
    database_path: Path
    catalog_dir: Path
    offices_path: Path
    cart_max_units: int
    internal_token: str

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


def load_settings(config_path: Path | None = None) -> AppSettings:
    _read_dotenv(PROJECT_ROOT / ".env")
    path = config_path or PROJECT_ROOT / "config" / "app.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    server = raw.get("server") or {}
    storage = raw.get("storage") or {}
    cart = raw.get("cart") or {}
    token = os.environ.get("INTERNAL_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("INTERNAL_API_TOKEN is required")
    max_units = _env_int("CART_MAX_UNITS", int(cart.get("max_units") or 99))
    if max_units < 1:
        raise RuntimeError("cart.max_units must be at least 1")
    return AppSettings(
        host=str(server.get("host") or "0.0.0.0"),
        port=int(server.get("port") or 8120),
        database_path=_resolve(PROJECT_ROOT, os.environ.get("DATABASE_PATH") or storage.get("database") or "data/shop.sqlite3"),
        catalog_dir=_resolve(PROJECT_ROOT, os.environ.get("CATALOG_DIR") or storage.get("catalog_dir") or "catalog"),
        offices_path=_resolve(PROJECT_ROOT, os.environ.get("OFFICES_PATH") or storage.get("offices_path") or "config/europost-offices.json"),
        cart_max_units=max_units,
        internal_token=token,
    )
