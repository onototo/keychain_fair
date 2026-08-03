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
TEXT_WIDTH_FACTOR = 0.82;
PREVIEW_BASE_COLOR = "#2f7f77";
PREVIEW_RELIEF_COLOR = "#f0b429";

module oval_border(width, height, border_width, height_on_top) {
    linear_extrude(height = height_on_top) {
        difference() {
            oval_2d(width, height);
            translate([border_width, border_width])
                oval_2d(width - 2 * border_width, height - 2 * border_width);
        }
    }
}

module print_line(label, x, y, font_size, relief, z) {
    translate([x, y, z])
        linear_extrude(height = relief)
            text(label, size = max(0.1, font_size), halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
}

module print_text_block(line_1, line_2, x, y, width, height, font_size, relief, z, line_spacing, exact_fit) {
    max_line_len = line_2 != "" ? max(len(line_1), len(line_2)) : len(line_1);
    safe_spacing = min(1.8, max(0.7, line_spacing));
    height_limit = line_2 != "" ? height / (1 + safe_spacing) : height * 0.72;
    width_limit = exact_fit > 0 ? font_size : width / max(1, max_line_len) * TEXT_WIDTH_FACTOR;
    fitted = min(font_size, min(width_limit, height_limit));
    if (line_2 != "") {
        gap = fitted * safe_spacing;
        print_line(line_1, x, y + gap / 2, fitted, relief, z);
        print_line(line_2, x, y - gap / 2, fitted, relief, z);
    } else {
        print_line(line_1, x, y, fitted, relief, z);
    }
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
    line_spacing = ep(editor_params, 26, 1.08);
    text_exact_fit = ep(editor_params, 27, 0);

    color(PREVIEW_BASE_COLOR)
        difference() {
            linear_extrude(height = body_thickness) oval_2d(body_width, body_height);
            translate([hole_x, hole_y, -0.1])
                cylinder(h = body_thickness + 0.25, r = hole_radius, $fn = 32);
        }

    color(PREVIEW_RELIEF_COLOR)
        translate([0, 0, body_thickness])
            oval_border(body_width, body_height, 1.25, min(0.34, relief_height));

    color(PREVIEW_RELIEF_COLOR)
        translate([hole_x, hole_y, body_thickness])
            difference() {
                cylinder(h = min(0.60, relief_height), r = hole_radius + 1.65, $fn = 32);
                translate([0, 0, -0.05])
                    cylinder(h = min(0.70, relief_height + 0.10), r = hole_radius + 0.22, $fn = 32);
            }

    color(PREVIEW_RELIEF_COLOR)
        print_text_block(
            line_1,
            line_2,
            body_width / 2 + block_param(editor_params, "car", 0, 0),
            body_height / 2 + block_param(editor_params, "car", 1, 0),
            line_1_width,
            line_1_height,
            line_1_font,
            line_1_relief,
            body_thickness,
            line_spacing,
            text_exact_fit
        );
}
