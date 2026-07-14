import json

from fastapi.testclient import TestClient

from tests.support import ADMIN_HEADERS, create_test_app, make_settings, make_temp_design_settings, order_payload


def assert_base_layer_aligned(value):
    layer_index = (value - 0.1) / 0.2
    assert abs(layer_index - round(layer_index)) < 0.000001


def assert_relief_layer_aligned(value):
    layer_index = value / 0.2
    assert abs(layer_index - round(layer_index)) < 0.000001


def test_model_editor_designs_returns_editable_params(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        response = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    designs = response.json()["designs"]
    assert [design["id"] for design in designs] == ["stacked_plate_classic", "square_plate", "classic_plate", "rounded_tag"]
    for design in designs:
        for size in design["sizes"]:
            params = size["editor_params"]
            assert params["base_width_mm"] > 0
            assert 1.1 <= params["thickness_mm"] <= 5.9
            assert_base_layer_aligned(params["thickness_mm"])
            assert 0.2 <= params["relief_height_mm"] <= 2.0
            assert_relief_layer_aligned(params["relief_height_mm"])
            assert 0.2 <= params["text_blocks"]["car"]["relief_height_mm"] <= 2.0
            assert_relief_layer_aligned(params["text_blocks"]["car"]["relief_height_mm"])


def test_model_editor_preview_creates_authenticated_stl(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = next(item for item in designs if item["id"] == "classic_plate")
        size = next(item for item in design["sizes"] if item["id"] == "standard")
        response = client.post(
            "/api/admin/model-editor/preview",
            headers=ADMIN_HEADERS,
            json={
                "design_id": design["id"],
                "size_id": size["id"],
                "editor_params": size["editor_params"],
                "sample_text": {"customer_name": "Nikita", "car_number": "A123BC77", "phone": "375291234567"},
            },
        )
        assert response.status_code == 200
        payload = response.json()
        stl_response = client.get(payload["stl_url"], headers=ADMIN_HEADERS)

    assert payload["preview_id"].startswith("prv_")
    assert payload["bounds"]["z"] == round(size["editor_params"]["thickness_mm"] + size["editor_params"]["relief_height_mm"], 3)
    assert stl_response.status_code == 200
    assert b"solid preview" in stl_response.content


def test_model_editor_preview_uses_current_editor_base_height(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = next(item for item in designs if item["id"] == "classic_plate")
        size = next(item for item in design["sizes"] if item["id"] == "standard")
        params = dict(size["editor_params"])
        params["thickness_mm"] = 4.9
        response = client.post(
            "/api/admin/model-editor/preview",
            headers=ADMIN_HEADERS,
            json={
                "design_id": design["id"],
                "size_id": size["id"],
                "editor_params": params,
                "sample_text": {"customer_name": "Nikita", "car_number": "A123BC77", "phone": "375291234567"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filament_change_height_mm"] == 4.9
    assert payload["bounds"]["z"] == 5.7


def test_model_editor_preview_snaps_base_height_to_layer_grid(tmp_path):
    settings = make_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = next(item for item in designs if item["id"] == "classic_plate")
        size = next(item for item in design["sizes"] if item["id"] == "standard")
        params = dict(size["editor_params"])
        params["thickness_mm"] = 2.0
        response = client.post(
            "/api/admin/model-editor/preview",
            headers=ADMIN_HEADERS,
            json={
                "design_id": design["id"],
                "size_id": size["id"],
                "editor_params": params,
                "sample_text": {"customer_name": "Nikita", "car_number": "A123BC77", "phone": "375291234567"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filament_change_height_mm"] == 2.1
    assert payload["bounds"]["z"] == 2.9


def test_model_editor_save_preset_updates_size_and_creates_backup(tmp_path):
    settings = make_temp_design_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = designs[0]
        size = design["sizes"][0]
        params = size["editor_params"]
        params["base_width_mm"] = 57.5
        params["thickness_mm"] = 4.9
        response = client.post(
            "/api/admin/model-editor/presets",
            headers=ADMIN_HEADERS,
            json={"design_id": design["id"], "size_id": size["id"], "editor_params": params},
        )

    assert response.status_code == 200
    source = settings.designs_dir / "03_custom_rectangular.json"
    saved = json.loads(source.read_text(encoding="utf-8"))
    saved_size = next(item for item in saved["sizes"] if item["id"] == size["id"])
    assert saved_size["width_mm"] == 57.5
    assert saved_size["thickness_mm"] == 4.9
    assert saved_size["model_scale"] == 1
    assert saved_size["editor_params"]["base_width_mm"] == 57.5
    assert saved_size["editor_params"]["thickness_mm"] == 4.9
    assert response.json()["editor_params"]["thickness_mm"] == 4.9
    backups = list((settings.base_dir / "data" / "design_backups").glob("*_03_custom_rectangular.json"))
    assert len(backups) == 1


def test_model_editor_saved_preset_survives_app_restart(tmp_path):
    settings = make_temp_design_settings(tmp_path)

    app = create_test_app(settings)
    with TestClient(app) as client:
        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = next(item for item in designs if item["id"] == "classic_plate")
        size = next(item for item in design["sizes"] if item["id"] == "standard")
        params = size["editor_params"]
        params["base_width_mm"] = 68.4
        params["base_height_mm"] = 31.2
        params["hole"]["x_mm"] = 8.2
        params["text_blocks"]["car"]["font_size_mm"] = 8.4
        response = client.post(
            "/api/admin/model-editor/presets",
            headers=ADMIN_HEADERS,
            json={"design_id": design["id"], "size_id": size["id"], "editor_params": params},
        )
        assert response.status_code == 200

    restarted_app = create_test_app(settings)
    with TestClient(restarted_app) as client:
        refreshed = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]

    refreshed_design = next(item for item in refreshed if item["id"] == "classic_plate")
    refreshed_size = next(item for item in refreshed_design["sizes"] if item["id"] == "standard")
    refreshed_params = refreshed_size["editor_params"]
    assert refreshed_size["width_mm"] == 68.4
    assert refreshed_size["height_mm"] == 31.2
    assert refreshed_params["base_width_mm"] == 68.4
    assert refreshed_params["base_height_mm"] == 31.2
    assert refreshed_params["hole"]["x_mm"] == 8.2
    assert refreshed_params["text_blocks"]["car"]["font_size_mm"] == 8.4


def test_model_editor_save_preset_syncs_shared_heights_to_all_design_presets(tmp_path):
    settings = make_temp_design_settings(tmp_path, source_names=["classic_plate", "rounded_tag"])
    app = create_test_app(settings)

    with TestClient(app) as client:
        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = next(item for item in designs if item["id"] == "classic_plate")
        size = next(item for item in design["sizes"] if item["id"] == "standard")
        params = size["editor_params"]
        params["thickness_mm"] = 4.7
        params["relief_height_mm"] = 1.2
        response = client.post(
            "/api/admin/model-editor/presets",
            headers=ADMIN_HEADERS,
            json={"design_id": design["id"], "size_id": size["id"], "editor_params": params},
        )
        refreshed = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]

    assert response.status_code == 200
    assert len(response.json()["paths"]["synced_sources"]) == 2
    assert len(response.json()["paths"]["backups"]) == 2
    for source_name in ["03_custom_rectangular.json", "04_custom_oval.json"]:
        saved = json.loads((settings.designs_dir / source_name).read_text(encoding="utf-8"))
        for size in saved["sizes"]:
            assert size["thickness_mm"] == 4.7
            assert size["relief_height_mm"] == 1.2
            assert size["editor_params"]["thickness_mm"] == 4.7
            assert size["editor_params"]["relief_height_mm"] == 1.2
    for design in refreshed:
        for size in design["sizes"]:
            assert size["editor_params"]["thickness_mm"] == 4.7
            assert size["editor_params"]["relief_height_mm"] == 1.2


def test_order_model_params_snapshot_survives_later_preset_change(tmp_path):
    settings = make_temp_design_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        order_response = client.post(
            "/api/orders",
            json=order_payload(idempotency_key="snapshot-editor-order"),
        )
        assert order_response.status_code == 201
        order_id = order_response.json()["order"]["id"]
        before = app.state.database.get_order(order_id)["model_params"]

        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = designs[0]
        size = next(item for item in design["sizes"] if item["id"] == "standard")
        params = size["editor_params"]
        params["base_width_mm"] = before["base_width_mm"] + 10
        save_response = client.post(
            "/api/admin/model-editor/presets",
            headers=ADMIN_HEADERS,
            json={"design_id": design["id"], "size_id": size["id"], "editor_params": params},
        )
        assert save_response.status_code == 200
        after = app.state.database.get_order(order_id)["model_params"]

    assert before["base_width_mm"] == 64
    assert after == before


def test_order_model_params_use_saved_editor_base_height(tmp_path):
    settings = make_temp_design_settings(tmp_path)
    app = create_test_app(settings)

    with TestClient(app) as client:
        designs = client.get("/api/admin/model-editor/designs", headers=ADMIN_HEADERS).json()["designs"]
        design = designs[0]
        size = next(item for item in design["sizes"] if item["id"] == "standard")
        params = size["editor_params"]
        params["thickness_mm"] = 4.4
        save_response = client.post(
            "/api/admin/model-editor/presets",
            headers=ADMIN_HEADERS,
            json={"design_id": design["id"], "size_id": size["id"], "editor_params": params},
        )
        assert save_response.status_code == 200

        order_response = client.post(
            "/api/orders",
            json=order_payload(idempotency_key="saved-base-height-order"),
        )
        assert order_response.status_code == 201
        order_id = order_response.json()["order"]["id"]
        model_params = app.state.database.get_order(order_id)["model_params"]

    assert model_params["thickness_mm"] == 4.5
