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

