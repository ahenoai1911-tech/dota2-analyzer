# Dota 2 Analyzer Bot v2.0

## Почему был "Failed to fetch"
Фронтенд (Vercel) не мог достучаться до бэкенда потому что:
1. URL бэкенда не был прописан в фронтенде
2. Или вебхук не был зарегистрирован в Telegram

---

## Быстрый старт

### 1. Переменные Railway (Settings → Variables)
```
BOT_TOKEN      = токен от @BotFather
WEBAPP_URL     = https://твой-фронтенд.vercel.app
STRATZ_TOKEN   = токен с https://stratz.com/api
```

### 2. Зарегистрировать webhook Telegram
Открой в браузере (один раз):
```
https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<RAILWAY_URL>/webhook
```

### 3. Проверить что API работает
```
https://<RAILWAY_URL>/player?query=Miracle-
https://<RAILWAY_URL>/search?q=Miracle
```

### 4. Фронтенд — исправить URL запроса
В своём фронте замени BASE_URL:
```js
const BASE_URL = "https://твой-проект.railway.app";

// Поиск игрока:
fetch(`${BASE_URL}/player?query=${encodeURIComponent(input)}`)
```

---

## Архитектура данных

```
/player?query=...
  ↓
  Stratz GraphQL (если STRATZ_TOKEN задан)
  ↓ (fallback)
  OpenDota REST API
  ↓
  Единый JSON ответ
```

## Ответ /player содержит:
- `profile` — имя, аватар, ранг
- `stats` — wins/losses/winrate
- `top_heroes` — топ-10 героев с KDA, GPM, WR
- `recent_matches` — последние 20 матчей
- `trend` — тренды за 5 и 20 матчей, серия W/L
- `source` — "stratz" или "opendota"

## Команды бота:
- `/start` — приветствие + кнопка Web App
- `/help` — инструкция
- Любой текст — поиск игрока по нику/ID
