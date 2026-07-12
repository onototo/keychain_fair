import re
import unicodedata


RU_CAR_LETTERS = "ABEKMHOPCTYXАВЕКМНОРСТУХ"
BY_CAR_LETTERS = "ABCEHIKMOPTXАВСЕНІКМОРТХ"
RU_CAR_RE = re.compile(rf"^[{RU_CAR_LETTERS}]\d{{3}}[{RU_CAR_LETTERS}]{{2}}\d{{2,3}}$")
BY_CAR_RE = re.compile(rf"^\d{{4}}[{BY_CAR_LETTERS}]{{2}}[1-7]$")
RU_MOBILE_RE = re.compile(r"^[78]9\d{9}$")
BY_MOBILE_RE = re.compile(r"^375(25|29|33|44)\d{7}$")
PROFANITY_ROOTS = (
    "fuck",
    "shit",
    "bitch",
    "cunt",
    "dickhead",
    "asshole",
    "хуй",
    "хуе",
    "хуя",
    "хуи",
    "пизд",
    "бляд",
    "блят",
    "еба",
    "еби",
    "ебл",
    "ебн",
    "ебу",
    "заеб",
    "уеб",
    "сука",
    "мудак",
    "мудил",
)


def _is_name_character(char: str) -> bool:
    if char == " ":
        return True
    if not char.isalpha():
        return False
    name = unicodedata.name(char, "")
    return "LATIN" in name or "CYRILLIC" in name


def _contains_profanity(value: str) -> bool:
    normalized = value.casefold().replace("ё", "е")
    compact = re.sub(r"\s+", "", normalized)
    return any(root in compact for root in PROFANITY_ROOTS)


def normalize_phone(value: str) -> str:
    phone = value.strip()
    if not phone.isdigit():
        raise ValueError("Телефон должен содержать только цифры")
    if not (RU_MOBILE_RE.match(phone) or BY_MOBILE_RE.match(phone)):
        raise ValueError("Телефон должен быть мобильным номером РБ или РФ")
    return phone


def normalize_car_number(value: str) -> str:
    car_number = re.sub(r"[\s-]+", "", value.strip().upper())
    if not (RU_CAR_RE.match(car_number) or BY_CAR_RE.match(car_number)):
        raise ValueError("Номер авто должен быть в формате РБ или РФ")
    return car_number


def normalize_customer_name(value: str) -> str:
    name = " ".join(value.strip().split())
    if len(name) < 2 or len(name) > 30:
        raise ValueError("Имя должно быть от 2 до 30 символов")
    if not all(_is_name_character(char) for char in name):
        raise ValueError("Имя должно содержать только буквы латиницы/кириллицы")
    if _contains_profanity(name):
        raise ValueError("Имя содержит недопустимые слова")
    return name
