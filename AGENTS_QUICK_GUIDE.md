# Keychain Fair: краткая инструкция для агентов

Обновлено: 2026-08-04.

Этот файл заменяет старый handoff. Он нужен для быстрого входа в проект при скане репозитория: что запускать, где лежит основная логика и какие инварианты нельзя ломать.

## Что это за проект

`keychain-fair` - локальная система приема заказов на кастомные 3D-брелоки на ярмарке.

Основной поток:

1. Покупатель оформляет заказ на сайте `/` или через Telegram bot.
2. Оператор в `/admin` отмечает оплату.
3. Backend генерирует STL через OpenSCAD.
4. Заготовки печатаются пачкой на столе.
5. Текст печатается overlay-партиями поверх уже напечатанных заготовок.
6. После готового overlay место надо физически освободить и вручную вернуть в состояние `1`.

## Быстрый запуск

Главные точки входа для Windows:

```bat
start_server.bat
stop_server.bat
```

`start_server.bat` запускает сервер и предлагает:

- `1` - local server
- `2` - Docker server
- `3` - exit

`stop_server.bat` останавливает сервер и предлагает:

- `1` - local server
- `2` - Docker server
- `3` - exit

Прямые команды тоже есть:

```bat
start_server.bat local
start_server.bat docker
stop_server.bat local
stop_server.bat docker
```

При старте сервер спрашивает размер заготовки:

- `1` = `compact`, 56x24 мм, до 18 мест на столе
- `2` = `standard`, 64x30 мм, до 10 мест на столе

Выбор применяется только при старте процесса через `BLANK_SIZE_ID`. Не переключайте размер на лету. Если на столе уже есть активные/занятые места, backend сохраняет геометрию текущего стола до очистки мест.

## Runtime и конфиг

Основные URL:

- сайт: `http://127.0.0.1:8120/`
- админка: `http://127.0.0.1:8120/admin`
- health: `http://127.0.0.1:8120/api/health`

Важные настройки:

- `config/app.yaml` - основной конфиг.
- `.env` - секреты и overrides.
- `BLANK_SIZE_ID=compact|standard`; legacy alias `medium` нормализуется в `standard`.
- `ADMIN_PIN` - PIN админки, default `1337`.
- `SLICER_ENABLED`, `OCTOPRINT_ENABLED`, `OCTOPRINT_BASE_URL`, `OCTOPRINT_API_KEY` могут приходить из env.
- Docker stack использует Postgres и `bot/`; локальный запуск по умолчанию использует SQLite.

Текущая локальная печатная настройка в `config/app.yaml`:

- OpenSCAD: `C:\Program Files\OpenSCAD\openscad.exe`
- CuraEngine: `C:\Program Files\Ultimaker Cura 5.2.1\CuraEngine.exe`
- slicer profile: `config/slicer.ender3.yaml`
- OctoPrint: `http://127.0.0.1:5000`, `COM8`, `250000 baud`
- printer preheat: bed `60`, hotend `200`

## Логика заготовок и мест стола

Заготовка для общего стола берется из дизайна `classic_plate`:

- JSON: `designs/03_custom_rectangular.json`
- SCAD: `designs/classic_plate.scad`
- default size: `compact`

Раскладка считается в `keychain_fair/services.py`:

- bed: `queue.bed_size_mm`, сейчас `[220, 220]`
- spacing: `queue.item_spacing_mm`, сейчас `8`
- Y offset: `BLANK_TABLE_Y_OFFSET_MM = 5.0`
- compact 56x24 -> 3 колонки x 6 рядов = 18 мест
- standard 64x30 -> 2 колонки x 5 рядов = 10 мест

Состояния slot:

| code | state | смысл |
| --- | --- | --- |
| `1` | `empty` | пустое место, можно печатать заготовку |
| `2` | `blank_queued` | заготовка будет напечатана |
| `3` | `blank_printing` | заготовка печатается |
| `4` | `blank_printed` | заготовка напечатана, можно печатать текст |
| `5` | `overlay_queued` | текст будет напечатан поверх |
| `6` | `overlay_printing` | текст печатается поверх |
| `7` | `overlay_printed_needs_clear` | текст напечатан, место надо освободить |

Переходы:

- blank print: `1 -> 2 -> 3 -> 4`
- overlay print: `4 -> 5 -> 6 -> 7`
- stop/cancel возвращает активную печать назад в очередь: `3 -> 2`, `6 -> 5`
- ручная установка `1` освобождает место
- ручная установка `4` делает место доступным для overlay
- `7` блокирует место до физической очистки

Админка может вручную менять состояние каждого слота через `PATCH /api/admin/blanks/{blank_batch_id}/slots/{slot_index}` с `{ "state_code": 1..7 }`.
Нельзя вручную менять место, которое реально печатается в состоянии `3` или `6`.

`POST /api/admin/blanks/print` принимает `{ "target_count": N }`. Это целевое общее число занятых мест, не "добавить N". Если уже занято 6 мест, а оператор просит `10`, будут выбраны только 4 пустых. Если печатать нечего, API возвращает 200 с no-op сообщением и не создает новый G-code/upload.

## Overlay и размеры

Overlay допускается только на слот того же физического размера:

- `build_print_batches()` берет только слоты в состоянии `4`.
- Заказ должен совпадать со слотом по `width_mm` и `height_mm`, а также помещаться по footprint.
- Если активный стол compact, standard-заказы не должны уходить на overlay этого стола.
- Telegram preview и сообщение подтверждения используют актуальный размер заказа, а не статичную картинку дизайна.

При смене размера заготовок:

1. Завершите или освободите текущие занятые места.
2. Поставьте все места в `1`, если стол физически очищен.
3. Остановите сервер через `stop_server.bat`.
4. Запустите снова через `start_server.bat` и выберите новый размер.

## Slicer и старт печати

Актуальный профиль: `config/slicer.ender3.yaml`.

Важные инварианты профиля:

- `adhesion_type: brim`
- `brim_width: 1.2` при line width `0.4`, то есть примерно 3 линии
- `skirt_line_count: 0`
- стартовый G-code не должен рисовать две purge-линии вдоль стола
- nozzle print target остается `205`, но стартовый G-code сначала ждет `M109 S210`, затем переводит на `M104 S205`, чтобы прошивка не зависала около 195 градусов

Тест `tests/test_adapters.py::test_cura_slicer_uses_profile_and_repairs_header` проверяет профиль Cura и repair header.

## Важные файлы

- `keychain_fair/config.py` - env/config loading, `BLANK_SIZE_ID`, aliases.
- `keychain_fair/services.py` - очередь, blank slots, overlay, printer state transitions.
- `keychain_fair/main.py` - FastAPI endpoints.
- `keychain_fair/models.py` - API models, включая `BlankPrintRequest` и `BlankSlotUpdate`.
- `static/admin.js` - админка, preview стола, counts, slot state select.
- `bot/src/index.ts`, `bot/src/api.ts`, `bot/src/flow.ts` - Telegram bot.
- `config/slicer.ender3.yaml` - текущий Cura profile.
- `docker-compose.yml` - Postgres + API + Telegram bot.
- `start_server.bat` - запуск local/Docker.
- `stop_server.bat` - остановка local/Docker.

## Проверки перед сдачей

Backend:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Bot:

```powershell
cd bot
cmd /c npm test
cmd /c npm run build
cd ..
```

Дополнительно:

```powershell
node --check static\admin.js
git diff --check
```

Последний полный известный зеленый набор до этого документа:

- backend pytest: `120 passed`
- bot tests: `10 passed`
- bot build: ok
- `node --check static\admin.js`: ok
- `git diff --check`: ok

После изменений в батниках точечно проверены сценарии размера заготовки: `4 passed`.

## Осторожно

- Не удаляйте `data/`, `generated/`, `octoprint/` и `.env`, если пользователь явно не просит.
- Не меняйте физический размер активного стола без очистки slot states.
- Не сбрасывайте пользовательские изменения через `git reset --hard` или `git checkout --`.
- Все текстовые файлы держать в UTF-8 без BOM, см. `AGENTS.md`.
