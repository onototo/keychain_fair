import pytest

from keychain_fair.validation import (
    normalize_by_car_number,
    normalize_car_number,
    normalize_customer_name,
    split_by_car_number,
)


def test_normalizers_accept_common_inputs():
    assert normalize_customer_name("  Anna   Maria  ") == "Anna Maria"
    assert normalize_car_number(" 1234 ab-7 ") == "1234 AB-7"
    assert normalize_by_car_number("1234AB7") == "1234 AB-7"
    assert split_by_car_number("1234 AB-7") == ("1234", "AB-7")


def test_name_rejects_digits_and_long_values():
    with pytest.raises(ValueError):
        normalize_customer_name("Anna1")
    with pytest.raises(ValueError):
        normalize_customer_name("A" * 31)


def test_car_number_rejects_unknown_and_non_latin_formats():
    with pytest.raises(ValueError):
        normalize_car_number("AB1234")
    with pytest.raises(ValueError):
        normalize_car_number("\u0410 123 \u0412\u0421-7")
