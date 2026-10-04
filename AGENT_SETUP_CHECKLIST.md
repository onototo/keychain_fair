# Магазин: проверка окружения для Docker и Telegram-бота

## Что должно быть установлено
- Windows 10/11 с включенной виртуализацией в BIOS/UEFI.
- WSL2 и Windows feature `VirtualMachinePlatform`.
- Docker Desktop с Linux containers backend.
- Docker CLI и Docker Compose plugin.
- Git.
- Python 3.13+ для локальных тестов без Docker.
- Node.js 22+ и npm для локальной проверки `bot/`.

## Быстрые проверки
```powershell
wsl --status
docker --version
docker compose version
docker info
git --version
python --version
node --version
cmd /c npm --version
```

`docker info` должен отвечать без зависания и ошибок подключения к daemon.

## Настройки проекта
- В корне проекта должен быть `.env`.
- Обязательные значения:
  - `TELEGRAM_BOT_TOKEN` - токен из BotFather.
  - `INTERNAL_API_TOKEN` - один и тот же секрет для `api` и `telegram-bot`.
  - `ADMIN_TELEGRAM_USER_IDS` - Telegram id администраторов через запятую.
- Для перевода на карту: `PAYMENT_CARD_NUMBER` и `PAYMENT_CARD_HOLDER`.
- Для кнопки карты и Apple Pay: `ONLINE_PAYMENT_URL`. Пока переменная пустая, кнопки нет.

## Проверки проекта
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest

cd bot
cmd /c npm install
cmd /c npm test
cmd /c npm run build
cd ..
```

Постоянный бот запускается на отдельном домашнем ПК, не на машине разработки. Пока тот компьютер включён, заказы идут в Telegram. VPS — более поздний перенос того же compose.

## Запуск Docker stack
```powershell
docker compose config
docker compose up --build -d api telegram-bot
docker compose ps
docker compose logs -f api telegram-bot
```

Заказы смотрятся в Telegram, веб-админки нет. API: `http://127.0.0.1:8120/api/health`.
