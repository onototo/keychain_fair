from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Any

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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _resolve(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    value = os.environ.get(key)
    if value is None or not value.strip():
        return default
    return int(value)


@dataclass(frozen=True)
class QueueSettings:
    max_items_per_plate: int
    max_wait_minutes: int
    bed_size_mm: tuple[float, float]
    item_spacing_mm: float


@dataclass(frozen=True)
class ToolSettings:
    openscad_path: str
    openscad_timeout_seconds: int
    cura_engine_path: str
    cura_timeout_seconds: int


@dataclass(frozen=True)
class SlicerSettings:
    enabled: bool
    profile_path: Path


@dataclass(frozen=True)
class ModelSettings:
    base_height_mm: float


@dataclass(frozen=True)
class OctoPrintSettings:
    enabled: bool
    base_url: str
    api_key: str


@dataclass(frozen=True)
class PrinterControlSettings:
    bed_preheat_c: int
    hotend_preheat_c: int = 200


@dataclass(frozen=True)
class AppSettings:
    base_dir: Path
    host: str
    port: int
    public_url: str | None
    wifi_ssid: str
    wifi_password: str
    hotspot_gateway: str
    admin_pin: str
    database_path: Path
    database_url: str | None
    internal_api_token: str
    generated_dir: Path
    designs_dir: Path
    queue: QueueSettings
    tools: ToolSettings
    slicer: SlicerSettings
    model: ModelSettings
    octoprint: OctoPrintSettings
    printer_control: PrinterControlSettings
    worker_poll_seconds: int

    def ensure_directories(self) -> None:
        if not self.database_url or self.database_url.startswith("sqlite:"):
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)


def load_settings(base_dir: Path | None = None) -> AppSettings:
    base = base_dir or PROJECT_ROOT
    _read_dotenv(base / ".env")

    config_path = base / "config" / "app.yaml"
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    server = data.get("server", {})
    storage = data.get("storage", {})
    queue = data.get("queue", {})
    worker = data.get("worker", {})
    tools = data.get("external_tools", {})
    slicer = data.get("slicer", {})
    model = data.get("model", {})
    octoprint = data.get("octoprint", {})
    printer_control = data.get("printer_control", {})
    wifi = data.get("wifi", {})

    api_key_env = octoprint.get("api_key_env", "OCTOPRINT_API_KEY")
    bed_size = queue.get("bed_size_mm", [220, 220])

    return AppSettings(
        base_dir=base,
        host=os.environ.get("HOST") or server.get("host", "0.0.0.0"),
        port=_env_int("PORT", int(server.get("port", 8080))),
        public_url=(os.environ.get("PUBLIC_URL") or server.get("public_url") or None),
        wifi_ssid=str(os.environ.get("KEYCHAIN_WIFI_SSID") or wifi.get("ssid", "KeychainFair")),
        wifi_password=str(os.environ.get("KEYCHAIN_WIFI_PASSWORD") or wifi.get("password", "fair2026")),
        hotspot_gateway=str(os.environ.get("KEYCHAIN_HOTSPOT_GATEWAY") or wifi.get("hotspot_gateway", "192.168.137.1")),
        admin_pin=os.environ.get("ADMIN_PIN", "1234"),
        internal_api_token=os.environ.get("INTERNAL_API_TOKEN", ""),
        database_path=_resolve(base, storage.get("database", "data/orders.sqlite3")),
        database_url=os.environ.get("DATABASE_URL") or storage.get("database_url") or None,
        generated_dir=_resolve(base, storage.get("generated_dir", "generated")),
        designs_dir=_resolve(base, storage.get("designs_dir", "designs")),
        queue=QueueSettings(
            max_items_per_plate=int(queue.get("max_items_per_plate", 8)),
            max_wait_minutes=int(queue.get("max_wait_minutes", 10)),
            bed_size_mm=(float(bed_size[0]), float(bed_size[1])),
            item_spacing_mm=float(queue.get("item_spacing_mm", 8)),
        ),
        tools=ToolSettings(
            openscad_path=str(os.environ.get("OPENSCAD_PATH") or tools.get("openscad_path", "openscad")),
            openscad_timeout_seconds=_env_int("OPENSCAD_TIMEOUT_SECONDS", int(tools.get("openscad_timeout_seconds", 60))),
            cura_engine_path=str(os.environ.get("CURA_ENGINE_PATH") or tools.get("cura_engine_path", "CuraEngine")),
            cura_timeout_seconds=_env_int("CURA_TIMEOUT_SECONDS", int(tools.get("cura_timeout_seconds", 120))),
        ),
        slicer=SlicerSettings(
            enabled=_env_bool("SLICER_ENABLED", bool(slicer.get("enabled", False))),
            profile_path=_resolve(base, slicer.get("profile_path", "config/slicer.example.yaml")),
        ),
        model=ModelSettings(
            base_height_mm=float(model.get("base_height_mm", 3.2)),
        ),
        octoprint=OctoPrintSettings(
            enabled=_env_bool("OCTOPRINT_ENABLED", bool(octoprint.get("enabled", False))),
            base_url=str(os.environ.get("OCTOPRINT_BASE_URL") or octoprint.get("base_url", "http://127.0.0.1:5000")).rstrip("/"),
            api_key=os.environ.get(api_key_env, ""),
        ),
        printer_control=PrinterControlSettings(
            bed_preheat_c=int(printer_control.get("bed_preheat_c", 60)),
            hotend_preheat_c=int(printer_control.get("hotend_preheat_c", 200)),
        ),
        worker_poll_seconds=int(worker.get("poll_seconds", 3)),
    )
