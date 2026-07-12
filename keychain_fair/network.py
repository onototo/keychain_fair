from __future__ import annotations

from io import BytesIO
import base64
import socket

import qrcode


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = {"127.0.0.1"}

    try:
        host_name = socket.gethostname()
        for address in socket.gethostbyname_ex(host_name)[2]:
            if "." in address:
                addresses.add(address)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(sock.getsockname()[0])
    except OSError:
        pass

    return sorted(addresses, key=lambda item: (item.startswith("127."), item))


def qr_data_uri(text: str) -> str:
    image = qrcode.make(text)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def wifi_qr_payload(ssid: str, password: str) -> str:
    escaped_ssid = _escape_wifi_qr(ssid)
    escaped_password = _escape_wifi_qr(password)
    return f"WIFI:T:WPA;S:{escaped_ssid};P:{escaped_password};;"


def _escape_wifi_qr(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for char in [";", ",", ":", '"']:
        escaped = escaped.replace(char, f"\\{char}")
    return escaped
