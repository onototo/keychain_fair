module rounded_rect_2d(width, height, radius) {
    hull() {
        translate([radius, radius]) circle(r = radius, $fn = 32);
        translate([width - radius, radius]) circle(r = radius, $fn = 32);
        translate([radius, height - radius]) circle(r = radius, $fn = 32);
        translate([width - radius, height - radius]) circle(r = radius, $fn = 32);
    }
}

module raised_rounded_rect(width, height, radius, thickness) {
    linear_extrude(height = thickness) rounded_rect_2d(width, height, radius);
}

module raised_border(width, height, radius, border_width, height_on_top) {
    linear_extrude(height = height_on_top) {
        difference() {
            rounded_rect_2d(width, height, radius);
            translate([border_width, border_width])
                rounded_rect_2d(width - 2 * border_width, height - 2 * border_width, max(0.6, radius - border_width));
        }
    }
}

module heart_2d(size) {
    scale(size / 10) {
        union() {
            translate([-2.2, 1.5]) circle(r = 2.35, $fn = 32);
            translate([2.2, 1.5]) circle(r = 2.35, $fn = 32);
            polygon(points = [[-4.8, 1.1], [4.8, 1.1], [0, -5.2]]);
        }
    }
}

module star_2d(size) {
    outer = size * 0.5;
    inner = size * 0.22;
    polygon(points = [
        for (i = [0 : 9])
            let(angle = 90 + i * 36, radius = (i % 2 == 0) ? outer : inner)
            [cos(angle) * radius, sin(angle) * radius]
    ]);
}

module smile_icon(size, height) {
    face_height = height * 0.62;
    detail_radius = max(0.35, size * 0.07);

    cylinder(h = face_height, r = size * 0.5, $fn = 48);

    translate([-size * 0.18, size * 0.13, 0])
        cylinder(h = height, r = detail_radius, $fn = 18);
    translate([size * 0.18, size * 0.13, 0])
        cylinder(h = height, r = detail_radius, $fn = 18);

    for (i = [0 : 4]) {
        a1 = 210 + i * 24;
        a2 = 210 + (i + 1) * 24;
        hull() {
            translate([cos(a1) * size * 0.28, sin(a1) * size * 0.20 - size * 0.03, 0])
                cylinder(h = height, r = detail_radius * 0.82, $fn = 14);
            translate([cos(a2) * size * 0.28, sin(a2) * size * 0.20 - size * 0.03, 0])
                cylinder(h = height, r = detail_radius * 0.82, $fn = 14);
        }
    }
}

module raised_marker(shape, size, height) {
    if (shape == "heart") {
        linear_extrude(height = height) heart_2d(size);
    } else if (shape == "smile") {
        smile_icon(size, height);
    } else if (shape == "star") {
        linear_extrude(height = height) star_2d(size);
    }
}

function ep(params, index, fallback) = len(params) > index ? params[index] : fallback;
function ep_pos(params, index, fallback) = ep(params, index, fallback) > 0 ? ep(params, index, fallback) : fallback;
function block_start(kind) = kind == "name" ? 7 : kind == "car" ? 13 : 19;
function block_param(params, kind, offset, fallback) = ep(params, block_start(kind) + offset, fallback);
function block_param_pos(params, kind, offset, fallback) =
    block_param(params, kind, offset, fallback) > 0 ? block_param(params, kind, offset, fallback) : fallback;

module marker_row(selected_elements, plate_width, plate_height, font_size, top_height) {
    count = len(selected_elements);
    feature_size = max(5.8, font_size * 1.05);
    spacing = feature_size + 1.6;

    if (count > 0) {
        for (i = [0 : count - 1]) {
            translate([plate_width - 7.0 - i * spacing, plate_height - 6.8, top_height])
                raised_marker(selected_elements[i], feature_size, 0.8);
        }
    }
}

module keychain(customer_name, car_number, phone_number, selected_elements, plate_width, plate_height, plate_thickness, font_size, editor_params = []) {
    body_width = ep(editor_params, 0, plate_width);
    body_height = ep(editor_params, 1, plate_height);
    body_thickness = ep(editor_params, 2, plate_thickness);
    relief_height = ep(editor_params, 3, 0.8);
    radius = min(4.5, body_height * 0.18);
    hole_x = ep(editor_params, 4, 6.5);
    hole_y = ep(editor_params, 5, body_height / 2);
    hole_radius = ep(editor_params, 6, 2.35);
    car_box_width = block_param_pos(editor_params, "car", 2, body_width - 22);
    car_box_height = block_param_pos(editor_params, "car", 3, body_height * 0.34);
    car_font = block_param_pos(editor_params, "car", 4, font_size);
    car_relief = block_param_pos(editor_params, "car", 5, relief_height);
    phone_box_width = block_param_pos(editor_params, "phone", 2, body_width - 18);
    phone_box_height = block_param_pos(editor_params, "phone", 3, body_height * 0.22);
    phone_font = block_param_pos(editor_params, "phone", 4, font_size * 0.48);
    phone_relief = block_param_pos(editor_params, "phone", 5, relief_height);
    car_size = min(car_font, min(car_box_width / max(1, len(car_number)) * 1.55, car_box_height * 0.95));
    phone_size = min(phone_font, min(phone_box_width / max(1, len(phone_number)) * 1.12, phone_box_height * 0.95));

    difference() {
        raised_rounded_rect(body_width, body_height, radius, body_thickness);
        translate([hole_x, hole_y, -0.1])
            cylinder(h = body_thickness + 0.25, r = hole_radius, $fn = 36);
    }

    translate([0, 0, body_thickness])
        raised_border(body_width, body_height, radius, 1.35, min(0.34, relief_height));

    translate([hole_x, hole_y, body_thickness])
        difference() {
            cylinder(h = min(0.60, relief_height), r = hole_radius + 1.65, $fn = 40);
            translate([0, 0, -0.05])
                cylinder(h = min(0.70, relief_height + 0.10), r = hole_radius + 0.22, $fn = 36);
        }

    translate([body_width / 2 + block_param(editor_params, "car", 0, 0), body_height * 0.52 + block_param(editor_params, "car", 1, 0), body_thickness])
        linear_extrude(height = car_relief)
            text(car_number, size = car_size, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");

    translate([body_width / 2 + block_param(editor_params, "phone", 0, 0), body_height * 0.23 + block_param(editor_params, "phone", 1, 0), body_thickness])
        linear_extrude(height = phone_relief)
            text(phone_number, size = phone_size, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");

    marker_row(selected_elements, body_width, body_height, font_size, body_thickness);
}
