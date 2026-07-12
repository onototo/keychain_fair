from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
from typing import Any

import qrcode


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_value(dotenv: dict[str, str], key: str, default: str) -> str:
    return os.environ.get(key) or dotenv.get(key) or default


def escape_wifi_qr(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for char in [";", ",", ":", '"']:
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def wifi_qr_payload(ssid: str, password: str) -> str:
    return f"WIFI:T:WPA;S:{escape_wifi_qr(ssid)};P:{escape_wifi_qr(password)};;"


def png_bytes(text: str) -> bytes:
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def build_manifest(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    dotenv = read_dotenv(root / ".env")
    gateway = env_value(dotenv, "KEYCHAIN_HOTSPOT_GATEWAY", "192.168.137.1")
    ssid = env_value(dotenv, "KEYCHAIN_WIFI_SSID", "KeychainFair")
    password = env_value(dotenv, "KEYCHAIN_WIFI_PASSWORD", "fair2026")
    public_url = env_value(dotenv, "PUBLIC_URL", f"http://{gateway}:8080").rstrip("/")
    telegram_bot_username = env_value(dotenv, "TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    order_url = f"{public_url}/"
    admin_url = f"{public_url}/admin"

    manifest = {
        "version": 1,
        "wifi": {
            "file": "customer_wifi.png",
            "ssid": ssid,
            "payload": wifi_qr_payload(ssid, password),
        },
        "order": {
            "file": "customer_order.png",
            "url": order_url,
            "payload": order_url,
        },
        "admin": {
            "file": "admin.png",
            "url": admin_url,
            "payload": admin_url,
        },
    }
    if telegram_bot_username:
        telegram_url = f"https://t.me/{telegram_bot_username}?start=order"
        manifest["telegram"] = {
            "file": "customer_telegram.png",
            "url": telegram_url,
            "payload": telegram_url,
        }
    return manifest


def generate_qr_assets(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    manifest = build_manifest(root)
    qr_dir = root / "static" / "qr"
    qr_dir.mkdir(parents=True, exist_ok=True)

    for key in ["wifi", "order", "admin", "telegram"]:
        if key not in manifest:
            continue
        item = manifest[key]
        write_if_changed(qr_dir / item["file"], png_bytes(item["payload"]))

    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    write_if_changed(qr_dir / "manifest.json", manifest_bytes)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate stable QR assets for Keychain Fair.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    manifest = generate_qr_assets(args.root.resolve())
    print(f"QR assets ready in {args.root.resolve() / 'static' / 'qr'}")
    print(f"Wi-Fi SSID: {manifest['wifi']['ssid']}")
    print(f"Order URL: {manifest['order']['url']}")
    print(f"Admin URL: {manifest['admin']['url']}")
    if "telegram" in manifest:
        print(f"Telegram URL: {manifest['telegram']['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
