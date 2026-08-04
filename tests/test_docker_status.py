from fastapi.testclient import TestClient

from keychain_fair import main as main_module
from tests.support import ADMIN_HEADERS, create_test_app, make_settings


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_admin_docker_status_reports_daemon_online(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    monkeypatch.setattr(main_module, "_is_running_inside_docker", lambda: False)
    monkeypatch.setattr(main_module.shutil, "which", lambda name: "docker.exe" if name == "docker" else None)

    def fake_run(command, **kwargs):
        assert command == ["docker.exe", "info", "--format", "{{json .ServerVersion}}"]
        return FakeCompletedProcess(stdout='"27.1.1"\n')

    monkeypatch.setattr(main_module.subprocess, "run", fake_run)

    with TestClient(app) as client:
        response = client.get("/api/admin/docker/status", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "online": True,
        "online_label": "online",
        "state": "running",
        "label": "docker server",
        "message": "server 27.1.1",
        "server_version": "27.1.1",
    }


def test_admin_docker_status_reports_offline_without_cli(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    monkeypatch.setattr(main_module, "_is_running_inside_docker", lambda: False)
    monkeypatch.setattr(main_module.shutil, "which", lambda name: None)

    with TestClient(app) as client:
        response = client.get("/api/admin/docker/status", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["online"] is False
    assert payload["online_label"] == "offline"
    assert payload["state"] == "offline"
    assert payload["label"] == "offline"
    assert payload["message"] == "Docker CLI not found"
