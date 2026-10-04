from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import logging

import yaml


logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class Product:
    id: str
    category_id: str
    category_title: str
    title: str
    description: str
    price_kopecks: int
    images: tuple[Path, ...]

    @property
    def price_byn(self) -> str:
        return format_byn(self.price_kopecks)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "price_kopecks": self.price_kopecks,
            "price_byn": self.price_byn,
            "has_image": bool(self.images),
        }


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    products: tuple[Product, ...]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "products": [product.public() for product in self.products],
        }


@dataclass(frozen=True)
class Catalog:
    categories: tuple[Category, ...]
    warnings: tuple[str, ...]

    def find(self, product_id: str) -> Product | None:
        for category in self.categories:
            for product in category.products:
                if product.id == product_id:
                    return product
        return None

    def public(self, max_units: int) -> dict[str, Any]:
        return {
            "max_units": max_units,
            "categories": [category.public() for category in self.categories],
        }


def format_byn(kopecks: int) -> str:
    rubles, cents = divmod(int(kopecks), 100)
    if cents == 0:
        return f"{rubles} BYN"
    return f"{rubles}.{cents:02d} BYN"


def parse_price_kopecks(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("price_byn is required")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("price_byn must be a number") from error
    quantized = amount.quantize(Decimal("0.01"))
    if amount != quantized:
        raise ValueError("price_byn supports up to 2 decimal places")
    kopecks = int(quantized * 100)
    if kopecks <= 0:
        raise ValueError("price_byn must be greater than 0")
    return kopecks


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return loaded


def _images(folder: Path) -> tuple[Path, ...]:
    files = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES]
    return tuple(sorted(files, key=lambda path: path.name.casefold()))


def _safe_folder(path: Path) -> bool:
    name = path.name
    return path.is_dir() and name not in {".", ".."} and not name.startswith((".", "_")) and "/" not in name and "\\" not in name


def load_catalog(root: Path) -> Catalog:
    warnings: list[str] = []
    categories: list[Category] = []
    if not root.exists():
        return Catalog((), (f"Catalog folder does not exist: {root}",))

    for category_dir in sorted((path for path in root.iterdir() if _safe_folder(path)), key=lambda path: path.name.casefold()):
        try:
            category_meta = _read_yaml(category_dir / "category.yaml")
        except ValueError as error:
            warnings.append(str(error))
            continue
        title = str(category_meta.get("title") or category_dir.name).strip()
        if not title:
            warnings.append(f"Skip category without a title: {category_dir.name}")
            continue
        products: list[Product] = []
        for product_dir in sorted((path for path in category_dir.iterdir() if _safe_folder(path)), key=lambda path: path.name.casefold()):
            product_file = product_dir / "product.yaml"
            if not product_file.exists():
                warnings.append(f"Skip product without product.yaml: {category_dir.name}/{product_dir.name}")
                continue
            try:
                meta = _read_yaml(product_file)
                product_title = str(meta.get("title") or "").strip()
                if not product_title:
                    raise ValueError("title is required")
                price = parse_price_kopecks(meta.get("price_byn"))
                description = str(meta.get("description") or "").strip()
            except ValueError as error:
                warnings.append(f"Skip {category_dir.name}/{product_dir.name}: {error}")
                continue
            products.append(
                Product(
                    id=f"{category_dir.name}/{product_dir.name}",
                    category_id=category_dir.name,
                    category_title=title,
                    title=product_title,
                    description=description,
                    price_kopecks=price,
                    images=_images(product_dir),
                )
            )
        if products:
            categories.append(Category(id=category_dir.name, title=title, products=tuple(products)))
        else:
            warnings.append(f"Skip empty category: {category_dir.name}")
    return Catalog(tuple(categories), tuple(warnings))
