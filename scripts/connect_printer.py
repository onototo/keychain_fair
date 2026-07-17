from __future__ import annotations

from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from keychain_fair.adapters import OctoPrintController
from keychain_fair.config import load_settings


def _current_connection_summary(controller: OctoPrintController) -> tuple[bool, str, str | None]:
    probe = controller.get_connection()
    if not probe.success or not isinstance(probe.extra, dict):
        return False, probe.message, None

    current = probe.extra.get("current")
    if not isinstance(current, dict):
        return False, "OctoPrint connection status has no current state", None

    state = str(current.get("state") or "")
    port = str(current.get("port") or "") or None
    if controller._is_connected_state(state):
        return True, state, port
    return False, state or "unknown", port


def _verify_connection_stays_up(controller: OctoPrintController, seconds: int = 8) -> bool:
    deadline = time.monotonic() + seconds
    last_state = ""
    last_port = None
    while time.monotonic() < deadline:
        stable, state, port = _current_connection_summary(controller)
        last_state = state
        last_port = port
        if not stable:
            print(f"OctoPrint connection dropped during verification: state={state} port={port or 'unknown'}")
            return False
        time.sleep(1)

    print(f"OctoPrint connection stayed up for {seconds}s: state={last_state} port={last_port or 'unknown'}")
    return True


def main() -> int:
    settings = load_settings()
    print(f"OctoPrint URL: {settings.octoprint.base_url}")
    print(f"Configured printer port: {settings.octoprint.printer_port}")
    controller = OctoPrintController(settings)
    result = controller.connect()

    payload = (result.extra or {}).get("payload") if isinstance(result.extra, dict) else None
    if payload:
        print(
            "OctoPrint connect payload: "
            f"port={payload.get('port')} "
            f"baudrate={payload.get('baudrate')} "
            f"profile={payload.get('printerProfile')}"
        )
    print(result.message)
    if not result.success:
        return 1
    return 0 if _verify_connection_stays_up(controller) else 1


if __name__ == "__main__":
    sys.exit(main())
