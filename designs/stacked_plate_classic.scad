use <stacked_plate_common.scad>

module keychain(customer_name, car_number, phone_number, selected_elements, plate_width, plate_height, plate_thickness, font_size, editor_params = []) {
    stacked_plate_keychain(
        customer_name,
        car_number,
        phone_number,
        selected_elements,
        plate_width,
        plate_height,
        plate_thickness,
        font_size,
        "classic",
        editor_params
    );
}
