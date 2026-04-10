# 🎮 Dota 2 Analyzer - Исправленная версия

Полнофункциональное веб-приложение для анализа статистики Dota 2 с системой миссий, магазином и AI ассистентом.

## ✅ Что исправлено (2026-04-10)

### Backend (main.py):
- ✅ Исправлены баги в функции `assign_user_missions`
- ✅ Добавлены рабочие эндпоинты для миссий и магазина
- ✅ Удалены нерабочие функции
- ✅ Исправлена работа с PostgreSQL

### Frontend (index.html):
- ✅ Улучшенные анимации для карточек миссий
- ✅ Hover эффекты для магазина
- ✅ Анимированные прогресс-бары с shimmer эффектом
- ✅ Улучшенные кнопки с тенями и transitions

## 🚀 Быстрый старт

```bash
# 1. Установить зависимости
pip install fastapi uvicorn psycopg2-binary httpx

# 2. Настроить .env или экспортировать переменные
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
export BOT_TOKEN="your_telegram_bot_token"
export GROQ_API_KEY="your_groq_api_key"

# 3. Запустить сервер
python main.py
```

Сервер запустится на http://localhost:8000

## 📋 Основные функции

### 🔍 Поиск и анализ игроков
- Поиск по нику или Steam ID
- Детальная статистика (WR, KDA, GPM, XPM)
- Топ герои с винрейтом
- История последних 20 матчей
- Тренды и серии побед/поражений

### 🎯 Система миссий
- Ежедневные, недельные и месячные миссии
- Автоматическое назначение 3 daily миссий
- Прогресс трекинг
- Награды: монеты и XP

### 🛒 Магазин
- Бустеры XP и монет
- Косметические предметы (рамки, титулы)
- Специальные предметы
- Система инвентаря

### 🤖 AI Ассистент
- Анализ игры и советы
- Прогноз MMR
- Рекомендации по героям
- Поиск слабых мест

## 📊 API Endpoints

### Основные
- `GET /player?query=XXX` - поиск игрока
- `GET /search?q=XXX` - поиск по нику
- `POST /ai` - AI чат
- `POST /roast` - AI роаст

### Миссии
- `GET /missions?telegram_id=XXX` - получить миссии
- `POST /missions/claim` - забрать награду

### Магазин
- `GET /shop` - список товаров
- `POST /shop/buy` - купить товар

### Пользователь
- `GET /user/profile?telegram_id=XXX` - профиль

## 🗄️ База данных

PostgreSQL с таблицами:
- `users` - пользователи
- `missions` - шаблоны миссий
- `user_missions` - прогресс по миссиям
- `shop_items` - товары
- `user_inventory` - инвентарь
- `transactions` - история транзакций

База инициализируется автоматически при первом запуске.

## 🎨 UI Features

- Темная тема в стиле Telegram
- Плавные анимации и transitions
- Адаптивный дизайн
- Shimmer эффекты на прогресс-барах
- Hover эффекты с подъемом карточек
- Градиентные кнопки с тенями

## 📁 Файлы

- `main.py` - Backend сервер (FastAPI)
- `index.html` - Frontend приложение
- `FIXES.md` - Описание исправлений
- `CHANGELOG.md` - Полный changelog
- `*.backup` - Резервные копии

## 🔧 Требования

- Python 3.10+
- PostgreSQL 12+
- Telegram Bot Token
- Groq API Key (для AI)
- Stratz Token (опционально)

## 📝 Переменные окружения

```bash
DATABASE_URL=postgresql://user:pass@host:5432/dbname
BOT_TOKEN=your_telegram_bot_token
GROQ_API_KEY=your_groq_api_key
STRATZ_TOKEN=your_stratz_token  # опционально
WEBAPP_URL=https://your-webapp-url.com
PORT=8000  # опционально
```

## 🎯 Типы миссий

**Daily:** 5 типов (50-100💰, 100-150 XP)
**Weekly:** 5 типов (180-400💰, 300-600 XP)
**Monthly:** 5 типов (800-1500💰, 1500-3000 XP)

## 🛒 Товары

**Бустеры:** 500-1500💰
**Косметика:** 200-1000💰
**Специальное:** 250-400💰

## 🚀 Деплой

Готово к деплою на:
- Railway
- Heroku
- Render
- DigitalOcean
- AWS/GCP/Azure

## 📞 Поддержка

Все основные функции протестированы и работают:
- ✅ Поиск игроков
- ✅ Статистика
- ✅ AI ассистент
- ✅ Миссии
- ✅ Магазин
- ✅ Telegram бот

## 📄 Лицензия

MIT

---

**Версия:** 2.1.0 (исправленная)
**Дата:** 2026-04-10
**Статус:** ✅ Готово к использованию
