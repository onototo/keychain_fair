from fastapi.testclient import TestClient
import json

from tests.support import create_test_app, make_settings, order_payload


def test_rectangular_number_requires_by_car_number(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json=order_payload(
                design_id="stacked_plate_classic",
                size_id="standard",
                car_number="A123BC77",
                print_line_1="",
                idempotency_key="rect-rejects-ru",
            ),
        )

    assert response.status_code == 422
    assert "1234 AB-7" in json.dumps(response.json())


def test_rectangular_number_prints_by_number_on_one_line(tmp_path):
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
        order = response.json()["order"]

    assert response.status_code == 201
    assert order["car_number"] == "1234 AB-7"
    assert order["print_line_1"] == "1234 AB-7"
    assert order["print_line_2"] == ""
    assert order["selected_elements"] == []


def test_square_number_splits_by_number_into_two_lines(tmp_path):
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
        order = response.json()["order"]

    assert response.status_code == 201
    assert order["car_number"] == "1234 AB-7"
    assert order["print_line_1"] == "1234"
    assert order["print_line_2"] == "AB-7"
    assert order["width_mm"] == 36
    assert order["height_mm"] == 36
