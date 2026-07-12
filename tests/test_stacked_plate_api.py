from fastapi.testclient import TestClient

from tests.support import create_test_app, make_settings, order_payload


def create_stacked_order(client, *, elements, idempotency_key, car_number="A123BC77", phone="375291234567", size_id="standard"):
    return client.post(
        "/api/orders",
        json=order_payload(
            customer_name="Nikita",
            car_number=car_number,
            phone=phone,
            design_id="stacked_plate_classic",
            size_id=size_id,
            elements=elements,
            idempotency_key=idempotency_key,
        ),
    )


def test_stacked_plate_allows_name_only_without_car_number(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = create_stacked_order(
            client,
            elements=["name"],
            car_number="",
            idempotency_key="stacked-name-only",
        )

    assert response.status_code == 201
    order = response.json()["order"]
    assert order["car_number"] == ""
    assert order["height_mm"] == 15
    assert [item["id"] for item in order["selected_elements"]] == ["name", "loop_left"]


def test_stacked_plate_requires_car_number_when_car_block_is_selected(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = create_stacked_order(
            client,
            elements=["car"],
            car_number="",
            idempotency_key="stacked-car-without-number",
        )

    assert response.status_code == 400
    assert "номер авто" in response.json()["detail"]


def test_stacked_plate_accepts_right_loop_side(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = create_stacked_order(
            client,
            elements=["name", "car", "phone", "loop_right"],
            idempotency_key="stacked-right-loop",
        )

    assert response.status_code == 201
    order = response.json()["order"]
    assert abs(order["height_mm"] - 29.7) < 0.001
    assert [item["id"] for item in order["selected_elements"]] == ["name", "car", "phone", "loop_right"]


def test_stacked_plate_size_presets_scale_dimensions(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        orders = {
            size_id: create_stacked_order(
                client,
                elements=["car", "loop_left"],
                phone=f"37529123456{index}",
                size_id=size_id,
                idempotency_key=f"stacked-{size_id}-scale",
            ).json()["order"]
            for index, size_id in enumerate(["compact", "standard", "large"])
        }

    expected_scales = {"compact": 0.6, "standard": 0.75, "large": 0.9}
    shared_thickness = orders["standard"]["thickness_mm"]
    for size_id, scale in expected_scales.items():
        assert abs(orders[size_id]["width_mm"] - 88 * scale) < 0.001
        assert abs(orders[size_id]["height_mm"] - 20 * scale) < 0.001
        assert 1.1 <= orders[size_id]["thickness_mm"] <= 5.9
        assert abs(orders[size_id]["thickness_mm"] - shared_thickness) < 0.001
