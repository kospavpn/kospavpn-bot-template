# Kospavpn Telegram Bot (шаблон)

Telegram-бот для продажи подписок VPN. Покупка подписок, оплата (Platega / Coinso), пробный период, промокоды, реферальная система, админ-панель, интеграция с 3x-ui панелью.

> ⚠️ **Это шаблон проекта.** Все API-ключи, пароли и реальные адреса заменены на плейсхолдеры. Для запуска заполните свой `.env` (скопируйте из `.env.example`).

## Возможности

- 🛒 Продажа VPN-подписок (тарифы: 1/3/6/12 месяцев)
- 💳 Оплата через Platega (СБП) и Coinso (криптовалюта)
- 🔑 Пробный период 24 часа (с проверкой подписки на канал)
- 🎁 Реферальная система (бонусные дни за приглашённых)
- 🏷 Промокоды (скидка % или фиксированная)
- ⚙️ Админ-панель (управление тарифами, промокодами, пользователями)
- ❄️ Заморозка/разморозка подписки
- 🔔 Напоминания об окончании подписки за 3 дня
- 🔗 Автоматическая генерация клиентов в 3x-ui панели

## Архитектура

```
├── app.py                    # Точка входа, фоновые задачи, вебхук
├── services/
│   ├── xui.py                # Интеграция с 3x-ui панелью (VPN)
│   ├── platega.py            # Платёжный шлюз Platega
│   ├── coinso.py             # Платёжный шлюз Coinso
│   └── webhook.py            # Обработчик вебхуков
├── handlers/
│   ├── user_private.py       # Хэндлеры пользователей
│   ├── user_group.py         # Хэндлеры группы
│   └── admin_private.py      # Админ-панель
├── database/                 # SQLAlchemy модели и ORM
├── filters/                  # Фильтры (админ, чаты)
├── common/                   # Общие тексты и данные
├── kbds/                     # Клавиатуры
├── middlewares/              # Middleware для БД
└── utils/                    # Вспомогательные утилиты
```

## Установка и запуск

```bash
# 1. Клонировать и создать venv
git clone <repo-url>
cd <project>
python3 -m venv venv
source venv/bin/activate

# 2. Зависимости
pip install -r requirements.txt

# 3. Настройки
cp .env.example .env
# заполните .env своими значениями

# 4. Запуск
python app.py
```

## Переменные окружения

Все настройки в `.env` (см. `.env.example`):

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота (@BotFather) |
| `ADMIN_ID` | Telegram ID владельца бота |
| `CHANNEL_USERNAME` | Юзернейм канала для проверки подписки |
| `XUI_HOST` | Адрес 3x-ui панели |
| `XUI_TOKEN` | Bearer-токен 3x-ui панели |
| `SUBSCRIPTION_HOST` | Хост ссылок подписки |
| `SUBSCRIPTION_PATH` | Путь ссылок подписки |
| `PLATEGA_MERCHANT_ID` | Merchant ID Platega |
| `PLATEGA_SECRET_KEY` | Secret Key Platega |
| `COINSO_SECRET_KEY` | Secret Key Coinso |
| `COINSO_PROJECT_ID` | Project ID Coinso |
| `SSL_CERT_PATH` | Путь к SSL сертификату |
| `SSL_KEY_PATH` | Путь к приватному ключу SSL |
| `WEBHOOK_PORT` | Порт вебхука |

## Технологии

- Python 3.12+
- aiogram 3 (Telegram Bot API)
- SQLAlchemy 2 (async, SQLite/PostgreSQL)
- aiohttp (вебхуки, HTTP API)
- 3x-ui панель (Xray/VLESS)

## Примечание

Этот проект распространяется как пример реализации. Продажа VPN-услуг может требовать лицензий и соблюдения законодательства в вашей юрисдикции.