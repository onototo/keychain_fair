from fastapi.testclient import TestClient

from tests.support import create_test_app, make_settings, order_payload


def test_rectangular_number_design_is_disabled(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json=order_payload(
                design_id="stacked_plate_classic",
                size_id="standard",
                car_number="1234 AB-7",
                print_line_1="",
                idempotency_key="rect-rejects-ru",
            ),
        )

    assert response.status_code == 400
    assert "Unknown design" in response.json()["detail"]


def test_rectangular_number_order_is_rejected(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json=order_payload(
                design_id="stacked_plate_classic",
                size_id="standard",
                car_number="1234 AB-7",
                print_line_1="ignored",
                idempotency_key="rect-by-number",
            ),
        )

    assert response.status_code == 400
    assert "Unknown design" in response.json()["detail"]


def test_square_number_design_is_disabled(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json=order_payload(
                design_id="square_plate",
                size_id="standard",
                car_number="1234 AB-7",
                print_line_1="ignored",
                idempotency_key="square-by-number",
            ),
        )

    assert response.status_code == 400
    assert "Unknown design" in response.json()["detail"]
