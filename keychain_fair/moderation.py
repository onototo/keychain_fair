from __future__ import annotations

import re
import unicodedata


MUTE_SECONDS = 5 * 60

_LEET_TRANSLATION = str.maketrans(
    {
        "0": "о",
        "1": "и",
        "3": "е",
        "4": "а",
        "5": "с",
        "6": "б",
        "7": "т",
        "8": "в",
        "@": "а",
        "$": "с",
        "a": "а",
        "b": "б",
        "c": "с",
        "e": "е",
        "h": "х",
        "i": "и",
        "k": "к",
        "m": "м",
        "n": "н",
        "o": "о",
        "p": "п",
        "r": "р",
        "s": "с",
        "t": "т",
        "u": "у",
        "x": "х",
        "y": "у",
    }
)

_PROFANITY_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"х+у+[йиеяю]",
        r"п+[ие]+з+д",
        r"[её]+б",
        r"б+л+[яиа]+[дт]",
        r"м+у+д+[ао]",
        r"с+у+к+а",
    ]
]


def _moderation_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    normalized = normalized.translate(_LEET_TRANSLATION)
    compact = re.sub(r"[^0-9a-zа-я]+", "", normalized)
    return re.sub(r"(.)\1{2,}", r"\1\1", compact)


def contains_profanity(*values: str | None) -> bool:
    haystack = " ".join(_moderation_key(value or "") for value in values)
    if not haystack:
        return False
    return any(pattern.search(haystack) for pattern in _PROFANITY_PATTERNS)
