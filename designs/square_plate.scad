module rounded_square_2d(size, radius) {
    hull() {
        translate([radius, radius]) circle(r = radius, $fn = 20);
        translate([size - radius, radius]) circle(r = radius, $fn = 20);
        translate([radius, size - radius]) circle(r = radius, $fn = 20);
        translate([size - radius, size - radius]) circle(r = radius, $fn = 20);
    }
}

function ep(params, index, fallback) = len(params) > index ? params[index] : fallback;
function block_start(kind) = kind == "name" ? 7 : kind == "car" ? 13 : 19;
function block_param(params, kind, offset, fallback) = ep(params, block_start(kind) + offset, fallback);
function block_param_pos(params, kind, offset, fallback) =
    block_param(params, kind, offset, fallback) > 0 ? block_param(params, kind, offset, fallback) : fallback;
PREVIEW_BASE_COLOR = "#2f7f77";
PREVIEW_RELIEF_COLOR = "#f0b429";

module border(size, radius, border_width, height_on_top) {
    linear_extrude(height = height_on_top) {
        difference() {
            rounded_square_2d(size, radius);
            translate([border_width, border_width])
                rounded_square_2d(size - 2 * border_width, max(0.6, radius - border_width));
        }
    }
}

module print_line(label, x, y, width, height, font_size, relief, z) {
    fitted = min(font_size, min(width / max(1, len(label)) * 1.28, height * 0.74));
    translate([x, y, z])
        linear_extrude(height = relief)
            text(label, size = fitted, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
}

module keychain(customer_name, line_1, line_2, selected_elements, plate_width, plate_height, plate_thickness, font_size, editor_params = []) {
    body_width = ep(editor_params, 0, plate_width);
    body_height = ep(editor_params, 1, plate_height);
    body_size = min(body_width, body_height);
    body_thickness = ep(editor_params, 2, plate_thickness);
    relief_height = ep(editor_params, 3, 0.8);
    radius = min(3.8, body_size * 0.12);
    hole_x = ep(editor_params, 4, 5.8);
    hole_y = ep(editor_params, 5, max(5.8, body_size - 5.8));
    hole_radius = ep(editor_params, 6, 2.15);
    top_width = block_param_pos(editor_params, "car", 2, body_size - 12);
    top_height = block_param_pos(editor_params, "car", 3, body_size * 0.28);
    top_font = block_param_pos(editor_params, "car", 4, font_size);
    top_relief = block_param_pos(editor_params, "car", 5, relief_height);
    bottom_width = block_param_pos(editor_params, "phone", 2, body_size - 12);
    bottom_height = block_param_pos(editor_params, "phone", 3, body_size * 0.24);
    bottom_font = block_param_pos(editor_params, "phone", 4, font_size * 0.72);
    bottom_relief = block_param_pos(editor_params, "phone", 5, relief_height);
    outline_height = max(top_relief, bottom_relief);

    color(PREVIEW_BASE_COLOR)
        difference() {
            linear_extrude(height = body_thickness)
                rounded_square_2d(body_size, radius);
            translate([hole_x, hole_y, -0.1])
                cylinder(h = body_thickness + 0.25, r = hole_radius, $fn = 30);
        }

    color(PREVIEW_RELIEF_COLOR)
        translate([0, 0, body_thickness])
            border(body_size, radius, 1.1, outline_height);

    color(PREVIEW_RELIEF_COLOR)
        translate([hole_x, hole_y, body_thickness])
            difference() {
                cylinder(h = min(0.54, relief_height), r = hole_radius + 1.35, $fn = 30);
                translate([0, 0, -0.05])
                    cylinder(h = min(0.64, relief_height + 0.10), r = hole_radius + 0.20, $fn = 30);
            }

    color(PREVIEW_RELIEF_COLOR) {
        print_line(line_1, body_size / 2 + block_param(editor_params, "car", 0, 0), body_size * 0.58 + block_param(editor_params, "car", 1, 0), top_width, top_height, top_font, top_relief, body_thickness);
        print_line(line_2, body_size / 2 + block_param(editor_params, "phone", 0, 0), body_size * 0.36 + block_param(editor_params, "phone", 1, 0), bottom_width, bottom_height, bottom_font, bottom_relief, body_thickness);
    }
}
