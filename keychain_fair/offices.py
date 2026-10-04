from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class Office:
    id: str
    number: str
    city: str
    address: str

    def public(self) -> dict[str, str]:
        return {
            "id": self.id,
            "number": self.number,
            "city": self.city,
            "address": self.address,
        }


def load_offices(path: Path) -> tuple[Office, ...]:
    if not path.exists():
        return ()
    raw = json.loads(path.read_text(encoding="utf-8"))
    offices: list[Office] = []
    for item in raw:
        office_id = str(item.get("id") or item.get("number") or "").strip()
        number = str(item.get("number") or office_id).strip()
        city = str(item.get("city") or "").strip()
        address = str(item.get("address") or "").strip()
        if office_id and city and address:
            offices.append(Office(office_id, number, city, address))
    return tuple(offices)


def search_offices(offices: tuple[Office, ...], query: str, limit: int = 24) -> tuple[Office, ...]:
    needle = " ".join(query.casefold().split())
    if len(needle) < 2:
        return ()

    def rank(office: Office) -> tuple[int, str, int]:
        city = office.city.casefold()
        address = office.address.casefold()
        if city == needle:
            group = 0
        elif city.startswith(needle):
            group = 1
        elif needle in city:
            group = 2
        elif needle in address:
            group = 3
        else:
            group = 9
        return (group, city, int(office.number) if office.number.isdigit() else 10**9)

    matched = [office for office in offices if rank(office)[0] < 9]
    matched.sort(key=rank)
    return tuple(matched[:limit])
