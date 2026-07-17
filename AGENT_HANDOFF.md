# Handoff для настройки Keychain Fair на другом ПК

## 1. Что это за проект

`keychain-fair` - standalone MVP для локальной продажи кастомных 3D-брелков на ярмарке.

Сценарий:

1. Покупатель сканирует QR и открывает локальный сайт.
2. Вводит имя, номер авто и телефон.
3. Выбирает дизайн, размер и простые элементы.
4. Подтверждает заказ, заказ получает статус `не оплачен`.
5. Оператор в `/admin` отмечает оплату.
6. Worker пытается создать STL через OpenSCAD и поставить заказ в очередь печати.
7. CuraEngine/OctoPrint заложены адаптерами, но требуют настройки под реальный принтер.

Проект сделан без Node/NPM: backend и статика работают через Python/FastAPI.

## 2. Текущее состояние

Рабочая реализация уже есть:

- FastAPI backend.
- SQLite база заказов.
- Покупательский UI: `/`.
- Операторская панель: `/admin`.
- Страница статуса заказа: `/status/{order_id}`.
- Каталог дизайнов из `designs/*.json`.
- OpenSCAD-шаблоны из `designs/*.scad`.
- Фоновая очередь обработки заказов.
- Адаптеры для OpenSCAD, CuraEngine, OctoPrint.
- Тесты API, валидации, каталога дизайнов и batching.

На исходном ПК OpenSCAD, CuraEngine и OctoPrint не были установлены/настроены, поэтому реальная генерация STL и печать на железе еще не проверялись.

## 3. Важные файлы и папки

- `README.md` - краткий запуск и базовые инструкции.
- `.env.example` - пример локальных секретов.
- `.env` - текущий локальный файл, в архиве есть, но на новом ПК лучше проверить значения.
- `requirements.txt` - зафиксированные Python-зависимости.
- `config/app.yaml` - главный конфиг приложения, путей к инструментам, очереди, OctoPrint.
- `config/slicer.example.yaml` - заготовка профиля CuraEngine.
- `designs/*.json` - список доступных дизайнов, размеров, цен, элементов.
- `designs/*.scad` - OpenSCAD-шаблоны брелков.
- `keychain_fair/` - backend-код.
- `static/` - HTML/CSS/JS интерфейсы.
- `tests/` - автотесты.
- `data/orders.sqlite3` - текущая SQLite-база, создается автоматически.
- `generated/` - STL/SCAD/G-code артефакты, создаются автоматически.
- `logs/` - логи uvicorn.

## 4. Быстрый запуск после распаковки

Предполагаемый путь после распаковки:

```powershell
cd D:\2COEm\keychain-fair
```

В архиве есть `.venv`, но оно переносимо только между совместимыми Windows/Python окружениями. Надежнее пересоздать:

```powershell
if (Test-Path .venv) { Rename-Item .venv .venv.from-archive }
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Запуск:

```powershell
.\.venv\Scripts\python -m uvicorn keychain_fair.main:app --host 0.0.0.0 --port 8080
```

Проверка:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/designs
```

Открыть в браузере:

- Покупатель: `http://127.0.0.1:8080/`
- Оператор: `http://127.0.0.1:8080/admin`

PIN оператора по умолчанию сейчас `1234`; менять в `.env`:

```env
ADMIN_PIN=1337
OCTOPRINT_API_KEY=
```

## 5. Запуск в локальной сети / hotspot

1. Поднять Wi-Fi hotspot на ноутбуке или подключить ноутбук и телефоны к одному роутеру.
2. Запустить сервер с `--host 0.0.0.0`.
3. Открыть `/admin`.
4. В правом блоке админки будет QR и локальный URL сайта.
5. Покупатели должны открыть именно IP ноутбука в этой сети, например `http://192.168.x.x:8080/`.

Если телефон не открывает сайт:

- проверить, что телефон в той же сети;
- проверить Windows Firewall для Python/порта 8080;
- открыть `http://<ip-ноутбука>:8080/api/health` с телефона.

## 6. Проверка автотестами

```powershell
cd D:\2COEm\keychain-fair
.\.venv\Scripts\python -m pytest
```

Ожидаемый результат на исходной реализации:

```text
7 passed
```

Предупреждение FastAPI/TestClient про `httpx` допустимо и не ломает работу.

## 7. Настройка OpenSCAD для STL

Нужно для перехода `оплачен -> STL готов`.

1. Установить OpenSCAD.
2. Найти путь к `openscad.exe`.
3. Проверить:

```powershell
& "C:\Program Files\OpenSCAD\openscad.exe" --version
```

4. Обновить `config/app.yaml`:

```yaml
external_tools:
  openscad_path: C:\Program Files\OpenSCAD\openscad.exe
  openscad_timeout_seconds: 60
```

5. Перезапустить сервер.
6. В `/admin` проверить `/api/admin/tools`: OpenSCAD должен быть `ok: true`.

Основной код генерации:

- `keychain_fair/adapters.py` -> `OpenScadModelGenerator.generate_order_stl`
- `designs/*.scad`
- `designs/*.json`

Если OpenSCAD ругается на шрифты, проверить наличие `Liberation Sans` или заменить font в `.scad` на доступный системный шрифт.

## 8. Настройка CuraEngine

В MVP адаптер CuraEngine заложен, но профиль пока является заготовкой. Его нужно довести под конкретную версию Cura и конкретный принтер.

Что сделать:

1. Установить UltiMaker Cura.
2. Найти `CuraEngine.exe`.
3. Обновить `config/app.yaml`:

```yaml
external_tools:
  cura_engine_path: C:\Program Files\UltiMaker Cura X.X.X\CuraEngine.exe

slicer:
  enabled: true
  profile_path: config/slicer.example.yaml
```

4. Заполнить `config/slicer.example.yaml` реальными параметрами:

- модель принтера;
- размер стола;
- сопло;
- материал;
- температуры;
- слой;
- скорость;
- infill;
- реальные Cura definition/settings JSON, если выбранный CuraEngine требует их.

5. Проверить и при необходимости доработать `CuraEngineSlicer.slice_plate` в `keychain_fair/adapters.py`.

Текущая команда slicing в MVP намеренно минимальная:

```text
CuraEngine slice -o <batch>.gcode -l <plate>.stl
```

Для реальной Cura-сборки почти наверняка понадобятся `-j` definition/settings JSON и набор `-s key=value`.

## 9. Настройка OctoPrint

Рекомендуемый путь управления USB-принтером:

1. Установить и запустить OctoPrint на этом же ноутбуке или отдельном хосте.
2. Подключить 3D-принтер к OctoPrint-хосту по USB.
3. В OctoPrint получить API key.
4. В `.env` указать:

```env
OCTOPRINT_API_KEY=<api-key>
```

5. В `config/app.yaml` включить:

```yaml
octoprint:
  enabled: true
  base_url: http://127.0.0.1:5000
  api_key_env: OCTOPRINT_API_KEY
```

6. Перезапустить сервер.

Основной код:

- `keychain_fair/adapters.py` -> `OctoPrintController.upload_and_print`
- `keychain_fair/adapters.py` -> `OctoPrintController.get_job`

На текущем этапе приложение отправляет G-code в OctoPrint, если slicing включен и G-code создан. Полный polling прогресса печати можно расширять через OctoPrint Job API.

## 10. Типовой ручной smoke-тест

1. Запустить сервер.
2. Открыть `http://127.0.0.1:8080/`.
3. Создать заказ.
4. Открыть `http://127.0.0.1:8080/admin`, PIN `1337`.
5. Перевести заказ в `оплачен`.
6. Нажать `Обработать очередь`.
7. Если OpenSCAD не настроен, заказ должен перейти в `ошибка` с понятной диагностикой.
8. Если OpenSCAD настроен, проверить появление файлов в `generated/orders/<order_id>/`.
9. После настройки CuraEngine/OctoPrint проверить `generated/batches/<batch_id>/`.

## 11. Как добавлять дизайны

1. Скопировать существующий JSON, например `designs/03_custom_rectangular.json`.
2. Дать новый `id`, `name`, `template`.
3. Добавить или изменить `sizes`.
4. Добавить поддерживаемые элементы.
5. Создать или скопировать `.scad`-шаблон с модулем:

```scad
module keychain(customer_name, car_number, phone_number, selected_elements, plate_width, plate_height, plate_thickness, font_size) {
    ...
}
```

6. Обновить сайт в браузере. Frontend запрашивает `/api/designs`, пересборка не нужна.

## 12. Известные ограничения MVP

- Онлайн-оплаты нет, сейчас только наличные и ручное выставление статуса `оплачен`.
- Telegram/Viber уведомления не реализованы. Нельзя надежно отправлять сообщение просто по номеру телефона: пользователь должен сначала открыть/подписаться на бота.
- CuraEngine-профиль требует реальной настройки под принтер.
- Прогресс печати через OctoPrint пока заложен адаптером, но не выведен полноценно в UI.
- Нет полноценной авторизации, только PIN для локальной админки.
- Нет миграционной системы БД; SQLite-схема создается в `Database.init_schema`.

## 13. Команды диагностики

Проверить порт:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen
```

Остановить uvicorn:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*uvicorn keychain_fair.main:app*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Проверить OpenSCAD из приложения:

```powershell
Invoke-WebRequest -UseBasicParsing -Headers @{ 'X-Admin-Pin'='1234' } http://127.0.0.1:8080/api/admin/tools
```

Посмотреть логи:

```powershell
Get-Content logs\uvicorn.err.log -Tail 80
```

## 14. Фактическая настройка этого ПК на 2026-07-07

- Рабочая папка: `D:\keychain-fair`.
- Архивное `.venv` было привязано к старому Python `C:\Users\mozolev\...\Python313`; оно переименовано в `.venv.from-archive`, новое `.venv` создано на Python `3.13.7` из `M:\Python\python.exe`.
- Python-зависимости из `requirements.txt` установлены, автотесты проходят: `7 passed`.
- OpenSCAD установлен через winget: `C:\Program Files\OpenSCAD\openscad.exe`.
- CuraEngine найден: `C:\Program Files\Ultimaker Cura 5.2.1\CuraEngine.exe`.
- `config/app.yaml` обновлен под найденные пути OpenSCAD и CuraEngine.
- Smoke-тест OpenSCAD успешен: создан STL `generated\smoke\orders\smoke-openscad\smoke-openscad.stl` и batch STL `generated\smoke\batches\smoke-batch\smoke-batch_plate.stl`.
- USB-принтер определяется как `Silicon Labs CP210x USB to UART Bridge (COM8)`.
- Для CP210x установлен официальный Silicon Labs VCP-драйвер `silabser.inf`; версия драйвера в Driver Store: `11.5.0.417`.
- Проверка serial-связи: `COM8` отвечает на `M115` на скорости `250000`, прошивка `Marlin 1.1.0-RC8`, тип `3D Printer`.
- OctoPrint установлен в `.octoprint-venv`, basedir `octoprint`, URL `http://127.0.0.1:5000`.
- OctoPrint настроен на `COM8`, `250000 baud`, профиль `_default` = `Keychain Fair Printer` (`220x220x250`, nozzle `0.4`, heated bed enabled).
- OctoPrint API key записан в `.env`, а в `config/app.yaml` включено `octoprint.enabled: true`.
- OctoPrint после рестарта переходит в `Operational`; Keychain Fair adapter `OctoPrintController.get_job()` возвращает `state: Operational`.
- Тест движения через OctoPrint выполнен: jog по X `+2 мм` и `-2 мм`, оба API-запроса приняты (`204 No Content`), без нагрева и без экструдера.
- Важно: `slicer.enabled` пока остается `false`; для автоматической печати из очереди еще нужно довести CuraEngine profile/команду slicing до реального G-code.
- Frontend формы заказа обновлен: видимая кнопка `Подтвердить заказ` вынесена вниз страницы под макет, старая кнопка внутри формы скрыта CSS.
- Создание заказа защищено от дублей: frontend блокирует повторный submit и отправляет `idempotency_key`, backend хранит его в `orders.idempotency_key` с уникальным индексом `idx_orders_idempotency_key`.
- Живой duplicate-submit тест через два POST с одним `idempotency_key` вернул один и тот же `order_id`.
- `designs/classic_plate.scad` и `designs/rounded_tag.scad` переписаны под печатаемые брелки: рельефный бортик, усиленное кольцо отверстия, адаптивный текст, крупные рельефные элементы `heart/smile/star`.
- Smoke OpenSCAD после правок успешен: `generated\smoke_new_designs_final\orders\final-classic_plate\final-classic_plate.stl` и `generated\smoke_new_designs_final\orders\final-rounded_tag\final-rounded_tag.stl`.
