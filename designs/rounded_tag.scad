module oval_2d(width, height) {
    translate([width / 2, height / 2])
        scale([width / height, 1])
            circle(r = height / 2, $fn = 72);
}

function ep(params, index, fallback) = len(params) > index ? params[index] : fallback;
function block_start(kind) = kind == "name" ? 7 : kind == "car" ? 13 : 19;
function block_param(params, kind, offset, fallback) = ep(params, block_start(kind) + offset, fallback);
function block_param_pos(params, kind, offset, fallback) =
    block_param(params, kind, offset, fallback) > 0 ? block_param(params, kind, offset, fallback) : fallback;

module oval_border(width, height, border_width, height_on_top) {
    linear_extrude(height = height_on_top) {
        difference() {
            oval_2d(width, height);
            translate([border_width, border_width])
                oval_2d(width - 2 * border_width, height - 2 * border_width);
        }
    }
}

module print_line(label, x, y, width, height, font_size, relief, z) {
    fitted = min(font_size, min(width / max(1, len(label)) * 1.35, height * 0.72));
    translate([x, y, z])
        linear_extrude(height = relief)
            text(label, size = fitted, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
}

module keychain(customer_name, line_1, line_2, selected_elements, plate_width, plate_height, plate_thickness, font_size, editor_params = []) {
    body_width = ep(editor_params, 0, plate_width);
    body_height = ep(editor_params, 1, plate_height);
    body_thickness = ep(editor_params, 2, plate_thickness);
    relief_height = ep(editor_params, 3, 0.8);
    hole_x = ep(editor_params, 4, 7.0);
    hole_y = ep(editor_params, 5, body_height / 2);
    hole_radius = ep(editor_params, 6, 2.35);
    line_1_width = block_param_pos(editor_params, "car", 2, body_width - 22);
    line_1_height = block_param_pos(editor_params, "car", 3, body_height * 0.34);
    line_1_font = block_param_pos(editor_params, "car", 4, font_size);
    line_1_relief = block_param_pos(editor_params, "car", 5, relief_height);
    line_2_width = block_param_pos(editor_params, "phone", 2, body_width - 18);
    line_2_height = block_param_pos(editor_params, "phone", 3, body_height * 0.22);
    line_2_font = block_param_pos(editor_params, "phone", 4, font_size * 0.56);
    line_2_relief = block_param_pos(editor_params, "phone", 5, relief_height);
    has_second_line = line_2 != "";

    difference() {
        linear_extrude(height = body_thickness) oval_2d(body_width, body_height);
        translate([hole_x, hole_y, -0.1])
            cylinder(h = body_thickness + 0.25, r = hole_radius, $fn = 32);
    }

    translate([0, 0, body_thickness])
        oval_border(body_width, body_height, 1.25, min(0.34, relief_height));

    translate([hole_x, hole_y, body_thickness])
        difference() {
            cylinder(h = min(0.60, relief_height), r = hole_radius + 1.65, $fn = 32);
            translate([0, 0, -0.05])
                cylinder(h = min(0.70, relief_height + 0.10), r = hole_radius + 0.22, $fn = 32);
        }

    if (has_second_line) {
        print_line(line_1, body_width / 2 + block_param(editor_params, "car", 0, 0), body_height * 0.58 + block_param(editor_params, "car", 1, 0), line_1_width, line_1_height, line_1_font, line_1_relief, body_thickness);
        print_line(line_2, body_width / 2 + block_param(editor_params, "phone", 0, 0), body_height * 0.36 + block_param(editor_params, "phone", 1, 0), line_2_width, line_2_height, line_2_font, line_2_relief, body_thickness);
    } else {
        print_line(line_1, body_width / 2 + block_param(editor_params, "car", 0, 0), body_height / 2 + block_param(editor_params, "car", 1, 0), line_1_width, max(line_1_height, body_height * 0.45), line_1_font, line_1_relief, body_thickness);
    }
}
