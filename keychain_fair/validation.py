import re
import unicodedata


BY_CAR_LETTERS = "ABCEHIKMOPTX"
BY_CAR_RE = re.compile(rf"^\d{{4}}[{BY_CAR_LETTERS}]{{2}}[1-7]$")


def _is_name_character(char: str) -> bool:
    if char == " ":
        return True
    if not char.isalpha():
        return False
    name = unicodedata.name(char, "")
    return "LATIN" in name or "CYRILLIC" in name


def normalize_car_number(value: str) -> str:
    return normalize_by_car_number(value)


def normalize_by_car_number(value: str) -> str:
    car_number = re.sub(r"[\s-]+", "", value.strip().upper())
    if not BY_CAR_RE.match(car_number):
        raise ValueError("Car number must use BY format 1234 AB-7 with Latin letters")
    return f"{car_number[:4]} {car_number[4:6]}-{car_number[6]}"


def split_by_car_number(value: str) -> tuple[str, str]:
    normalized = normalize_by_car_number(value)
    digits, suffix = normalized.split(" ", 1)
    return digits, suffix


def normalize_customer_name(value: str) -> str:
    name = " ".join(value.strip().split())
    if len(name) < 2 or len(name) > 30:
        raise ValueError("Name must be from 2 to 30 characters")
    if not all(_is_name_character(char) for char in name):
        raise ValueError("Name may contain only Latin/Cyrillic letters and spaces")
    return name
