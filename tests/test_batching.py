from keychain_fair.batching import layout_orders


def test_layout_respects_max_items_and_bed_size():
    orders = [
        {"id": f"ord_{index}", "width_mm": 50, "height_mm": 25}
        for index in range(10)
    ]

    layout = layout_orders(orders, bed_size_mm=(130, 80), spacing_mm=5, max_items=4)

    assert len(layout) == 4
    assert layout[0]["x_mm"] == 5
    assert layout[0]["y_mm"] == 5
    assert all(item["x_mm"] + item["order"]["width_mm"] <= 130 for item in layout)


def test_layout_stops_at_earliest_order_that_does_not_fit_current_plate():
    orders = [
        {"id": "oldest", "width_mm": 40, "height_mm": 40},
        {"id": "next-oldest", "width_mm": 70, "height_mm": 40},
        {"id": "newer-small", "width_mm": 20, "height_mm": 20},
    ]

    layout = layout_orders(orders, bed_size_mm=(100, 50), spacing_mm=5, max_items=8)

    assert [item["order"]["id"] for item in layout] == ["oldest"]


def test_layout_rejects_model_that_is_wider_than_printable_area():
    orders = [{"id": "too-wide", "width_mm": 95, "height_mm": 20}]

    layout = layout_orders(orders, bed_size_mm=(100, 50), spacing_mm=5, max_items=8)

    assert layout == []


def test_layout_uses_print_footprint_when_available():
    orders = [
        {"id": "actual-size", "width_mm": 40, "height_mm": 20, "print_width_mm": 70, "print_height_mm": 20},
        {"id": "second", "width_mm": 20, "height_mm": 20},
    ]

    layout = layout_orders(orders, bed_size_mm=(100, 60), spacing_mm=5, max_items=8)

    assert len(layout) == 2
    assert layout[1]["x_mm"] == 5
    assert layout[1]["y_mm"] == 30
