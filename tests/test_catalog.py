from pathlib import Path
import base64

from fastapi.testclient import TestClient

from keychain_fair.catalog import load_catalog
from keychain_fair.config import AppSettings
from keychain_fair.main import create_app


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def write_product(folder: Path, title: str, price: str, description: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    folder.joinpath("product.yaml").write_text(
        f"title: {title}\nprice_byn: {price}\ndescription: {description}\n",
        encoding="utf-8",
    )


def test_folder_name_is_the_category_title_when_yaml_is_missing(tmp_path: Path):
    write_product(tmp_path / "figurki" / "lisa", "Лиса", "7")
    catalog = load_catalog(tmp_path)
    assert catalog.categories[0].title == "figurki"
    assert catalog.find("figurki/lisa").price_kopecks == 700


def test_hidden_folders_and_bad_prices_stay_out_of_the_catalog(tmp_path: Path):
    write_product(tmp_path / "toys" / "ok", "Нормальный", "5")
    write_product(tmp_path / "toys" / "cents", "Дробный", "1.555")
    write_product(tmp_path / "_secret" / "hidden", "Скрытый", "9")
    (tmp_path / "toys" / "notes").mkdir()
    catalog = load_catalog(tmp_path)
    assert [product.id for product in catalog.categories[0].products] == ["toys/ok"]
    assert catalog.find("_secret/hidden") is None
    assert any("cents" in warning for warning in catalog.warnings)
    assert any("notes" in warning for warning in catalog.warnings)


def test_cover_uses_the_first_image_name_and_catalog_is_reread(tmp_path: Path):
    product = tmp_path / "catalog" / "toys" / "skull"
    write_product(product, "Череп", "10", "Маленький")
    (product / "b.png").write_bytes(b"later")
    (product / "a.png").write_bytes(PNG)
    offices = tmp_path / "offices.json"
    offices.write_text("[]", encoding="utf-8")
    client = TestClient(
        create_app(
            AppSettings(
                host="127.0.0.1",
                port=8120,
                database_path=tmp_path / "shop.sqlite3",
                catalog_dir=tmp_path / "catalog",
                offices_path=offices,
                cart_max_units=99,
                internal_token="token",
            )
        )
    )

    listed = client.get("/api/catalog").json()["categories"][0]["products"][0]
    assert listed["has_image"] is True
    cover = client.get("/api/catalog/toys/skull/cover")
    assert cover.status_code == 200
    assert cover.content == PNG
    assert client.get("/api/catalog/toys/missing/cover").status_code == 404

    write_product(tmp_path / "catalog" / "toys" / "cube", "Кубик", "4")
    ids = [item["id"] for item in client.get("/api/catalog").json()["categories"][0]["products"]]
    assert ids == ["toys/cube", "toys/skull"]
