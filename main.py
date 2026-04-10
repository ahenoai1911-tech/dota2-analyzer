import logging
import time
import asyncio
import os
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Dota 2 Analyzer API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# в”Ђв”Ђ ENV в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
WEBAPP_URL       = os.getenv("WEBAPP_URL", "")
STRATZ_TOKEN     = os.getenv("STRATZ_TOKEN", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
DATABASE_URL     = os.getenv("DATABASE_URL", "")

OPENDOTA_BASE = "https://api.opendota.com/api"
STRATZ_GQL    = "https://api.stratz.com/graphql"
STRATZ_BASE   = "https://api.stratz.com/api/v1"

# в”Ђв”Ђ CACHE в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
cache: dict = {}
CACHE_TTL = 300

def get_cache(key: str):
    if key in cache:
        if time.time() - cache[key]["ts"] < CACHE_TTL:
            return cache[key]["data"]
        del cache[key]
    return None

def set_cache(key: str, data):
    cache[key] = {"data": data, "ts": time.time()}

def steam64_to_account_id(steam64: int) -> int:
    return steam64 - 76561197960265728

# в”Ђв”Ђ DATABASE в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def get_db_connection():
    """Get PostgreSQL connection"""
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            steam_id BIGINT,
            username TEXT,
            coins INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            premium_until TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Missions table
    c.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id SERIAL PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            requirement TEXT NOT NULL,
            target_value INTEGER NOT NULL,
            reward_coins INTEGER NOT NULL,
            reward_xp INTEGER NOT NULL,
            icon TEXT DEFAULT 'рџЋЇ'
        )
    """)
    
    # User missions progress
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_missions (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            mission_id INTEGER NOT NULL,
            progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
            FOREIGN KEY (mission_id) REFERENCES missions(id)
        )
    """)
    
    # Shop items
    c.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            type TEXT NOT NULL,
            price INTEGER NOT NULL,
            icon TEXT DEFAULT 'рџЋЃ',
            data TEXT
        )
    """)
    
    # User inventory
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_inventory (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
            FOREIGN KEY (item_id) REFERENCES shop_items(id)
        )
    """)
    
    # Transactions log
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )
    """)
    
        # Insert default missions if empty
    try:
        c.execute("SELECT COUNT(*) FROM missions")
        count = c.fetchone()
        mission_count = count[0] if count else 0
    except Exception:
        mission_count = 0  # таблица не существует или другая ошибка

    if mission_count == 0:
        print("Добавляем стандартные миссии...")
        default_missions = [
            # Daily missions
            ("daily", "Первая кровь", "Получи First Blood в любом матче", "first_blood", 1, 50, 100, "🩸"),
            ("daily", "Победная серия", "Выиграй 3 игры подряд", "win_streak", 3, 100, 150, "🔥"),
            ("daily", "Мастер фарма", "Набери 600+ GPM в матче", "gpm", 600, 75, 120, "💰"),
            ("daily", "Безупречная игра", "Сыграй матч с KDA 10+", "kda", 10, 80, 130, "⭐"),
            ("daily", "Командный игрок", "Сделай 20+ ассистов в матче", "assists", 20, 60, 100, "🤝"),
           
            # Weekly missions
            ("weekly", "Марафонец", "Сыграй 20 матчей за неделю", "matches", 20, 300, 500, "🏃"),
            ("weekly", "Универсал", "Сыграй на 10 разных героях", "unique_heroes", 10, 250, 400, "🎭"),
            ("weekly", "Доминатор", "Выиграй 15 игр за неделю", "wins", 15, 400, 600, "👑"),
            ("weekly", "Разрушитель", "Нанеси 1M урона по строениям", "tower_damage", 1000000, 200, 350, "🏰"),
            ("weekly", "Целитель", "Вылечи 50K HP союзникам", "healing", 50000, 180, 300, "💚"),
           
            # Monthly missions
            ("monthly", "Легенда", "Выиграй 50 игр за месяц", "wins", 50, 1000, 2000, "🏆"),
            ("monthly", "Мастер героя", "Сыграй 30 игр на одном герое", "hero_matches", 30, 800, 1500, "🦸"),
            ("monthly", "Несокрушимый", "Достигни винрейта 60%+", "winrate", 60, 1200, 2500, "💎"),
            ("monthly", "Профессионал", "Набери средний KDA 4.0+", "avg_kda", 4, 900, 1800, "🎯"),
            ("monthly", "Богатей", "Накопи 10000 монет", "total_coins", 10000, 1500, 3000, "💸"),
        ]
        c.executemany("""
            INSERT INTO missions (type, title, description, requirement, target_value, reward_coins, reward_xp, icon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, default_missions)
        conn.commit()

    # Insert shop items if empty
    try:
        c.execute("SELECT COUNT(*) FROM shop_items")
        count = c.fetchone()
        shop_count = count[0] if count else 0
    except Exception:
        shop_count = 0

    if shop_count == 0:
        print("Добавляем товары в магазин...")
        shop_items = [
            # Boosters
            ("XP Booster x2", "Удваивает получаемый опыт на 24 часа", "booster_xp", 500, "⚡", "duration:24,multiplier:2"),
            ("XP Booster x3", "Утраивает получаемый опыт на 12 часов", "booster_xp", 800, "⚡⚡", "duration:12,multiplier:3"),
            ("Coin Booster x2", "Удваивает награды монет на 24 часа", "booster_coins", 600, "💰", "duration:24,multiplier:2"),
            ("Mega Booster", "x2 XP и монеты на 48 часов", "booster_mega", 1500, "🚀", "duration:48,xp:2,coins:2"),
           
            # Cosmetics
            ("Золотая рамка", "Золотая рамка для профиля", "cosmetic_frame", 300, "🖼️", "color:gold"),
            ("Алмазная рамка", "Алмазная рамка для профиля", "cosmetic_frame", 800, "💎", "color:diamond"),
            ("Титул: Новичок", "Отображается в профиле", "cosmetic_title", 200, "🏷️", "title:Новичок"),
            ("Титул: Ветеран", "Отображается в профиле", "cosmetic_title", 500, "🎖️", "title:Ветеран"),
            ("Титул: Легенда", "Отображается в профиле", "cosmetic_title", 1000, "👑", "title:Легенда"),
           
            # Special
            ("Дополнительная миссия", "Открывает 1 дополнительную миссию на день", "special_mission", 400, "📋", "missions:1"),
            ("Сброс миссий", "Обновляет все текущие миссии", "special_refresh", 300, "🔄", "refresh:all"),
            ("AI Запросы x10", "10 дополнительных AI запросов", "special_ai", 250, "🤖", "queries:10"),
        ]
        c.executemany("""
            INSERT INTO shop_items (name, description, type, price, icon, data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, shop_items)
        conn.commit()

    conn.close()

# Initialize database on startup
init_db()

# ── USER DB HELPERS ───────────────────────────────────────────────────────────
def upsert_user(telegram_id: int, username: str = ""):
    conn = get_db_connection(); c = conn.cursor()
    c.execute("""
        INSERT INTO users (telegram_id, username) VALUES (%s, %s)
        ON CONFLICT (telegram_id) DO UPDATE
        SET username = EXCLUDED.username, last_seen = CURRENT_TIMESTAMP
    """, (telegram_id, username))
    conn.commit(); conn.close()

def get_user(telegram_id: int) -> dict | None:
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def link_steam(telegram_id: int, steam_id: int, username: str = ""):
    conn = get_db_connection(); c = conn.cursor()
    c.execute("""
        INSERT INTO users (telegram_id, steam_id, username) VALUES (%s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET steam_id = %s, username = %s
    """, (telegram_id, steam_id, username, steam_id, username))
    conn.commit(); conn.close()

def unlink_steam(telegram_id: int):
    conn = get_db_connection(); c = conn.cursor()
    c.execute("UPDATE users SET steam_id = NULL WHERE telegram_id = %s", (telegram_id,))
    conn.commit(); conn.close()

def assign_user_missions(telegram_id: int):
    """Назначить миссии если ещё не назначены сегодня"""
    conn = get_db_connection(); c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM user_missions
        WHERE telegram_id = %s AND DATE(assigned_at) = CURRENT_DATE AND claimed = 0
    """, (telegram_id,))
    if c.fetchone()[0] > 0:
        conn.close(); return
    c.execute("SELECT id FROM missions WHERE type = 'daily' ORDER BY RANDOM() LIMIT 3")
    mission_ids = [r[0] for r in c.fetchall()]
    for mid in mission_ids:
        c.execute("INSERT INTO user_missions (telegram_id, mission_id) VALUES (%s, %s)", (telegram_id, mid))
    conn.commit(); conn.close()

def get_user_missions(telegram_id: int) -> list:
    conn = get_db_connection(); c = conn.cursor()
    c.execute("""
        SELECT um.id, m.type, m.title, m.description, m.icon,
               m.target_value, m.reward_coins, m.reward_xp,
               um.progress, um.completed, um.claimed
        FROM user_missions um JOIN missions m ON um.mission_id = m.id
        WHERE um.telegram_id = %s AND um.claimed = 0
        ORDER BY um.completed, m.type
    """, (telegram_id,))
    rows = c.fetchall(); conn.close()
    return [dict(r) for r in rows]

def get_shop_items() -> list:
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT id, name, description, type, price, icon FROM shop_items ORDER BY type, price")
    rows = c.fetchall(); conn.close()
    return [dict(r) for r in rows]

def buy_item(telegram_id: int, item_id: int) -> dict:
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT name, price FROM shop_items WHERE id = %s", (item_id,))
    item = c.fetchone()
    if not item:
        conn.close(); raise Exception("Предмет не найден")
    c.execute("SELECT coins FROM users WHERE telegram_id = %s", (telegram_id,))
    user = c.fetchone()
    if not user:
        conn.close(); raise Exception("Пользователь не найден")
    if user["coins"] < item["price"]:
        conn.close(); raise Exception(f"Недостаточно монет. Нужно: {item['price']}, есть: {user['coins']}")
    new_coins = user["coins"] - item["price"]
    c.execute("UPDATE users SET coins = %s WHERE telegram_id = %s", (new_coins, telegram_id))
    c.execute("INSERT INTO user_inventory (telegram_id, item_id) VALUES (%s, %s)", (telegram_id, item_id))
    c.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (%s, 'spend', %s, %s)",
              (telegram_id, item["price"], f"Купил: {item['name']}"))
    conn.commit(); conn.close()
    return {"item_name": item["name"], "coins_left": new_coins}

async def _load_player(query: str) -> dict:
    """Internal: load player data with cache"""
    cached = get_cache(f"player:{query}")
    if cached:
        return cached
    if query.isdigit():
        q_int = int(query)
        account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
    else:
        results = await search_combined(query)
        if not results:
            raise Exception("Игрок не найден")
        account_id = results[0]["account_id"]
    result = None
    if STRATZ_TOKEN:
        sd = await stratz_player(account_id)
        if sd:
            result = build_from_stratz(sd, account_id)
    if not result:
        player, wl, matches, heroes = await asyncio.gather(
            od_player(account_id), od_wl(account_id),
            od_matches(account_id), od_heroes(account_id),
        )
        if not player:
            raise Exception("Профиль не найден или приватный")
        result = build_from_opendota(player, wl, matches, heroes, account_id)
    set_cache(f"player:{query}", result)
    return result

# в”Ђв”Ђ STRATZ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def stratz_headers() -> dict:
    return {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "User-Agent": "Dota2AnalyzerBot/2.1",
    }

async def stratz_player(account_id: int) -> dict | None:
    query = """
    query Player($steamAccountId: Long!) {
      player(steamAccountId: $steamAccountId) {
        steamAccount {
          id
          name
          avatar
          profileUri
          isAnonymous
          seasonRank
        }
        winCount
        matchCount
        heroesPerformance(request: { take: 10 }) {
          hero { displayName shortName }
          winCount
          matchCount
          avgKills
          avgDeaths
          avgAssists
          avgGoldPerMinute
          avgExperiencePerMinute
          avgNetworth
          avgImp
        }
        matches(request: { take: 20, orderBy: END_DATE_TIME }) {
          id
          didRadiantWin
          durationSeconds
          endDateTime
          gameMode
          players(steamAccountId: $steamAccountId) {
            isRadiant
            kills
            deaths
            assists
            goldPerMinute
            experiencePerMinute
            networth
            heroDamage
            towerDamage
            heroHealingDone
            numLastHits
            numDenies
            hero { displayName shortName }
          }
        }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                STRATZ_GQL,
                json={"query": query, "variables": {"steamAccountId": account_id}},
                headers=stratz_headers(),
            )
            data = r.json()
            if "errors" in data:
                logger.warning(f"Stratz errors: {data['errors']}")
                return None
            return data.get("data", {}).get("player")
    except Exception as e:
        logger.error(f"Stratz player error: {e}")
        return None

async def stratz_search(nickname: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{STRATZ_BASE}/search",
                params={"query": nickname},
                headers=stratz_headers(),
            )
            data = r.json()
            players = data.get("players", [])
            return [
                {
                    "account_id": p["steamAccount"]["id"],
                    "personaname": p["steamAccount"].get("name", "Unknown"),
                    "avatarfull": p["steamAccount"].get("avatar", ""),
                }
                for p in players
            ]
    except Exception as e:
        logger.error(f"Stratz search error: {e}")
        return []

# в”Ђв”Ђ OPENDOTA в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
async def od_get(path: str, params: dict = None):
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{OPENDOTA_BASE}{path}", params=params)
            if r.status_code == 200:
                return r.json()
            return None
    except Exception as e:
        logger.error(f"OpenDota error {path}: {e}")
        return None

async def od_player(account_id: int):   return await od_get(f"/players/{account_id}")
async def od_wl(account_id: int):       return await od_get(f"/players/{account_id}/wl")
async def od_matches(account_id: int):  return await od_get(f"/players/{account_id}/recentMatches", {"limit": 20})
async def od_heroes(account_id: int):   return await od_get(f"/players/{account_id}/heroes", {"limit": 10})
async def od_search(nickname: str):     return await od_get("/search", {"q": nickname})

# в”Ђв”Ђ ANALYSIS в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def calc_kda(kills, deaths, assists):
    return round((kills + assists) / max(deaths, 1), 2)

def rank_tier_to_name(rank_tier) -> str:
    if rank_tier is None:
        return "Uncalibrated"
    tiers = {1:"Herald",2:"Guardian",3:"Crusader",4:"Archon",5:"Legend",6:"Ancient",7:"Divine",8:"Immortal"}
    tier  = int(str(rank_tier)[0]) if rank_tier else 0
    star  = int(str(rank_tier)[-1]) if rank_tier and len(str(rank_tier)) > 1 else 0
    name  = tiers.get(tier, "Unknown")
    return f"{name} {'в…' * star}" if star else name

def compute_streak(matches: list) -> dict:
    if not matches: return {"type": "none", "count": 0}
    first_win = matches[0].get("win", False)
    count = 0
    for m in matches:
        if m.get("win") == first_win: count += 1
        else: break
    return {"type": "win" if first_win else "loss", "count": count}

def compute_trend(matches: list) -> dict:
    if not matches: return {}
    last5  = matches[:5]
    last20 = matches[:20]
    def avg(lst, key):
        vals = [m.get(key, 0) or 0 for m in lst]
        return round(sum(vals) / len(vals), 2) if vals else 0
    def wr(lst):
        if not lst: return 0
        return round(sum(1 for m in lst if m.get("win")) / len(lst) * 100, 1)
    return {
        "last5_winrate":  wr(last5),
        "last20_winrate": wr(last20),
        "last5_avg_kda":  avg(last5, "kda"),
        "last20_avg_kda": avg(last20, "kda"),
        "last5_avg_gpm":  avg(last5, "gpm"),
        "last20_avg_gpm": avg(last20, "gpm"),
        "streak": compute_streak(matches),
    }

def build_from_stratz(player_data: dict, account_id: int) -> dict:
    sa    = player_data.get("steamAccount", {})
    win   = player_data.get("winCount", 0)
    total = player_data.get("matchCount", 1) or 1
    loss  = total - win
    winrate = round(win / total * 100, 1)
    rank_tier = sa.get("seasonRank")

    heroes_raw = player_data.get("heroesPerformance", []) or []
    heroes = []
    for h in heroes_raw:
        hero = h.get("hero", {}) or {}
        hm = h.get("matchCount", 0) or 1
        hw = h.get("winCount", 0)
        heroes.append({
            "hero_name":  hero.get("displayName", "Unknown"),
            "hero_short": hero.get("shortName", ""),
            "matches":    hm,
            "wins":       hw,
            "winrate":    round(hw / hm * 100, 1),
            "kda":        calc_kda(h.get("avgKills",0), h.get("avgDeaths",1), h.get("avgAssists",0)),
            "avg_gpm":    round(h.get("avgGoldPerMinute", 0)),
            "avg_xpm":    round(h.get("avgExperiencePerMinute", 0)),
            "avg_networth": round(h.get("avgNetworth", 0)),
        })

    matches_raw = player_data.get("matches", []) or []
    matches = []
    for m in matches_raw:
        ps = (m.get("players") or [{}])[0]
        hero_info  = ps.get("hero") or {}
        is_radiant = ps.get("isRadiant", True)
        radiant_win = m.get("didRadiantWin", False)
        win_flag = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        dur = m.get("durationSeconds", 0)
        matches.append({
            "match_id":     m.get("id"),
            "hero":         hero_info.get("displayName", "Unknown"),
            "hero_short":   hero_info.get("shortName", ""),
            "win":          win_flag,
            "kills":        ps.get("kills", 0),
            "deaths":       ps.get("deaths", 0),
            "assists":      ps.get("assists", 0),
            "kda":          calc_kda(ps.get("kills",0), ps.get("deaths",0), ps.get("assists",0)),
            "gpm":          ps.get("goldPerMinute", 0),
            "xpm":          ps.get("experiencePerMinute", 0),
            "networth":     ps.get("networth", 0),
            "hero_damage":  ps.get("heroDamage", 0),
            "tower_damage": ps.get("towerDamage", 0),
            "healing":      ps.get("heroHealingDone", 0),
            "last_hits":    ps.get("numLastHits", 0),
            "denies":       ps.get("numDenies", 0),
            "duration_min": dur // 60,
            "duration_sec": dur % 60,
            "end_time":     m.get("endDateTime"),
        })

    return {
        "source":     "stratz",
        "account_id": account_id,
        "profile": {
            "name":         sa.get("name", "Unknown"),
            "avatar":       sa.get("avatar", ""),
            "profile_url":  sa.get("profileUri", ""),
            "rank":         rank_tier_to_name(rank_tier),
            "rank_tier":    rank_tier,
            "is_anonymous": sa.get("isAnonymous", False),
        },
        "stats": {
            "wins":          win,
            "losses":        loss,
            "total_matches": total,
            "winrate":       winrate,
        },
        "top_heroes":     heroes,
        "recent_matches": matches,
        "trend":          compute_trend(matches),
    }

def build_from_opendota(player: dict, wl: dict, matches: list, heroes: list, account_id: int) -> dict:
    profile_data = player.get("profile", {})
    mmr       = player.get("mmr_estimate", {}).get("estimate")
    rank_tier = player.get("rank_tier")
    win   = (wl or {}).get("win", 0)
    loss  = (wl or {}).get("lose", 0)
    total = win + loss or 1
    winrate = round(win / total * 100, 1)

    heroes_out = []
    for h in (heroes or [])[:10]:
        hm = h.get("games", 0) or 1
        hw = h.get("win", 0)
        heroes_out.append({
            "hero_id":  h.get("hero_id"),
            "hero_name": "",
            "matches":  hm,
            "wins":     hw,
            "winrate":  round(hw / hm * 100, 1),
            "kda":      0,
        })

    matches_out = []
    for m in (matches or [])[:20]:
        pslot = m.get("player_slot", 0)
        is_radiant = pslot < 128
        radiant_win = m.get("radiant_win", False)
        win_flag = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        dur = m.get("duration", 0)
        matches_out.append({
            "match_id":     m.get("match_id"),
            "hero_id":      m.get("hero_id"),
            "hero":         "",
            "hero_short":   "",
            "win":          win_flag,
            "kills":        m.get("kills", 0),
            "deaths":       m.get("deaths", 0),
            "assists":      m.get("assists", 0),
            "kda":          calc_kda(m.get("kills",0), m.get("deaths",0), m.get("assists",0)),
            "gpm":          m.get("gold_per_min", 0),
            "xpm":          m.get("xp_per_min", 0),
            "networth":     0,
            "hero_damage":  m.get("hero_damage", 0),
            "tower_damage": m.get("tower_damage", 0),
            "healing":      m.get("hero_healing", 0),
            "last_hits":    m.get("last_hits", 0),
            "denies":       m.get("denies", 0),
            "duration_min": dur // 60,
            "duration_sec": dur % 60,
            "end_time":     m.get("start_time"),
        })

    return {
        "source":     "opendota",
        "account_id": account_id,
        "profile": {
            "name":          profile_data.get("personaname", "Unknown"),
            "avatar":        profile_data.get("avatarfull", ""),
            "profile_url":   profile_data.get("profileurl", ""),
            "rank":          rank_tier_to_name(rank_tier),
            "rank_tier":     rank_tier,
            "mmr_estimate":  mmr,
            "is_anonymous":  False,
        },
        "stats": {
            "wins":          win,
            "losses":        loss,
            "total_matches": total,
            "winrate":       winrate,
        },
        "top_heroes":     heroes_out,
        "recent_matches": matches_out,
        "trend":          compute_trend(matches_out),
    }

# в”Ђв”Ђ SEARCH в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
async def search_combined(query: str) -> list:
    results = []
    if STRATZ_TOKEN:
        results = await stratz_search(query)
    if not results:
        od = await od_search(query)
        if od:
            results = [
                {
                    "account_id": p.get("account_id"),
                    "personaname": p.get("personaname", "Unknown"),
                    "avatarfull":  p.get("avatarfull", ""),
                }
                for p in od
            ]
    return results

# в”Ђв”Ђ ENDPOINTS в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
@app.get("/")
async def root():
    return {"status": "ok", "message": "Dota 2 Analyzer API v2.1"}

@app.get("/test-ai")
async def test_ai():
    return {
        "groq_key_set": bool(GROQ_API_KEY),
        "groq_key_length": len(GROQ_API_KEY) if GROQ_API_KEY else 0,
    }

@app.get("/player")
async def find_player(query: str = Query(..., min_length=1)):
    query = query.strip()
    cache_key = f"player:{query}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    if query.isdigit():
        q_int = int(query)
        account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
    else:
        results = await search_combined(query)
        if not results:
            raise HTTPException(status_code=404, detail="Player not found")
        account_id = results[0]["account_id"]

    result = None
    if STRATZ_TOKEN:
        stratz_data = await stratz_player(account_id)
        if stratz_data:
            result = build_from_stratz(stratz_data, account_id)

    if not result:
        player, wl, matches, heroes = await asyncio.gather(
            od_player(account_id),
            od_wl(account_id),
            od_matches(account_id),
            od_heroes(account_id),
        )
        if not player:
            raise HTTPException(status_code=404, detail="Profile not found")
        result = build_from_opendota(player, wl, matches, heroes, account_id)

    # Check if profile is private (no matches and no stats)
    if (not result.get("recent_matches") or len(result["recent_matches"]) == 0) and result["stats"]["total_matches"] <= 1:
        raise HTTPException(status_code=403, detail="Private profile or no match data available")

    set_cache(cache_key, result)
    return result

@app.get("/search")
async def search(q: str = Query(..., min_length=1)):
    results = await search_combined(q)
    return results[:5]

@app.get("/matches")
async def get_matches(player_id: int = Query(...)):
    cache_key = f"matches:{player_id}"
    cached = get_cache(cache_key)
    if cached:
        return cached
    matches = await od_matches(player_id)
    if not matches:
        raise HTTPException(status_code=404, detail="Matches not found")
    set_cache(cache_key, matches)
    return matches

@app.get("/heroes")
async def get_heroes(player_id: int = Query(...)):
    cache_key = f"heroes:{player_id}"
    cached = get_cache(cache_key)
    if cached:
        return cached
    heroes = await od_heroes(player_id)
    set_cache(cache_key, heroes)
    return heroes

# в”Ђв”Ђ AI ENDPOINT в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
class AIRequest(BaseModel):
    message: str
    player_context: str = ""
    history: list = []

class RoastRequest(BaseModel):
    player_context: str
    mode: str = "toxic"  # toxic, friendly, coach, brutal

@app.post("/ai")
async def ai_chat(req: AIRequest):
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY is not set!")
        raise HTTPException(status_code=503, detail="AI not configured. Set GROQ_API_KEY in Railway.")
    
    logger.info(f"GROQ_API_KEY is set: {GROQ_API_KEY[:10]}...")

    system = """You are an expert Dota 2 coach and analyst.
Analyze player statistics and give specific, actionable advice.
Be concise but insightful. Use bullet points where helpful.
Focus on concrete improvements, not generic advice.
Respond in the same language the user writes in (Russian or English).
Keep responses under 300 words."""

    if req.history:
        messages = req.history[-6:] + [{"role": "user", "content": req.message}]
    else:
        content = f"Player data:\n{req.player_context}\n\n---\n{req.message}" if req.player_context else req.message
        messages = [{"role": "user", "content": content}]

    try:
        logger.info(f"AI request: message={req.message[:50]}... history_len={len(req.history)}")
        
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "system", "content": system}] + messages,
                    "max_tokens": 1000,
                    "temperature": 0.7,
                }
            )
        
        logger.info(f"Groq response status: {r.status_code}")
        
        if r.status_code != 200:
            error_text = r.text
            logger.error(f"Groq error: {r.status_code} {error_text}")
            raise HTTPException(status_code=502, detail=f"AI request failed: {error_text[:100]}")

        data = r.json()
        text = data["choices"][0]["message"]["content"]
        logger.info(f"AI response length: {len(text)}")
        return {"reply": text}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/roast")
async def roast_player(req: RoastRequest):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="AI not configured")
    
    mode_prompts = {
        "toxic": """You are a BRUTAL, SAVAGE Dota 2 roaster. Your job is to DESTROY this player with dark humor.
Rules:
- Be RUTHLESSLY funny but not offensive to real people
- Roast their stats HARD (deaths, GPM, KDA, winrate)
- Use dark humor, sarcasm, and exaggeration
- Format: Short punchy lines with emojis
- Keep it under 200 words
- Write in Russian
- End with a devastating verdict

Example style:
рџ’Ђ РўС‹ СѓРјРµСЂ 12 СЂР°Р· Р·Р° РёРіСЂСѓ. Р­С‚Рѕ РЅРµ KDA вЂ” СЌС‚Рѕ РЅРѕРјРµСЂ С‚РµР»РµС„РѕРЅР°.
рџ¤Ў GPM 320? Р”Р°Р¶Рµ РєСЂРёРїС‹ С„Р°СЂРјСЏС‚ Р±С‹СЃС‚СЂРµРµ.
рџ‚ Р’РµСЂРґРёРєС‚: РўС‹ РЅРµ РїСЂРѕРёРіСЂР°Р» вЂ” С‚С‹ РґР°Р» РІСЂР°РіР°Рј С€Р°РЅСЃ РїРѕРІРµСЂРёС‚СЊ РІ СЃРµР±СЏ.""",

        "friendly": """You are a friendly Dota 2 comedian. Roast the player gently with humor.
- Be funny but supportive
- Point out funny stats
- Keep it light and fun
- Format with emojis
- Under 150 words
- Write in Russian""",

        "coach": """You are a Dota 2 coach who roasts with constructive feedback.
- Point out mistakes with humor
- Give actual advice
- Be motivating but honest
- Format with emojis
- Under 200 words
- Write in Russian""",

        "brutal": """You are the MOST SAVAGE Dota 2 roaster on the planet. MAXIMUM DESTRUCTION.
- OBLITERATE their stats
- Use the darkest humor possible (but stay appropriate)
- Every line should HURT
- Format with skull emojis рџ’Ђ
- Under 250 words
- Write in Russian
- Make them question their life choices"""
    }

    system = mode_prompts.get(req.mode, mode_prompts["toxic"])
    prompt = f"Roast this Dota 2 player based on their stats:\n\n{req.player_context}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 800,
                    "temperature": 0.9,
                }
            )
        
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail="AI request failed")

        data = r.json()
        text = data["choices"][0]["message"]["content"]
        return {"roast": text}

    except Exception as e:
        logger.error(f"Roast error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# в”Ђв”Ђ MISSIONS ENDPOINTS в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
class MissionRequest(BaseModel):
    account_id: int
    username: str = ""

def generate_missions(account_id: int, player_stats: dict) -> list:
    """Generate personalized missions based on player weaknesses"""
    stats = player_stats.get("stats", {})
    trend = player_stats.get("trend", {})
    recent = player_stats.get("recent_matches", [])
    
    missions = []
    
    # Analyze weaknesses
    avg_deaths = sum(m.get("deaths", 0) for m in recent[:10]) / max(len(recent[:10]), 1)
    avg_gpm = sum(m.get("gpm", 0) for m in recent[:10]) / max(len(recent[:10]), 1)
    avg_kda = trend.get("last20_avg_kda", 0)
    avg_networth = sum(m.get("networth", 0) for m in recent[:10]) / max(len(recent[:10]), 1)
    avg_healing = sum(m.get("healing", 0) for m in recent[:10]) / max(len(recent[:10]), 1)
    avg_denies = sum(m.get("denies", 0) for m in recent[:10]) / max(len(recent[:10]), 1)
    avg_tower_damage = sum(m.get("tower_damage", 0) for m in recent[:10]) / max(len(recent[:10]), 1)
    
    # Mission 1: Deaths
    if avg_deaths > 7:
        missions.append({"type": "deaths", "description": "РЈРјРµСЂРµС‚СЊ в‰¤ 5 СЂР°Р·", "target": 5, "icon": "рџ’Ђ"})
    elif avg_deaths > 5:
        missions.append({"type": "deaths", "description": "РЈРјРµСЂРµС‚СЊ в‰¤ 3 СЂР°Р·Р°", "target": 3, "icon": "рџ’Ђ"})
    else:
        missions.append({"type": "deaths", "description": "РЎС‹РіСЂР°С‚СЊ Р±РµР· СЃРјРµСЂС‚РµР№", "target": 0, "icon": "рџ›ЎпёЏ"})
    
    # Mission 2: GPM
    if avg_gpm < 400:
        missions.append({"type": "gpm", "description": "РЎРґРµР»Р°С‚СЊ GPM > 450", "target": 450, "icon": "рџ’°"})
    elif avg_gpm < 500:
        missions.append({"type": "gpm", "description": "РЎРґРµР»Р°С‚СЊ GPM > 550", "target": 550, "icon": "рџ’°"})
    else:
        missions.append({"type": "gpm", "description": "РЎРґРµР»Р°С‚СЊ GPM > 650", "target": 650, "icon": "рџ’Ћ"})
    
    # Mission 3: Networth
    if avg_networth < 8000:
        missions.append({"type": "networth", "description": "РќР°С„Р°СЂРјРёС‚СЊ NW > 10000", "target": 10000, "icon": "рџ’µ"})
    elif avg_networth < 12000:
        missions.append({"type": "networth", "description": "РќР°С„Р°СЂРјРёС‚СЊ NW > 15000", "target": 15000, "icon": "рџ’µ"})
    else:
        missions.append({"type": "networth", "description": "РќР°С„Р°СЂРјРёС‚СЊ NW > 20000", "target": 20000, "icon": "рџ’Ћ"})
    
    # Mission 4: Healing (for supports)
    if avg_healing < 2000:
        missions.append({"type": "healing", "description": "Р—Р°Р»РµС‡РёС‚СЊ > 3000 HP", "target": 3000, "icon": "рџ’љ"})
    elif avg_healing < 5000:
        missions.append({"type": "healing", "description": "Р—Р°Р»РµС‡РёС‚СЊ > 6000 HP", "target": 6000, "icon": "рџ’љ"})
    else:
        missions.append({"type": "healing", "description": "Р—Р°Р»РµС‡РёС‚СЊ > 10000 HP", "target": 10000, "icon": "рџЏҐ"})
    
    # Mission 5: Denies
    if avg_denies < 3:
        missions.append({"type": "denies", "description": "Р—Р°РґРµРЅР°РёС‚СЊ в‰Ґ 5 РєСЂРёРїРѕРІ", "target": 5, "icon": "рџљ«"})
    elif avg_denies < 7:
        missions.append({"type": "denies", "description": "Р—Р°РґРµРЅР°РёС‚СЊ в‰Ґ 10 РєСЂРёРїРѕРІ", "target": 10, "icon": "рџљ«"})
    else:
        missions.append({"type": "denies", "description": "Р—Р°РґРµРЅР°РёС‚СЊ в‰Ґ 15 РєСЂРёРїРѕРІ", "target": 15, "icon": "рџ‘‘"})
    
    # Mission 6: Tower Damage
    if avg_tower_damage < 2000:
        missions.append({"type": "tower_damage", "description": "РќР°РЅРµСЃС‚Рё СѓСЂРѕРЅ Р±Р°С€РЅСЏРј > 3000", "target": 3000, "icon": "рџЏ°"})
    elif avg_tower_damage < 5000:
        missions.append({"type": "tower_damage", "description": "РќР°РЅРµСЃС‚Рё СѓСЂРѕРЅ Р±Р°С€РЅСЏРј > 6000", "target": 6000, "icon": "рџЏ°"})
    else:
        missions.append({"type": "tower_damage", "description": "РќР°РЅРµСЃС‚Рё СѓСЂРѕРЅ Р±Р°С€РЅСЏРј > 10000", "target": 10000, "icon": "рџ”Ё"})
    
    return missions

@app.post("/missions/generate")
async def get_daily_missions(req: MissionRequest):
    """Generate daily missions for player"""
    account_id = req.account_id
    username = req.username or f"Player_{account_id}"
    
    # Create player if not exists
    player = get_player_data(account_id)
    if not player:
        create_player(account_id, username)
        player = get_player_data(account_id)
    
    # Check if missions already generated today
    today = datetime.now().strftime("%Y-%m-%d")
    if player["last_mission_date"] == today:
        # Return existing missions
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT mission_type, target_value, current_value, completed FROM missions WHERE account_id = %s AND date = %s", 
                  (account_id, today))
        rows = c.fetchall()
        conn.close()
        
        if rows:
            missions = []
            for row in rows:
                mission_type, target, current, completed = row
                missions.append({
                    "type": mission_type,
                    "target": target,
                    "current": current,
                    "completed": bool(completed)
                })
            
            return {
                "account_id": account_id,
                "date": today,
                "missions": missions,
                "player": player
            }
    
    # Fetch player stats
    try:
        player_data = await find_player(str(account_id))
    except:
        raise HTTPException(status_code=404, detail="Player not found")
    
    # Generate new missions
    missions = generate_missions(account_id, player_data)
    
    # Save to database
    conn = get_db_connection()
    c = conn.cursor()
    
    # Delete old missions
    c.execute("DELETE FROM missions WHERE account_id = %s AND date = %s", (account_id, today))
    
    # Insert new missions
    for mission in missions:
        c.execute("""INSERT INTO missions (account_id, date, mission_type, target_value, xp_reward) 
                     VALUES (?, ?, ?, ?, ?)""",
                  (account_id, today, mission["type"], mission["target"], 25))
    
    # Update last mission date
    c.execute("UPDATE players SET last_mission_date = %s WHERE account_id = %s", (today, account_id))
    
    conn.commit()
    conn.close()
    
    return {
        "account_id": account_id,
        "date": today,
        "missions": missions,
        "player": player
    }

@app.post("/missions/check")
async def check_mission_progress(req: MissionRequest):
    """Check if player completed today's missions"""
    account_id = req.account_id
    
    player = get_player_data(account_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Get today's missions
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, mission_type, target_value, completed FROM missions WHERE account_id = %s AND date = %s", 
              (account_id, today))
    missions = c.fetchall()
    conn.close()
    
    if not missions:
        raise HTTPException(status_code=404, detail="No missions for today")
    
    # Fetch latest match
    try:
        recent_matches = await od_matches(account_id)
        if not recent_matches or len(recent_matches) == 0:
            return {"message": "No recent matches found"}
        
        latest_match = recent_matches[0]
    except:
        raise HTTPException(status_code=500, detail="Failed to fetch matches")
    
    # Check each mission
    results = []
    completed_count = 0
    total_xp = 0
    
    conn = get_db_connection()
    c = conn.cursor()
    
    for mission_id, mission_type, target, is_completed in missions:
        if is_completed:
            results.append({"type": mission_type, "completed": True, "value": target})
            completed_count += 1
            continue
        
        completed = False
        current_value = 0
        
        if mission_type == "deaths":
            current_value = latest_match.get("deaths", 999)
            completed = current_value <= target
        elif mission_type == "gpm":
            current_value = latest_match.get("gold_per_min", 0)
            completed = current_value >= target
        elif mission_type == "kda":
            kills = latest_match.get("kills", 0)
            deaths = max(latest_match.get("deaths", 1), 1)
            assists = latest_match.get("assists", 0)
            current_value = round((kills + assists) / deaths, 2)
            completed = current_value >= target
        elif mission_type == "networth":
            current_value = latest_match.get("networth", 0)
            completed = current_value >= target
        elif mission_type == "healing":
            current_value = latest_match.get("hero_healing", 0)
            completed = current_value >= target
        elif mission_type == "denies":
            current_value = latest_match.get("denies", 0)
            completed = current_value >= target
        elif mission_type == "tower_damage":
            current_value = latest_match.get("tower_damage", 0)
            completed = current_value >= target
        
        # Update mission
        c.execute("UPDATE missions SET current_value = %s, completed = %s WHERE id = %s",
                  (current_value, 1 if completed else 0, mission_id))
        
        results.append({
            "type": mission_type,
            "target": target,
            "current": current_value,
            "completed": completed
        })
        
        if completed:
            completed_count += 1
            total_xp += 25
    
    conn.commit()
    
    # Update player XP and streak if all missions completed
    if completed_count == len(missions):
        new_level = update_player_xp(account_id, total_xp)
        c.execute("UPDATE players SET streak = streak + 1 WHERE account_id = %s", (account_id,))
        conn.commit()
    else:
        new_level = player["level"]
    
    conn.close()
    
    return {
        "account_id": account_id,
        "missions": results,
        "completed": completed_count,
        "total": len(missions),
        "xp_gained": total_xp,
        "new_level": new_level,
        "match_id": latest_match.get("match_id")
    }

# в”Ђв”Ђ TELEGRAM в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
async def tg_send(chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload)

def format_player_message(data: dict) -> str:
    p  = data["profile"]
    s  = data["stats"]
    t  = data.get("trend", {})
    streak = t.get("streak", {})
    src_icon = "⚡" if data.get("source") == "stratz" else "📊"
    anon = " 🔒" if p.get("is_anonymous") else ""

    streak_str = ""
    if streak.get("count", 0) >= 2:
        emoji = "🔥" if streak["type"] == "win" else "❄️"
        streak_str = f"\n{emoji} Streak: {streak['count']} {'побед' if streak['type']=='win' else 'поражений'} подряд"

    heroes_lines = ""
    for i, h in enumerate(data.get("top_heroes", [])[:5], 1):
        name = h.get("hero_name") or f"Hero#{h.get('hero_id','?')}"
        heroes_lines += f"  {i}. {name} — {h['matches']}г, WR {h['winrate']}%, KDA {h['kda']}\n"

    matches_lines = ""
    for m in data.get("recent_matches", [])[:5]:
        hero   = m.get("hero") or f"Hero#{m.get('hero_id','?')}"
        result = "✅" if m["win"] else "❌"
        matches_lines += f"  {result} {hero} — {m['kills']}/{m['deaths']}/{m['assists']} ({m['duration_min']}:{m['duration_sec']:02d})\n"

    mmr_str = f"\n📊 MMR: ~{p['mmr_estimate']}" if p.get("mmr_estimate") else ""

    return (
        f"{src_icon} <b>{p['name']}</b>{anon}\n"
        f"🏅 Ранг: {p.get('rank','?')}{mmr_str}\n"
        f"📈 Винрейт: {s['winrate']}% ({s['wins']}П / {s['losses']}П)\n"
        f"🎮 Матчей: {s['total_matches']}\n"
        f"\n📊 Тренды:\n"
        f"  Последние 5:  WR {t.get('last5_winrate','?')}%, KDA {t.get('last5_avg_kda','?')}\n"
        f"  Последние 20: WR {t.get('last20_winrate','?')}%, GPM {t.get('last20_avg_gpm','?')}\n"
        f"{streak_str}\n"
        f"\n🦸 Топ герои:\n{heroes_lines}"
        f"\n🕹 Последние матчи:\n{matches_lines}"
        f"\n🔗 <a href='https://stratz.com/players/{data['account_id']}'>Открыть на Stratz</a>"
    )

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    if "message" not in data:
        return {"ok": True}

    chat_id  = data["message"]["chat"]["id"]
    text     = data["message"].get("text", "").strip()
    username = data["message"]["from"].get("username", "")

    # Убеждаемся что пользователь есть в БД
    try:
        upsert_user(chat_id, username)
    except Exception:
        pass

    def webapp_btn(label="🎮 Открыть анализатор", url=None):
        u = url or WEBAPP_URL
        if not u:
            return None
        return {"inline_keyboard": [[{"text": label, "web_app": {"url": u}}]]}

    # ── /start ──
    if text == "/start":
        user = get_user(chat_id)
        steam_linked = user and user.get("steam_id")
        if steam_linked:
            msg = (
                f"👋 С возвращением, <b>{username or 'игрок'}</b>!\n\n"
                f"🎮 Steam привязан: <code>{user['steam_id']}</code>\n\n"
                "Используй команды:\n"
                "• /stats — моя статистика\n"
                "• /missions — миссии\n"
                "• /shop — магазин\n"
                "• /profile — профиль\n"
                "• /unlink — отвязать Steam"
            )
        else:
            msg = (
                "👋 <b>Dota 2 Analyzer</b>\n\n"
                "Отправь ник или Steam ID игрока для анализа.\n\n"
                "🔗 <b>Привязать свой аккаунт:</b>\n"
                "Отправь свой Steam ID и напиши: <code>привязать 105248644</code>\n\n"
                "Или открой Web App 👇"
            )
        await tg_send(chat_id, msg, reply_markup=webapp_btn())

    # ── привязать ──
    elif text.lower().startswith("привязать ") or text.lower().startswith("link "):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await tg_send(chat_id, "❌ Формат: <code>привязать 105248644</code> или <code>привязать 76561198065514372</code>")
            return {"ok": True}
        sid = int(parts[1])
        if sid > 76561197960265728:
            sid = steam64_to_account_id(sid)
        try:
            link_steam(chat_id, sid, username)
            await tg_send(chat_id, f"✅ Steam ID <code>{sid}</code> успешно привязан!\n\nТеперь используй /stats для своей статистики.")
        except Exception as e:
            await tg_send(chat_id, f"❌ Ошибка привязки: {e}")

    # ── /unlink ──
    elif text == "/unlink":
        try:
            unlink_steam(chat_id)
            await tg_send(chat_id, "✅ Steam аккаунт отвязан.")
        except Exception as e:
            await tg_send(chat_id, f"❌ Ошибка: {e}")

    # ── /stats ──
    elif text in ("/stats", "Моя статистика"):
        user = get_user(chat_id)
        if not user or not user.get("steam_id"):
            await tg_send(chat_id, "❌ Сначала привяжи Steam:\n<code>привязать 105248644</code>")
            return {"ok": True}
        await tg_send(chat_id, "🔍 Загружаю статистику...")
        try:
            result = await _load_player(str(user["steam_id"]))
            msg = format_player_message(result)
            await tg_send(chat_id, msg, reply_markup=webapp_btn(
                "📊 Подробный анализ",
                f"{WEBAPP_URL}?player_id={user['steam_id']}" if WEBAPP_URL else None
            ))
        except Exception as e:
            await tg_send(chat_id, f"❌ Ошибка: {e}")

    # ── /profile ──
    elif text in ("/profile", "Профиль"):
        user = get_user(chat_id)
        if not user:
            await tg_send(chat_id, "❌ Пользователь не найден. Используй /start")
            return {"ok": True}
        coins = user.get("coins", 0)
        xp    = user.get("xp", 0)
        level = user.get("level", 1)
        steam = user.get("steam_id")
        steam_str = f"<code>{steam}</code>" if steam else "не привязан"
        await tg_send(chat_id, (
            f"👤 <b>Профиль</b>\n\n"
            f"🎮 Steam: {steam_str}\n"
            f"💰 Монеты: {coins}\n"
            f"⭐ Уровень: {level} (XP: {xp})\n\n"
            f"Команды: /missions /shop /stats"
        ))

    # ── /missions ──
    elif text in ("/missions", "Миссии"):
        user = get_user(chat_id)
        if not user or not user.get("steam_id"):
            await tg_send(chat_id, "❌ Сначала привяжи Steam:\n<code>привязать 105248644</code>")
            return {"ok": True}
        try:
            assign_user_missions(chat_id)
            missions = get_user_missions(chat_id)
            if not missions:
                await tg_send(chat_id, "📭 Нет активных миссий.")
                return {"ok": True}
            lines = []
            for m in missions:
                done = "✅" if m["completed"] else "⏳"
                pct  = min(100, int(m["progress"] / max(m["target_value"], 1) * 100))
                lines.append(f"{done} {m['icon']} <b>{m['title']}</b>\n   {m['description']}\n   Прогресс: {m['progress']}/{m['target_value']} ({pct}%)\n   Награда: 💰{m['reward_coins']} ⭐{m['reward_xp']}")
            await tg_send(chat_id, "🎯 <b>Твои миссии</b>\n\n" + "\n\n".join(lines))
        except Exception as e:
            await tg_send(chat_id, f"❌ Ошибка: {e}")

    # ── /shop ──
    elif text in ("/shop", "Магазин"):
        user = get_user(chat_id)
        coins = user.get("coins", 0) if user else 0
        try:
            items = get_shop_items()
            if not items:
                await tg_send(chat_id, "🛒 Магазин пуст.")
                return {"ok": True }
            lines = [f"🛒 <b>Магазин</b> | Баланс: 💰{coins}\n"]
            for item in items[:10]:
                can = "✅" if coins >= item["price"] else "❌"
                lines.append(f"{can} {item['icon']} <b>{item['name']}</b> — {item['price']}💰\n   {item['description']}")
            lines.append("\n💡 Для покупки: <code>купить {id}</code>")
            await tg_send(chat_id, "\n".join(lines))
        except Exception as e:
            await tg_send(chat_id, f"❌ Ошибка: {e}")

    # ── купить ──
    elif text.lower().startswith("купить "):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await tg_send(chat_id, "❌ Формат: <code>купить 1</code>")
            return {"ok": True}
        item_id = int(parts[1])
        try:
            result = buy_item(chat_id, item_id)
            await tg_send(chat_id, f"✅ Куплено: <b>{result['item_name']}</b>!\nОсталось: 💰{result['coins_left']}")
        except Exception as e:
            await tg_send(chat_id, f"❌ {e}")

    # ── /help ──
    elif text == "/help":
        await tg_send(chat_id, (
            "📖 <b>Команды:</b>\n\n"
            "🔍 Поиск: отправь ник или Steam ID\n"
            "🔗 Привязка: <code>привязать 105248644</code>\n\n"
            "/start — главное меню\n"
            "/stats — моя статистика\n"
            "/profile — мой профиль\n"
            "/missions — миссии\n"
            "/shop — магазин\n"
            "/unlink — отвязать Steam\n"
            "/help — эта справка"
        ))

    elif text.startswith("/"):
        await tg_send(chat_id, "❓ Неизвестная команда. /help")

    # ── поиск игрока ──
    else:
        await tg_send(chat_id, f"🔍 Ищу <b>{text}</b>...")
        try:
            query = text
            if query.isdigit():
                q_int = int(query)
                account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
            else:
                results = await search_combined(query)
                if not results:
                    await tg_send(chat_id, "❌ Игрок не найден. Проверь ник или ID.")
                    return {"ok": True}
                account_id = results[0]["account_id"]

            result = await _load_player(str(account_id))
            msg = format_player_message(result)
            await tg_send(chat_id, msg, reply_markup=webapp_btn(
                "📊 Подробный анализ",
                f"{WEBAPP_URL}?player_id={account_id}" if WEBAPP_URL else None
            ))
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            await tg_send(chat_id, "⚠️ Ошибка загрузки. Попробуй позже.")

    return {"ok": True}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 8000)))
