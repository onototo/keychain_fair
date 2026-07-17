from keychain_fair.config import PROJECT_ROOT
from keychain_fair.designs import DesignCatalog


def test_design_catalog_loads_templates():
    catalog = DesignCatalog(PROJECT_ROOT / "designs")
    designs = catalog.load_designs()

    assert [item["id"] for item in designs] == ["stacked_plate_classic", "square_plate", "classic_plate", "rounded_tag"]
    assert [item["name"] for item in designs] == [
        "Прямоугольный номер",
        "Квадратный номер",
        "Кастомный прямоугольный",
        "Кастомный овальный",
    ]
    assert all(item["template_available"] for item in designs)
    assert all(item["preview_image"].startswith("/static/design-previews/") for item in designs)
    assert all((PROJECT_ROOT / item["preview_image"].lstrip("/")).exists() for item in designs)
    assert [item["preview_image"].split("/")[-1] for item in designs] == [
        "01_rectangular_number.png",
        "02_square_number.png",
        "03_custom_rectangular.png",
        "04_custom_oval.png",
    ]


def test_selection_rejects_removed_elements():
    catalog = DesignCatalog(PROJECT_ROOT / "designs")
    try:
        catalog.validate_selection("classic_plate", "standard", ["heart"])
    except Exception as exc:
        assert "Unsupported elements" in str(exc)
    else:
        raise AssertionError("heart should not be accepted")


def test_models_have_no_default_elements():
    catalog = DesignCatalog(PROJECT_ROOT / "designs")
    designs = catalog.load_designs()

    assert all(item["default_elements"] == [] for item in designs)
    assert all(item["elements"] == [] for item in designs)


def test_custom_designs_expose_text_limits():
    catalog = DesignCatalog(PROJECT_ROOT / "designs")
    designs = {item["id"]: item for item in catalog.load_public_designs()}

    rectangular = [size["custom_text_limits"]["max_total_chars"] for size in designs["classic_plate"]["sizes"]]
    oval = [size["custom_text_limits"]["max_total_chars"] for size in designs["rounded_tag"]["sizes"]]

    assert rectangular == [22, 24, 30]
    assert oval == [16, 24, 26]
