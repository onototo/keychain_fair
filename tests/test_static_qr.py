import json

from scripts.generate_static_qr import generate_qr_assets


def test_generate_static_qr_assets_from_env_file(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "PUBLIC_URL=http://192.168.137.1:8080",
                "KEYCHAIN_WIFI_SSID=KeychainFair",
                "KEYCHAIN_WIFI_PASSWORD=fair2026",
                "KEYCHAIN_HOTSPOT_GATEWAY=192.168.137.1",
            ]
        ),
        encoding="utf-8",
    )

    manifest = generate_qr_assets(tmp_path)
    qr_dir = tmp_path / "static" / "qr"
    manifest_file = json.loads((qr_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["wifi"]["payload"] == "WIFI:T:WPA;S:KeychainFair;P:fair2026;;"
    assert manifest["order"]["payload"] == "http://192.168.137.1:8080/"
    assert manifest["admin"]["payload"] == "http://192.168.137.1:8080/admin"
    assert manifest_file == manifest
    for name in ["customer_wifi.png", "customer_order.png", "admin.png"]:
        assert (qr_dir / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_generate_static_qr_assets_prefers_process_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "PUBLIC_URL=http://192.168.137.1:8080",
                "KEYCHAIN_WIFI_SSID=KeychainFair",
                "KEYCHAIN_WIFI_PASSWORD=fair2026",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PUBLIC_URL", "http://192.168.0.50:8080")

    manifest = generate_qr_assets(tmp_path)

    assert manifest["order"]["payload"] == "http://192.168.0.50:8080/"
    assert manifest["admin"]["payload"] == "http://192.168.0.50:8080/admin"
