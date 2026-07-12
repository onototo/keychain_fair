# Тесты проекта

Этот набор проверяет серверную часть ярмарочного приложения без реального OpenSCAD, CuraEngine, OctoPrint и принтера. Внешние инструменты заменены фейками из `tests/support.py`, поэтому обычный прогон должен быть быстрым и безопасным для локальной машины.

## Как запускать

Из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

В `pyproject.toml` уже задан `--basetemp=tmp/pytest`: pytest складывает временные базы, STL/G-code и другие артефакты в игнорируемую папку проекта, а не в системный `%TEMP%`.

Точечные запуски:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_order_flow.py
.\.venv\Scripts\python.exe -m pytest tests/test_adapters.py::test_cura_slicer_uses_profile_and_repairs_header
```

## Карта набора

- `test_validation.py` проверяет нормализацию имени, номера авто и телефона.
- `test_designs.py` проверяет загрузку JSON-дизайнов, наличие SCAD-шаблонов и правила выбора элементов.
- `test_batching.py` проверяет раскладку заказов по рабочему столу принтера.
- `test_adapters.py` проверяет командные обертки OpenSCAD/CuraEngine и логику подключения OctoPrint через monkeypatch, без запуска реальных бинарей.
- `test_order_flow.py` проверяет основные API-потоки заказов: создание и оплату, подготовку очереди, восстановление статусов, удаление заказа, PIN администратора и системные URL.
- `test_printing_api.py` проверяет старт партии на печать, отказ при неготовом принтере или отключенном slicer, управление нагревом, resume и stop.
- `test_editor_api.py` проверяет API редактора модели: параметры, preview STL, сохранение пресета и snapshot параметров заказа.
- `test_stacked_plate_api.py` проверяет бизнес-правила дизайна `stacked_plate_classic`.
- `test_printer_status.py` параметризованно проверяет перевод ответов OctoPrint в состояние админки.
- `support.py` содержит общие фейки, фабрики настроек/приложения и helpers для заказов.

## Правила поддержки

- Для новых API-сценариев сначала используйте `create_test_app`, `make_settings`, `create_paid_order` и `ADMIN_HEADERS` из `tests/support.py`.
- Не добавляйте реальные вызовы OpenSCAD, CuraEngine, OctoPrint или сетевые обращения в обычный pytest-набор. Для ручных smoke-проверок лучше завести отдельный скрипт или явно помеченный тест.
- Если поведение можно проверить на уровне чистой функции или адаптера, не добавляйте новый длинный API-flow.
- Для похожих состояний используйте `pytest.mark.parametrize`, как в `test_printer_status.py`, чтобы не раздувать файл копиями одного сценария.
- Тесты могут читать штатные файлы из `designs/` и `config/`, но все изменяемые данные должны жить в `tmp_path`.
