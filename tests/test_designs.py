from keychain_fair.config import PROJECT_ROOT
from keychain_fair.designs import DesignCatalog


def test_design_catalog_loads_templates():
    catalog = DesignCatalog(PROJECT_ROOT / "designs")
    designs = catalog.load_designs()

    assert {item["id"] for item in designs} >= {"classic_plate", "rounded_tag"}
    assert all(item["template_available"] for item in designs)


def test_selection_filters_elements():
    catalog = DesignCatalog(PROJECT_ROOT / "designs")
    selection = catalog.validate_selection("classic_plate", "standard", ["heart", "heart", "star"])

    assert selection.design["id"] == "classic_plate"
    assert selection.size["id"] == "standard"
    assert [item["id"] for item in selection.elements] == ["heart", "heart", "star"]


def test_stacked_plate_uses_default_print_block():
    catalog = DesignCatalog(PROJECT_ROOT / "designs")
    selection = catalog.validate_selection("stacked_plate_classic", "standard", [])

    assert selection.design["layout"] == "stacked_plate"
    assert selection.design["default_size_id"] == "standard"
    assert [item["id"] for item in selection.elements] == ["car", "loop_left"]
