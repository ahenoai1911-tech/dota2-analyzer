# Dota 2 Analyzer - Telegram Bot + WebApp

Полнофункциональный бот для анализа игроков Dota 2 с системой миссий, магазином и AI анализом.

## 🚀 Быстрый старт

### 1. Создать PostgreSQL на Railway
\\\
Railway Dashboard → + New → Database → PostgreSQL
\\\

### 2. Установить переменные окружения
\\\
DATABASE_URL = (автоматически от Railway)
BOT_TOKEN = ваш_токен_telegram_бота
WEBAPP_URL = https://your-site.com/index.html
STRATZ_TOKEN = ваш_stratz_токен (опционально)
GROQ_API_KEY = ваш_groq_токен
\\\

### 3. Деплой backend
\\\ash
git add main.py requirements.txt
git commit -m "Deploy to Railway"
git push
\\\

### 4. Деплой frontend (index.html)
Загрузить на:
- GitHub Pages (рекомендуется)
- Vercel
- Netlify

### 5. Тест
Отправить Steam ID боту → Нажать кнопку "📊 Полный анализ"

## 📂 Структура

- **main.py** - Backend (FastAPI + PostgreSQL)
- **index.html** - Frontend (WebApp)
- **requirements.txt** - Зависимости Python

## ✨ Функционал

- ✅ Поиск игроков (Stratz + OpenDota)
- ✅ Приветственный баннер (раз в день)
- ✅ Система миссий (daily/weekly/monthly)
- ✅ Магазин (бустеры, косметика)
- ✅ Профиль с уровнями и опытом
- ✅ AI анализ игры
- ✅ Premium подписка

## 💾 База данных

PostgreSQL таблицы:
- users
- missions
- user_missions
- shop_items
- user_inventory
- transactions

## 📝 Требования

- Python 3.9+
- PostgreSQL
- Telegram Bot Token
- Groq API Key (для AI)

## 🎯 Готово!

Всё настроено и готово к использованию! 🚀
