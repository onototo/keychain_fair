function contains(values, needle) = len([for (value = values) if (value == needle) value]) > 0;

function content_count(values) =
    max(1,
        (contains(values, "name") ? 1 : 0) +
        (contains(values, "car") ? 1 : 0) +
        (contains(values, "phone") ? 1 : 0)
    );

function center_kind(values) =
    content_count(values) == 1 ?
        (contains(values, "phone") ? "phone" : (contains(values, "name") ? "name" : "car")) :
    contains(values, "car") ? "car" :
    contains(values, "name") ? "name" :
    "phone";

function bottom_kind(values) =
    content_count(values) == 2 ?
        (center_kind(values) == "car" ? (contains(values, "phone") ? "phone" : "name") :
        (center_kind(values) == "name" ? "phone" : "name")) :
    "phone";

function value_for_kind(kind, customer_name, car_number, phone_number) =
    kind == "name" ? customer_name :
    kind == "phone" ? str("+", phone_number) :
    car_number;

function text_width_factor(kind) =
    kind == "phone" ? 1.05 :
    kind == "name" ? 1.22 :
    1.35;

function text_height_factor(kind) =
    kind == "phone" ? 0.64 :
    kind == "name" ? 0.68 :
    0.72;

function ep(params, index, fallback) = len(params) > index ? params[index] : fallback;
function ep_pos(params, index, fallback) = ep(params, index, fallback) > 0 ? ep(params, index, fallback) : fallback;
function block_start(kind) = kind == "name" ? 7 : kind == "car" ? 13 : 19;
function block_param(params, kind, offset, fallback) = ep(params, block_start(kind) + offset, fallback);
function block_param_pos(params, kind, offset, fallback) =
    block_param(params, kind, offset, fallback) > 0 ? block_param(params, kind, offset, fallback) : fallback;

module rounded_rect_2d(width, height, radius) {
    hull() {
        translate([radius, radius]) circle(r = radius, $fn = 30);
        translate([width - radius, radius]) circle(r = radius, $fn = 30);
        translate([radius, height - radius]) circle(r = radius, $fn = 30);
        translate([width - radius, height - radius]) circle(r = radius, $fn = 30);
    }
}

module loop_2d(plate_width, center_y, side, outer_radius, bridge_width) {
    loop_x = side == "left" ? -outer_radius * 0.92 : plate_width + outer_radius * 0.92;
    bridge_x = side == "left" ? -bridge_width : plate_width;

    union() {
        translate([bridge_x, center_y - outer_radius * 0.45])
            square([bridge_width, outer_radius * 0.9]);
        translate([loop_x, center_y])
            circle(r = outer_radius, $fn = 48);
    }
}

module stepped_shape_2d(plate_width, total_height, center_height, side_height, overlap, side_width_ratio, loop_side, count) {
    side_width = plate_width * side_width_ratio;
    side_x = (plate_width - side_width) / 2;
    radius = min(3.4, center_height * 0.18);
    side_radius = min(3.0, side_height * 0.24);
    center_y = count == 3 ? side_height - overlap : 0;
    bottom_y = 0;
    top_y = center_y + center_height - overlap;
    ear_y = center_y + center_height / 2;
    outer_loop = max(3.7, center_height * 0.19);
    bridge_width = max(3.6, center_height * 0.18);

    union() {
        if (count == 3) {
            translate([side_x, top_y])
                rounded_rect_2d(side_width, side_height, side_radius);
        }
        if (count >= 2) {
            translate([side_x, bottom_y])
                rounded_rect_2d(side_width, side_height, side_radius);
        }

        translate([0, center_y])
            rounded_rect_2d(plate_width, center_height, radius);

        loop_2d(plate_width, ear_y, loop_side, outer_loop, bridge_width);
    }
}

module raised_outline(plate_width, total_height, center_height, side_height, overlap, side_width_ratio, loop_side, count, border_width, height_on_top) {
    linear_extrude(height = height_on_top) {
        difference() {
            stepped_shape_2d(plate_width, total_height, center_height, side_height, overlap, side_width_ratio, loop_side, count);
            offset(delta = -border_width)
                stepped_shape_2d(plate_width, total_height, center_height, side_height, overlap, side_width_ratio, loop_side, count);
        }
    }
}

module side_badge(label, x, y, height, top_z, style) {
    badge_width = max(5.4, height * 0.38);
    badge_height = height * 0.60;

    if (style == "frame" || style == "layered") {
        translate([x, y + (height - badge_height) / 2, top_z])
            linear_extrude(height = 0.24)
                rounded_rect_2d(badge_width, badge_height, min(1.1, badge_width / 4));

        translate([x + badge_width / 2, y + height / 2, top_z + 0.24])
            linear_extrude(height = 0.28)
                offset(r = 0.02)
                    text(label, size = badge_width * 0.42, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
    }
}

module raised_text(kind, x, y, width, height, customer_name, car_number, phone_number, top_z, font_size, style, editor_params = []) {
    label = value_for_kind(kind, customer_name, car_number, phone_number);
    box_width = block_param_pos(editor_params, kind, 2, width);
    box_height = block_param_pos(editor_params, kind, 3, height);
    block_font_size = block_param_pos(editor_params, kind, 4, font_size);
    size_by_width = box_width / max(1, len(label)) * text_width_factor(kind);
    fitted_size = min(block_font_size, min(box_height * text_height_factor(kind), size_by_width));
    thicken = kind == "car" ? 0.08 : 0.045;
    text_height = block_param_pos(editor_params, kind, 5, ep(editor_params, 3, 0.8));

    translate([x + width / 2 + block_param(editor_params, kind, 0, 0), y + height / 2 + block_param(editor_params, kind, 1, 0), top_z])
        linear_extrude(height = text_height)
            offset(r = thicken)
                text(label, size = fitted_size, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
}

module slot_text(slot, kind, plate_width, center_y, center_height, side_height, overlap, side_width_ratio, customer_name, car_number, phone_number, top_z, font_size, style, editor_params = []) {
    side_width = plate_width * side_width_ratio;
    side_x = (plate_width - side_width) / 2;
    slot_y = slot == "top" ? center_y + center_height - overlap :
             slot == "bottom" ? 0 :
             center_y;
    slot_height = slot == "center" ? center_height : side_height;
    slot_width = slot == "center" ? plate_width : side_width;
    slot_x = slot == "center" ? 0 : side_x;
    badge_space = style == "classic" ? 4.0 : max(7.2, slot_height * 0.44);
    right_space = 4.0;
    text_x = slot_x + badge_space;
    text_width = max(10, slot_width - badge_space - right_space);

    if (slot != "center") {
        side_badge(kind == "name" ? "ID" : (kind == "phone" ? "TEL" : "BY"), slot_x + 2.6, slot_y, slot_height, top_z, style);
    } else if (style == "layered") {
        side_badge(kind == "name" ? "ID" : (kind == "phone" ? "TEL" : "BY"), slot_x + 3.0, slot_y, slot_height, top_z, style);
    }

    raised_text(kind, text_x, slot_y, text_width, slot_height, customer_name, car_number, phone_number, top_z, font_size, style, editor_params);
}

module decorative_dots(plate_width, center_y, center_height, top_z, style) {
    if (style == "layered") {
        dot_r = max(0.55, center_height * 0.028);
        for (x = [4.0, plate_width - 4.0]) {
            for (y = [center_y + 3.8, center_y + center_height - 3.8]) {
                translate([x, y, top_z])
                    cylinder(h = 0.30, r = dot_r, $fn = 18);
            }
        }
    }
}

module stacked_plate_keychain(customer_name, car_number, phone_number, selected_elements, plate_width, plate_height, plate_thickness, font_size, style = "classic", editor_params = []) {
    count = content_count(selected_elements);
    top_ratio = 0.56;
    bottom_ratio = 0.56;
    overlap = count == 1 ? 0 : ep(editor_params, 25, 1.4);
    side_width_ratio = style == "classic" ? 0.78 : 0.82;
    loop_side = "left";
    legacy_center_height = count == 1 ? plate_height :
        count == 2 ? (plate_height + overlap) / (1 + bottom_ratio) :
        (plate_height + 2 * overlap) / (1 + top_ratio + bottom_ratio);
    center_height = ep(editor_params, 1, legacy_center_height);
    side_height = center_height * bottom_ratio;
    total_height = count == 1 ? center_height :
        count == 2 ? center_height + side_height - overlap :
        center_height + center_height * top_ratio + side_height - 2 * overlap;
    center_y = count == 3 ? side_height - overlap : 0;
    plate_z = ep(editor_params, 2, plate_thickness);
    relief_height = ep(editor_params, 3, 0.8);
    hole_radius = ep_pos(editor_params, 6, max(1.95, center_height * 0.10));
    loop_outer = max(3.7, center_height * 0.19);
    loop_space = loop_outer * 2.05;
    total_width = ep(editor_params, 0, plate_width);
    body_width = max(20, total_width - loop_space);
    body_x = loop_side == "left" ? loop_space : 0;
    loop_x_local = loop_side == "left" ? -loop_outer * 0.92 : body_width + loop_outer * 0.92;
    loop_x = body_x + loop_x_local + ep(editor_params, 4, 0);
    loop_y = center_y + center_height - loop_outer + ep(editor_params, 5, 0);
    border_width = style == "layered" ? 1.25 : 1.0;

    difference() {
        union() {
            translate([body_x, 0, 0])
                linear_extrude(height = plate_z)
                    stepped_shape_2d(body_width, total_height, center_height, side_height, overlap, side_width_ratio, loop_side, count);

            translate([body_x, 0, plate_z])
                raised_outline(body_width, total_height, center_height, side_height, overlap, side_width_ratio, loop_side, count, border_width, min(0.34, relief_height));

            if (style == "frame" || style == "layered") {
                translate([body_x, 0, plate_z + min(0.34, relief_height)])
                    raised_outline(body_width, total_height, center_height, side_height, overlap, side_width_ratio, loop_side, count, 2.25, min(0.14, relief_height));
            }

            translate([body_x, 0, 0])
                decorative_dots(body_width, center_y, center_height, plate_z + min(0.48, relief_height), style);

            if (count == 1) {
                translate([body_x, 0, 0])
                    slot_text("center", center_kind(selected_elements), body_width, center_y, center_height, side_height, overlap, side_width_ratio, customer_name, car_number, phone_number, plate_z, font_size, style, editor_params);
            } else if (count == 2) {
                translate([body_x, 0, 0]) {
                    slot_text("center", center_kind(selected_elements), body_width, center_y, center_height, side_height, overlap, side_width_ratio, customer_name, car_number, phone_number, plate_z, font_size, style, editor_params);
                    slot_text("bottom", bottom_kind(selected_elements), body_width, center_y, center_height, side_height, overlap, side_width_ratio, customer_name, car_number, phone_number, plate_z, font_size * 0.76, style, editor_params);
                }
            } else {
                translate([body_x, 0, 0]) {
                    slot_text("top", "name", body_width, center_y, center_height, side_height, overlap, side_width_ratio, customer_name, car_number, phone_number, plate_z, font_size * 0.72, style, editor_params);
                    slot_text("center", "car", body_width, center_y, center_height, side_height, overlap, side_width_ratio, customer_name, car_number, phone_number, plate_z, font_size, style, editor_params);
                    slot_text("bottom", "phone", body_width, center_y, center_height, side_height, overlap, side_width_ratio, customer_name, car_number, phone_number, plate_z, font_size * 0.76, style, editor_params);
                }
            }
        }

        translate([loop_x, loop_y, -0.12])
            cylinder(h = plate_z + relief_height + 0.4, r = hole_radius, $fn = 42);
    }
}
