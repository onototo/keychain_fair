# Keychain Fair

Локальная система для приема заказов на кастомные 3D-брелоки на ярмарке.

## Быстрый старт

```powershell
cd D:\2COEm\keychain-fair
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn keychain_fair.main:app --host 0.0.0.0 --port 8120
```

Открыть:

- Покупательский сайт: `http://127.0.0.1:8120/`
- Панель оператора: `http://127.0.0.1:8120/admin`

PIN оператора задается в `.env` переменной `ADMIN_PIN`.

## Внешние инструменты

MVP работает без установленного OpenSCAD/Cura/OctoPrint, но в этом режиме после оплаты заказ перейдет в `error` с диагностикой отсутствующего инструмента. Для реальной печати нужно:

1. Установить OpenSCAD и прописать `external_tools.openscad_path` в `config/app.yaml`.
2. Установить UltiMaker Cura/CuraEngine и заполнить `config/slicer.example.yaml` под свой принтер.
3. Поднять OctoPrint, подключить принтер по USB, включить API key и прописать `OCTOPRINT_API_KEY` в `.env`.

## Дизайны

Дизайны лежат в `designs/*.json` и используют `.scad`-шаблоны из той же папки. Новые или измененные JSON-файлы перечитываются при каждом открытии сайта и при каждом API-запросе `/api/designs`.

## Тесты

```powershell
.\.venv\Scripts\python -m pytest
```
