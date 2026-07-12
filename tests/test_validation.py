import pytest

from keychain_fair.validation import normalize_car_number, normalize_customer_name, normalize_phone


def test_normalizers_accept_common_inputs():
    assert normalize_customer_name("  Анна   Мария  ") == "Анна Мария"
    assert normalize_car_number(" а 123 вс-77 ") == "А123ВС77"
    assert normalize_car_number(" 1234 ab-7 ") == "1234AB7"
    assert normalize_phone("375291234567") == "375291234567"
    assert normalize_phone("79991234567") == "79991234567"


def test_name_rejects_digits_and_long_values():
    with pytest.raises(ValueError):
        normalize_customer_name("Анна1")
    with pytest.raises(ValueError):
        normalize_customer_name("А" * 31)


def test_name_rejects_profanity_but_accepts_normal_names():
    with pytest.raises(ValueError):
        normalize_customer_name("Fuck")
    with pytest.raises(ValueError):
        normalize_customer_name("Хуев")

    assert normalize_customer_name("Глеб") == "Глеб"
    assert normalize_customer_name("Anna Maria") == "Anna Maria"


def test_car_number_rejects_unknown_format():
    with pytest.raises(ValueError):
        normalize_car_number("AB1234")


def test_phone_rejects_separators_and_short_number():
    with pytest.raises(ValueError):
        normalize_phone("+375291234567")
    with pytest.raises(ValueError):
        normalize_phone("123")
