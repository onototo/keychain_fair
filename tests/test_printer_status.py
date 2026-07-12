import pytest
from fastapi.testclient import TestClient

from keychain_fair.adapters import AdapterResult
from tests.support import ADMIN_HEADERS, FakePrinter, create_test_app, make_settings


@pytest.mark.parametrize(
    ("job_extra", "printer_extra", "expected"),
    [
        pytest.param(
            {"state": "Printing", "progress": {"completion": 42.5, "printTime": 125, "printTimeLeft": 875}},
            {
                "state": {
                    "text": "Printing",
                    "flags": {"operational": True, "printing": True, "closedOrError": False, "error": False},
                },
                "temperature": {"tool0": {"actual": 205, "target": 205}},
            },
            {
                "online": True,
                "online_label": "online",
                "state": "printing",
                "elapsed_seconds": 125,
                "remaining_seconds": 875,
                "progress_percent": 42.5,
                "busy": True,
            },
            id="printing-time",
        ),
        pytest.param(
            {"state": "Operational", "progress": {}},
            {
                "state": {
                    "text": "Operational",
                    "flags": {"operational": True, "ready": True, "printing": False, "closedOrError": False, "error": False},
                },
                "temperature": {"bed": {"actual": 30, "target": 60}},
            },
            {"online": True, "state": "heating", "busy": False},
            id="heating",
        ),
        pytest.param(
            {"state": "Paused", "progress": {}},
            {
                "state": {
                    "text": "Paused",
                    "flags": {"operational": True, "paused": True, "closedOrError": False, "error": False},
                }
            },
            {"online": True, "state": "printing", "busy": True, "paused": True, "filament_change_required": False},
            id="paused-without-progress",
        ),
        pytest.param(
            {"state": "Paused", "progress": {"completion": 8.5, "printTime": 240}},
            {
                "state": {
                    "text": "Paused",
                    "flags": {"operational": True, "paused": True, "closedOrError": False, "error": False},
                }
            },
            {"online": True, "state": "printing", "busy": True, "paused": True, "filament_change_required": True},
            id="paused-after-progress",
        ),
    ],
)
def test_admin_printer_status_reports_octoprint_state(tmp_path, job_extra, printer_extra, expected):
    settings = make_settings(tmp_path)
    printer = FakePrinter(
        settings,
        AdapterResult(True, "fake job", extra=job_extra),
        AdapterResult(True, "fake printer", extra=printer_extra),
    )
    app = create_test_app(settings, printer=printer)

    with TestClient(app) as client:
        response = client.get("/api/admin/printer/status", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    for key, value in expected.items():
        assert payload[key] == value


def test_admin_printer_status_reports_offline_when_octoprint_disabled(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.get("/api/admin/printer/status", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["online"] is False
    assert payload["online_label"] == "offline"
    assert payload["state"] == "offline"
