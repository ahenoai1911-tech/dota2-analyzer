import logging
import time
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from opendota import  get_player, get_player_wl, get_recent_matches, get_player_heroes
from analysis import full_analysis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Dota 2 Analyzer API", version="1.0.0")

# CORS — разрешаем запросы из браузера / Telegram Web App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Простой кэш: { key: {"data": ..., "ts": timestamp} }
cache: dict = {}
CACHE_TTL = 300  # 5 минут


def get_cache(key: str):
    if key in cache:
        if time.time() - cache[key]["ts"] < CACHE_TTL:
            logger.info(f"Кэш hit: {key}")
            return cache[key]["data"]
        else:
            del cache[key]
    return None


def set_cache(key: str, data):
    cache[key] = {"data": data, "ts": time.time()}


@app.get("/")
async def root():
    return {"status": "ok", "message": "Dota 2 Analyzer API"}


@app.get("/player")
async def find_player(query: str = Query(..., min_length=1)):
    """
    Поиск игрока по нику или Steam ID.
    Если query — число, считаем это player_id.
    """
    query = query.strip()
    cache_key = f"player:{query}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    # Определяем: ник или ID
    if query.isdigit():
        player_id = int(query)
    else:
        # Ищем по нику
        results = await search_player(query)
        if not results:
            raise HTTPException(status_code=404, detail="Игрок не найден")
        player_id = results[0]["account_id"]

    # Загружаем все данные параллельно
    import asyncio
    player, wl, matches = await asyncio.gather(
        get_player(player_id),
        get_player_wl(player_id),
        get_recent_matches(player_id, limit=20),
    )

    if not player:
        raise HTTPException(status_code=404, detail="Профиль не найден")

    result = full_analysis(player, wl, matches)

    set_cache(cache_key, result)
    return result


@app.get("/matches")
async def get_matches(player_id: int = Query(...)):
    """Получить последние матчи игрока"""
    cache_key = f"matches:{player_id}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    matches = await get_recent_matches(player_id, limit=20)

    if not matches:
        raise HTTPException(status_code=404, detail="Матчи не найдены")

    set_cache(cache_key, matches)
    return matches


@app.get("/heroes")
async def get_heroes(player_id: int = Query(...)):
    """Топ героев игрока"""
    cache_key = f"heroes:{player_id}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    heroes = await get_player_heroes(player_id)
    set_cache(cache_key, heroes)
    return heroes


@app.get("/search")
async def search(q: str = Query(..., min_length=1)):
    """Поиск игроков по нику (возвращает список)"""
    results = await search_player(q)
    return results[:5]  # Максимум 5 результатов

from fastapi import Request
import httpx
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text
        })


@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    print(data)  # 👈 будет видно в логах

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        await send_message(chat_id, f"echo: {text}")

    return {"ok": True}
