# Keychain Fair: проверка окружения для Docker + Telegram bot

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
  - `ADMIN_PIN` - PIN админки.
- Для Docker можно оставить:
  - `SLICER_ENABLED=false`
  - `OCTOPRINT_ENABLED=false`
- Если OctoPrint работает на хосте Windows, использовать:
  - `OCTOPRINT_BASE_URL=http://host.docker.internal:5000`

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

## Запуск Docker stack
```powershell
docker compose config
docker compose up --build -d postgres api telegram-bot
docker compose ps
docker compose logs -f api telegram-bot
```

Админка: `http://127.0.0.1:8080/admin`.
