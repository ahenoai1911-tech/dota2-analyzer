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

app = FastAPI(title="Dota 2 Analyzer API", version="2.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ENV ──────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
WEBAPP_URL       = os.getenv("WEBAPP_URL", "")
STRATZ_TOKEN     = os.getenv("STRATZ_TOKEN", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
DATABASE_URL     = os.getenv("DATABASE_URL", "")

OPENDOTA_BASE = "https://api.opendota.com/api"
STRATZ_GQL    = "https://api.stratz.com/graphql"
STRATZ_BASE   = "https://api.stratz.com/api/v1"

# ── CACHE ────────────────────────────────────────────────────────────────────
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

# ── DATABASE ─────────────────────────────────────────────────────────────────
def get_db_connection():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            steam_id BIGINT,
            username TEXT,
            coins INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            premium_until TIMESTAMP,
            ai_requests_used INTEGER DEFAULT 0,
            ai_requests_reset_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
            icon TEXT DEFAULT '🎯'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_missions (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            mission_id INTEGER NOT NULL,
            progress INTEGER DEFAULT 0,
            completed BOOLEAN DEFAULT FALSE,
            claimed BOOLEAN DEFAULT FALSE,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
            FOREIGN KEY (mission_id) REFERENCES missions(id)
        )
    """)

    # Миграция: привести типы к BOOLEAN если вдруг INTEGER
    c.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='user_missions'
                AND column_name='completed'
                AND data_type='integer'
            ) THEN
                ALTER TABLE user_missions
                    ALTER COLUMN completed DROP DEFAULT,
                    ALTER COLUMN claimed   DROP DEFAULT;
                ALTER TABLE user_missions
                    ALTER COLUMN completed TYPE BOOLEAN USING (completed::int::boolean),
                    ALTER COLUMN claimed   TYPE BOOLEAN USING (claimed::int::boolean);
                ALTER TABLE user_missions
                    ALTER COLUMN completed SET DEFAULT FALSE,
                    ALTER COLUMN claimed   SET DEFAULT FALSE;
            END IF;
        END$$;
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            type TEXT NOT NULL,
            price INTEGER NOT NULL,
            icon TEXT DEFAULT '🎁',
            data TEXT
        )
    """)

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

    conn.commit()

    # Удалить дубли в shop_items
    c.execute("""
        DELETE FROM shop_items
        WHERE id NOT IN (
            SELECT MIN(id) FROM shop_items GROUP BY name
        )
    """)
    conn.commit()

    c.execute("DROP INDEX IF EXISTS shop_items_name_unique")
    conn.commit()
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS shop_items_name_unique ON shop_items(name)")
    conn.commit()

    # Дефолтные миссии
    c.execute("SELECT COUNT(*) as count FROM missions")
    if c.fetchone()['count'] == 0:
        default_missions = [
            ("daily",   "Победная серия",  "Выиграй 3 матча подряд",              "win_streak",    3,       100, 150,  "🔥"),
            ("daily",   "Мастер фарма",    "Набери 600+ GPM в матче",             "gpm",           600,     75,  120,  "💰"),
            ("daily",   "Высокий KDA",     "Сыграй матч с KDA 4+",               "kda",           4,       80,  130,  "⭐"),
            ("daily",   "Командный игрок", "Сделай 15+ ассистов в матче",         "assists",       15,      60,  100,  "🤝"),
            ("daily",   "Победитель",      "Выиграй 1 матч",                      "wins",          1,       30,  60,   "🏅"),
            ("weekly",  "Марафонец",       "Сыграй 20 матчей",                    "matches",       20,      300, 500,  "🏃"),
            ("weekly",  "Доминатор",       "Выиграй 10 игр",                      "wins",          10,      400, 600,  "👑"),
            ("weekly",  "Стабильность",    "Держи WR выше 50% (последние 20)",    "winrate",       50,      250, 400,  "📈"),
            ("weekly",  "Боец",            "Набери средний KDA 3+ (последние 20)","avg_kda",       3,       200, 350,  "⚔️"),
            ("monthly", "Легенда",         "Выиграй 50 игр",                      "wins",          50,      1000,2000, "🏆"),
            ("monthly", "Несокрушимый",    "Достигни винрейта 55%+",              "winrate",       55,      1200,2500, "💎"),
            ("monthly", "Профессионал",    "Набери средний KDA 4.0+",             "avg_kda",       4,       900, 1800, "🎯"),
        ]
        c.executemany("""
            INSERT INTO missions (type, title, description, requirement, target_value, reward_coins, reward_xp, icon)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, default_missions)
        conn.commit()

    # Дефолтные товары магазина
    c.execute("SELECT COUNT(*) as count FROM shop_items")
    if c.fetchone()['count'] == 0:
        shop_items = [
            ("Premium 30 дней",  "30 дней Premium: 3 миссии в день + 100 AI запросов", "premium",        129,  "⭐", "days:30"),
            ("XP Booster x2",    "Удваивает получаемый опыт на 24 часа",               "booster_xp",     500,  "⚡", "duration:24,multiplier:2"),
            ("Coin Booster x2",  "Удваивает награды монет на 24 часа",                 "booster_coins",  600,  "💰", "duration:24,multiplier:2"),
            ("Mega Booster",     "x2 XP и монеты на 48 часов",                         "booster_mega",   1500, "🚀", "duration:48,xp:2,coins:2"),
            ("Золотая рамка",    "Золотая рамка для профиля",                           "cosmetic_frame", 300,  "🖼️","color:gold"),
            ("Алмазная рамка",   "Алмазная рамка для профиля",                         "cosmetic_frame", 800,  "💎","color:diamond"),
            ("Титул: Ветеран",   "Отображается в профиле",                             "cosmetic_title", 500,  "🎖️","title:Ветеран"),
            ("Титул: Легенда",   "Отображается в профиле",                             "cosmetic_title", 1000, "👑","title:Легенда"),
            ("AI Запросы +10",   "10 дополнительных AI запросов",                      "special_ai",     250,  "🤖","queries:10"),
            ("Сброс миссий",     "Обновляет все текущие миссии",                       "special_refresh",300,  "🔄","refresh:all"),
        ]
        c.executemany("""
            INSERT INTO shop_items (name, description, type, price, icon, data)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
        """, shop_items)
        conn.commit()

    conn.close()

init_db()

# ── USER HELPERS ─────────────────────────────────────────────────────────────
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

def is_premium(telegram_id: int) -> bool:
    user = get_user(telegram_id)
    if not user or not user.get('premium_until'):
        return False
    premium_until = user['premium_until']
    if isinstance(premium_until, str):
        premium_until = datetime.fromisoformat(premium_until)
    return premium_until > datetime.now()

def check_ai_limit(telegram_id: int) -> dict:
    user = get_user(telegram_id)
    if not user:
        return {"allowed": False, "remaining": 0, "limit": 0, "premium": False}
    premium = is_premium(telegram_id)
    limit = 100 if premium else 5
    reset_at = user.get('ai_requests_reset_at')
    if reset_at:
        if isinstance(reset_at, str):
            reset_at = datetime.fromisoformat(reset_at)
        if datetime.now() - reset_at > timedelta(days=1):
            conn = get_db_connection(); c = conn.cursor()
            c.execute("UPDATE users SET ai_requests_used = 0, ai_requests_reset_at = CURRENT_TIMESTAMP WHERE telegram_id = %s", (telegram_id,))
            conn.commit(); conn.close()
            user['ai_requests_used'] = 0
    used = user.get('ai_requests_used', 0)
    remaining = max(0, limit - used)
    return {"allowed": remaining > 0, "remaining": remaining, "limit": limit, "premium": premium}

def increment_ai_usage(telegram_id: int):
    conn = get_db_connection(); c = conn.cursor()
    c.execute("UPDATE users SET ai_requests_used = ai_requests_used + 1 WHERE telegram_id = %s", (telegram_id,))
    conn.commit(); conn.close()

def activate_premium(telegram_id: int, days: int = 30):
    conn = get_db_connection(); c = conn.cursor()
    user = get_user(telegram_id)
    if user and user.get('premium_until'):
        premium_until = user['premium_until']
        if isinstance(premium_until, str):
            premium_until = datetime.fromisoformat(premium_until)
        new_until = (premium_until + timedelta(days=days)) if premium_until > datetime.now() else (datetime.now() + timedelta(days=days))
    else:
        new_until = datetime.now() + timedelta(days=days)
    c.execute("UPDATE users SET premium_until = %s WHERE telegram_id = %s", (new_until, telegram_id))
    conn.commit(); conn.close()
    return new_until

# ── MISSIONS ──────────────────────────────────────────────────────────────────
def assign_user_missions(telegram_id: int):
    """Назначить миссии если ещё не назначены сегодня"""
    conn = get_db_connection(); c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) as count FROM user_missions
        WHERE telegram_id = %s AND DATE(assigned_at) = CURRENT_DATE
    """, (telegram_id,))
    if c.fetchone()['count'] > 0:
        conn.close(); return

    premium = is_premium(telegram_id)
    limit = 3 if premium else 1

    c.execute(f"SELECT id FROM missions WHERE type = 'daily' ORDER BY RANDOM() LIMIT {limit}")
    mission_ids = [r['id'] for r in c.fetchall()]
    for mid in mission_ids:
        c.execute("INSERT INTO user_missions (telegram_id, mission_id) VALUES (%s, %s)", (telegram_id, mid))
    conn.commit(); conn.close()

def get_user_missions(telegram_id: int) -> list:
    conn = get_db_connection(); c = conn.cursor()
    c.execute("""
        SELECT um.id, m.type, m.title, m.description, m.icon,
               m.target_value, m.reward_coins, m.reward_xp,
               um.progress, um.completed, um.claimed
        FROM user_missions um
        JOIN missions m ON um.mission_id = m.id
        WHERE um.telegram_id = %s AND um.claimed = FALSE
        ORDER BY um.completed DESC, m.type
    """, (telegram_id,))
    rows = c.fetchall(); conn.close()
    result = []
    for r in rows:
        row = dict(r)
        # Нормализуем: если progress >= target — считаем completed
        if row['progress'] >= row['target_value']:
            row['completed'] = True
        result.append(row)
    return result

def compute_mission_progress(requirement: str, target: int, stats: dict, trend: dict, recent: list) -> tuple[int, bool]:
    """Вычисляет (progress, completed) для одного требования миссии"""
    progress = 0

    if requirement == "win_streak":
        streak = trend.get("streak", {})
        progress = min(streak.get("count", 0), target) if streak.get("type") == "win" else 0

    elif requirement == "gpm":
        gpms = [m.get("gpm", 0) for m in recent if m.get("gpm")]
        progress = int(max(gpms)) if gpms else 0

    elif requirement == "kda":
        kdas = [m.get("kda", 0) for m in recent if m.get("kda")]
        progress = int(max(kdas)) if kdas else 0

    elif requirement == "assists":
        assists_list = [m.get("assists", 0) for m in recent if m.get("assists")]
        progress = int(max(assists_list)) if assists_list else 0

    elif requirement == "wins":
        progress = int(sum(1 for m in recent if m.get("win")))

    elif requirement == "matches":
        progress = len(recent)

    elif requirement == "winrate":
        progress = int(stats.get("winrate", 0))

    elif requirement == "avg_kda":
        progress = int(trend.get("last20_avg_kda", 0) or 0)

    else:
        # Неизвестное требование — не блокируем, прогресс 0
        progress = 0

    completed = progress >= target
    return (min(progress, target * 2), completed)  # cap at 2x target to avoid overflow

def update_mission_progress(telegram_id: int, player_data: dict):
    """Обновить прогресс ВСЕХ незабранных миссий"""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT um.id, m.requirement, m.target_value
        FROM user_missions um
        JOIN missions m ON um.mission_id = m.id
        WHERE um.telegram_id = %s AND um.claimed = FALSE
    """, (telegram_id,))
    missions = c.fetchall()

    stats  = player_data.get("stats", {})
    trend  = player_data.get("trend", {})
    recent = player_data.get("recent_matches", [])

    for mission in missions:
        progress, completed = compute_mission_progress(
            mission["requirement"], mission["target_value"],
            stats, trend, recent
        )
        c.execute("""
            UPDATE user_missions
            SET progress = %s,
                completed = %s,
                completed_at = CASE WHEN %s AND completed_at IS NULL
                               THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE id = %s
        """, (progress, completed, completed, mission["id"]))

    conn.commit()
    conn.close()

# ── SHOP ─────────────────────────────────────────────────────────────────────
def get_shop_items() -> list:
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT id, name, description, type, price, icon FROM shop_items ORDER BY type, price")
    rows = c.fetchall(); conn.close()
    return [dict(r) for r in rows]

def buy_item(telegram_id: int, item_id: int) -> dict:
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT name, price, type, data FROM shop_items WHERE id = %s", (item_id,))
    item = c.fetchone()
    if not item:
        conn.close(); raise Exception("Предмет не найден")

    c.execute("SELECT coins FROM users WHERE telegram_id = %s", (telegram_id,))
    user = c.fetchone()
    if not user:
        conn.close(); raise Exception("Пользователь не найден")

    if item["type"] != "premium" and user["coins"] < item["price"]:
        conn.close(); raise Exception(f"Недостаточно монет. Нужно: {item['price']}, есть: {user['coins']}")

    if item["type"] == "premium":
        days = 30
        if item.get("data"):
            parts = dict(p.split(':') for p in item["data"].split(','))
            days = int(parts.get('days', 30))
        user_data = get_user(telegram_id)
        if user_data and user_data.get('premium_until'):
            premium_until = user_data['premium_until']
            if isinstance(premium_until, str):
                premium_until = datetime.fromisoformat(premium_until)
            new_until = (premium_until + timedelta(days=days)) if premium_until > datetime.now() else (datetime.now() + timedelta(days=days))
        else:
            new_until = datetime.now() + timedelta(days=days)
        c.execute("UPDATE users SET premium_until = %s WHERE telegram_id = %s", (new_until, telegram_id))

    elif item["type"] == "special_ai":
        if item.get("data"):
            parts = dict(p.split(':') for p in item["data"].split(','))
            queries = int(parts.get('queries', 10))
            c.execute("UPDATE users SET ai_requests_used = GREATEST(0, ai_requests_used - %s) WHERE telegram_id = %s", (queries, telegram_id))

    elif item["type"] == "special_refresh":
        # Сбросить сегодняшние миссии
        c.execute("DELETE FROM user_missions WHERE telegram_id = %s AND DATE(assigned_at) = CURRENT_DATE AND claimed = FALSE", (telegram_id,))

    new_coins = user["coins"] - (item["price"] if item["type"] != "premium" else 0)
    if item["type"] != "premium":
        c.execute("UPDATE users SET coins = %s WHERE telegram_id = %s", (new_coins, telegram_id))

    if item["type"] not in ["premium", "special_ai", "special_refresh"]:
        c.execute("INSERT INTO user_inventory (telegram_id, item_id) VALUES (%s, %s)", (telegram_id, item_id))

    c.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (%s, 'spend', %s, %s)",
              (telegram_id, item["price"], f"Купил: {item['name']}"))
    conn.commit(); conn.close()
    return {"item_name": item["name"], "coins_left": new_coins}

# ── PLAYER LOADING ────────────────────────────────────────────────────────────
async def _load_player(query: str) -> dict:
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

# ── STRATZ ───────────────────────────────────────────────────────────────────
def stratz_headers() -> dict:
    return {"Authorization": f"Bearer {STRATZ_TOKEN}", "User-Agent": "Dota2AnalyzerBot/2.2"}

async def stratz_player(account_id: int) -> dict | None:
    query = """
    query Player($steamAccountId: Long!) {
      player(steamAccountId: $steamAccountId) {
        steamAccount { id name avatar profileUri isAnonymous seasonRank }
        winCount matchCount
        heroesPerformance(request: { take: 10 }) {
          hero { displayName shortName }
          winCount matchCount avgKills avgDeaths avgAssists
          avgGoldPerMinute avgExperiencePerMinute avgNetworth avgImp
        }
        matches(request: { take: 20, orderBy: END_DATE_TIME }) {
          id didRadiantWin durationSeconds endDateTime gameMode
          players(steamAccountId: $steamAccountId) {
            isRadiant kills deaths assists goldPerMinute experiencePerMinute
            networth heroDamage towerDamage heroHealingDone numLastHits numDenies
            hero { displayName shortName }
          }
        }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(STRATZ_GQL,
                json={"query": query, "variables": {"steamAccountId": account_id}},
                headers=stratz_headers())
            data = r.json()
            if "errors" in data:
                return None
            return data.get("data", {}).get("player")
    except Exception as e:
        logger.error(f"Stratz error: {e}"); return None

async def stratz_search(nickname: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{STRATZ_BASE}/search", params={"query": nickname}, headers=stratz_headers())
            players = r.json().get("players", [])
            return [{"account_id": p["steamAccount"]["id"],
                     "personaname": p["steamAccount"].get("name","Unknown"),
                     "avatarfull": p["steamAccount"].get("avatar","")} for p in players]
    except Exception as e:
        logger.error(f"Stratz search error: {e}"); return []

# ── OPENDOTA ──────────────────────────────────────────────────────────────────
async def od_get(path: str, params: dict = None):
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{OPENDOTA_BASE}{path}", params=params)
            return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.error(f"OpenDota error {path}: {e}"); return None

async def od_player(account_id):  return await od_get(f"/players/{account_id}")
async def od_wl(account_id):      return await od_get(f"/players/{account_id}/wl")
async def od_matches(account_id): return await od_get(f"/players/{account_id}/recentMatches", {"limit": 20})
async def od_heroes(account_id):  return await od_get(f"/players/{account_id}/heroes", {"limit": 10})
async def od_search(nickname):    return await od_get("/search", {"q": nickname})

# ── ANALYSIS ──────────────────────────────────────────────────────────────────
def calc_kda(kills, deaths, assists):
    return round((kills + assists) / max(deaths, 1), 2)

def rank_tier_to_name(rank_tier) -> str:
    if rank_tier is None: return "Uncalibrated"
    tiers = {1:"Herald",2:"Guardian",3:"Crusader",4:"Archon",5:"Legend",6:"Ancient",7:"Divine",8:"Immortal"}
    tier = int(str(rank_tier)[0]) if rank_tier else 0
    star = int(str(rank_tier)[-1]) if rank_tier and len(str(rank_tier)) > 1 else 0
    name = tiers.get(tier, "Unknown")
    return f"{name} {'★'*star}" if star else name

def compute_streak(matches: list) -> dict:
    if not matches: return {"type":"none","count":0}
    first_win = matches[0].get("win", False)
    count = sum(1 for m in matches if m.get("win") == first_win)
    # count only leading streak
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
        vals = [m.get(key,0) or 0 for m in lst]
        return round(sum(vals)/len(vals), 2) if vals else 0
    def wr(lst):
        return round(sum(1 for m in lst if m.get("win"))/len(lst)*100, 1) if lst else 0
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
    heroes = []
    for h in (player_data.get("heroesPerformance") or []):
        hero = h.get("hero",{}); hm = h.get("matchCount",0) or 1; hw = h.get("winCount",0)
        heroes.append({"hero_name":hero.get("displayName","Unknown"),"hero_short":hero.get("shortName",""),
                       "matches":hm,"wins":hw,"winrate":round(hw/hm*100,1),
                       "kda":calc_kda(h.get("avgKills",0),h.get("avgDeaths",1),h.get("avgAssists",0)),
                       "avg_gpm":round(h.get("avgGoldPerMinute",0))})
    matches = []
    for m in (player_data.get("matches") or []):
        ps = (m.get("players") or [{}])[0]
        hero_info = ps.get("hero") or {}
        is_radiant = ps.get("isRadiant", True)
        win_flag = (is_radiant and m.get("didRadiantWin",False)) or (not is_radiant and not m.get("didRadiantWin",False))
        dur = m.get("durationSeconds", 0)
        matches.append({"match_id":m.get("id"),"hero":hero_info.get("displayName","Unknown"),
                        "hero_short":hero_info.get("shortName",""),"win":win_flag,
                        "kills":ps.get("kills",0),"deaths":ps.get("deaths",0),"assists":ps.get("assists",0),
                        "kda":calc_kda(ps.get("kills",0),ps.get("deaths",0),ps.get("assists",0)),
                        "gpm":ps.get("goldPerMinute",0),"xpm":ps.get("experiencePerMinute",0),
                        "networth":ps.get("networth",0),"hero_damage":ps.get("heroDamage",0),
                        "tower_damage":ps.get("towerDamage",0),"healing":ps.get("heroHealingDone",0),
                        "last_hits":ps.get("numLastHits",0),"denies":ps.get("numDenies",0),
                        "duration_min":dur//60,"duration_sec":dur%60,"end_time":m.get("endDateTime")})
    return {"source":"stratz","account_id":account_id,
            "profile":{"name":sa.get("name","Unknown"),"avatar":sa.get("avatar",""),
                       "profile_url":sa.get("profileUri",""),"rank":rank_tier_to_name(sa.get("seasonRank")),
                       "rank_tier":sa.get("seasonRank"),"is_anonymous":sa.get("isAnonymous",False)},
            "stats":{"wins":win,"losses":loss,"total_matches":total,"winrate":round(win/total*100,1)},
            "top_heroes":heroes,"recent_matches":matches,"trend":compute_trend(matches)}

def build_from_opendota(player, wl, matches, heroes, account_id):
    profile_data = player.get("profile", {})
    mmr = player.get("mmr_estimate",{}).get("estimate")
    rank_tier = player.get("rank_tier")
    win = (wl or {}).get("win",0); loss = (wl or {}).get("lose",0); total = win+loss or 1
    heroes_out = []
    for h in (heroes or [])[:10]:
        hm = h.get("games",0) or 1; hw = h.get("win",0)
        heroes_out.append({"hero_id":h.get("hero_id"),"hero_name":"","matches":hm,"wins":hw,
                           "winrate":round(hw/hm*100,1),"kda":0})
    matches_out = []
    for m in (matches or [])[:20]:
        pslot = m.get("player_slot",0); is_radiant = pslot < 128
        radiant_win = m.get("radiant_win",False)
        win_flag = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        dur = m.get("duration",0)
        matches_out.append({"match_id":m.get("match_id"),"hero_id":m.get("hero_id"),"hero":"","hero_short":"",
                            "win":win_flag,"kills":m.get("kills",0),"deaths":m.get("deaths",0),
                            "assists":m.get("assists",0),
                            "kda":calc_kda(m.get("kills",0),m.get("deaths",0),m.get("assists",0)),
                            "gpm":m.get("gold_per_min",0),"xpm":m.get("xp_per_min",0),
                            "networth":0,"hero_damage":m.get("hero_damage",0),
                            "tower_damage":m.get("tower_damage",0),"healing":m.get("hero_healing",0),
                            "last_hits":m.get("last_hits",0),"denies":m.get("denies",0),
                            "duration_min":dur//60,"duration_sec":dur%60,"end_time":m.get("start_time")})
    return {"source":"opendota","account_id":account_id,
            "profile":{"name":profile_data.get("personaname","Unknown"),"avatar":profile_data.get("avatarfull",""),
                       "profile_url":profile_data.get("profileurl",""),"rank":rank_tier_to_name(rank_tier),
                       "rank_tier":rank_tier,"mmr_estimate":mmr,"is_anonymous":False},
            "stats":{"wins":win,"losses":loss,"total_matches":total,"winrate":round(win/total*100,1)},
            "top_heroes":heroes_out,"recent_matches":matches_out,"trend":compute_trend(matches_out)}

async def search_combined(query: str) -> list:
    results = []
    if STRATZ_TOKEN:
        results = await stratz_search(query)
    if not results:
        od = await od_search(query)
        if od:
            results = [{"account_id":p.get("account_id"),"personaname":p.get("personaname","Unknown"),
                        "avatarfull":p.get("avatarfull","")} for p in od]
    return results

# ── FASTAPI ENDPOINTS ─────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "message": "Dota 2 Analyzer API v2.2"}

@app.get("/test-ai")
async def test_ai():
    return {"groq_key_set": bool(GROQ_API_KEY), "groq_key_length": len(GROQ_API_KEY) if GROQ_API_KEY else 0}

@app.get("/player")
async def find_player(query: str = Query(..., min_length=1)):
    query = query.strip()
    cache_key = f"player:{query}"
    cached = get_cache(cache_key)
    if cached: return cached
    if query.isdigit():
        q_int = int(query)
        account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
    else:
        results = await search_combined(query)
        if not results: raise HTTPException(status_code=404, detail="Player not found")
        account_id = results[0]["account_id"]
    result = None
    if STRATZ_TOKEN:
        sd = await stratz_player(account_id)
        if sd: result = build_from_stratz(sd, account_id)
    if not result:
        player, wl, matches, heroes = await asyncio.gather(
            od_player(account_id), od_wl(account_id), od_matches(account_id), od_heroes(account_id))
        if not player: raise HTTPException(status_code=404, detail="Profile not found")
        result = build_from_opendota(player, wl, matches, heroes, account_id)
    if (not result.get("recent_matches")) and result["stats"]["total_matches"] <= 1:
        raise HTTPException(status_code=403, detail="Private profile or no match data")
    set_cache(cache_key, result)
    return result

@app.get("/search")
async def search(q: str = Query(..., min_length=1)):
    return (await search_combined(q))[:5]

@app.get("/matches")
async def get_matches(player_id: int = Query(...)):
    cache_key = f"matches:{player_id}"
    cached = get_cache(cache_key)
    if cached: return cached
    matches = await od_matches(player_id)
    if not matches: raise HTTPException(status_code=404, detail="Matches not found")
    set_cache(cache_key, matches)
    return matches

@app.get("/heroes")
async def get_heroes(player_id: int = Query(...)):
    cache_key = f"heroes:{player_id}"
    cached = get_cache(cache_key)
    if cached: return cached
    heroes = await od_heroes(player_id)
    set_cache(cache_key, heroes)
    return heroes

# ── AI ────────────────────────────────────────────────────────────────────────
class AIRequest(BaseModel):
    message: str
    player_context: str = ""
    history: list = []
    telegram_id: int = None

class RoastRequest(BaseModel):
    player_context: str
    mode: str = "toxic"

@app.post("/ai")
async def ai_chat(req: AIRequest):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="AI not configured. Set GROQ_API_KEY.")
    telegram_id = req.telegram_id
    limit_check = None
    if telegram_id:
        limit_check = check_ai_limit(telegram_id)
        if not limit_check["allowed"]:
            raise HTTPException(status_code=429, detail=
                f"Лимит AI запросов исчерпан (0/{limit_check['limit']}). " +
                ("" if limit_check["premium"] else "Купи Premium за 129⭐ для 100 запросов/день!"))
    system = """You are an expert Dota 2 coach and analyst.
Analyze player statistics and give specific, actionable advice.
Be concise but insightful. Use bullet points where helpful.
Respond in the same language the user writes in (Russian or English).
Keep responses under 300 words."""
    if req.history:
        messages = req.history[-6:] + [{"role":"user","content":req.message}]
    else:
        content = f"Player data:\n{req.player_context}\n\n---\n{req.message}" if req.player_context else req.message
        messages = [{"role":"user","content":content}]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},
                json={"model":"llama-3.3-70b-versatile",
                      "messages":[{"role":"system","content":system}]+messages,
                      "max_tokens":1000,"temperature":0.7})
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"AI request failed: {r.text[:100]}")
        text = r.json()["choices"][0]["message"]["content"]
        if telegram_id:
            increment_ai_usage(telegram_id)
            limit_check = check_ai_limit(telegram_id)
        return {"reply":text,
                "ai_remaining": limit_check["remaining"] if limit_check else None,
                "ai_limit":     limit_check["limit"]     if limit_check else None}
    except HTTPException: raise
    except Exception as e:
        logger.error(f"AI error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/roast")
async def roast_player(req: RoastRequest):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=503, detail="AI not configured")
    mode_prompts = {
        "toxic":   "You are a BRUTAL, SAVAGE Dota 2 roaster. Destroy this player with dark humor. Short punchy lines with emojis. Under 200 words. Write in Russian. End with a devastating verdict.",
        "friendly":"You are a friendly Dota 2 comedian. Roast gently with humor. Light and fun. Format with emojis. Under 150 words. Write in Russian.",
        "coach":   "You are a Dota 2 coach who roasts with constructive feedback. Point out mistakes with humor. Give actual advice. Under 200 words. Write in Russian.",
        "brutal":  "You are the MOST SAVAGE Dota 2 roaster. MAXIMUM DESTRUCTION. Every line should hurt. Format with skull emojis 💀. Under 250 words. Write in Russian.",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},
                json={"model":"llama-3.3-70b-versatile",
                      "messages":[{"role":"system","content":mode_prompts.get(req.mode,mode_prompts["toxic"])},
                                  {"role":"user","content":f"Roast this Dota 2 player:\n\n{req.player_context}"}],
                      "max_tokens":800,"temperature":0.9})
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail="AI request failed")
        return {"roast": r.json()["choices"][0]["message"]["content"]}
    except Exception as e:
        logger.error(f"Roast error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── MISSIONS ENDPOINTS ────────────────────────────────────────────────────────
@app.get("/missions")
async def get_missions(telegram_id: int = Query(...)):
    try:
        upsert_user(telegram_id)
        assign_user_missions(telegram_id)
        user = get_user(telegram_id)
        if user and user.get("steam_id"):
            try:
                player_data = await _load_player(str(user["steam_id"]))
                update_mission_progress(telegram_id, player_data)
            except Exception as e:
                logger.warning(f"Could not update mission progress: {e}")
        missions = get_user_missions(telegram_id)
        return {"status": "ok", "missions": missions}
    except Exception as e:
        logger.error(f"Get missions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/missions/claim")
async def claim_mission(req: Request):
    data = await req.json()
    telegram_id = data.get("telegram_id")
    mission_id  = data.get("mission_id")

    if not telegram_id or not mission_id:
        raise HTTPException(status_code=400, detail="Missing telegram_id or mission_id")

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT um.id, um.completed, um.claimed, um.progress,
                   m.reward_coins, m.reward_xp, m.title, m.target_value
            FROM user_missions um
            JOIN missions m ON um.mission_id = m.id
            WHERE um.id = %s AND um.telegram_id = %s
        """, (mission_id, telegram_id))
        mission = c.fetchone()

        if not mission:
            conn.close()
            raise HTTPException(status_code=404, detail="Mission not found")

        if mission["claimed"]:
            conn.close()
            raise HTTPException(status_code=400, detail="Mission already claimed")

        # Считаем выполненной если флаг TRUE ИЛИ прогресс достиг цели
        is_completed = bool(mission["completed"]) or (int(mission["progress"]) >= int(mission["target_value"]))
        if not is_completed:
            conn.close()
            raise HTTPException(status_code=400, detail="Mission not completed")

        # Исправить флаг если нужно
        if not bool(mission["completed"]):
            c.execute("UPDATE user_missions SET completed = TRUE, completed_at = CURRENT_TIMESTAMP WHERE id = %s", (mission_id,))

        # Выдать награду и пометить как забранную
        c.execute("UPDATE user_missions SET claimed = TRUE WHERE id = %s", (mission_id,))
        c.execute("UPDATE users SET coins = coins + %s, xp = xp + %s WHERE telegram_id = %s",
                  (mission["reward_coins"], mission["reward_xp"], telegram_id))
        c.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (%s,'earn',%s,%s)",
                  (telegram_id, mission["reward_coins"], f"Миссия: {mission['title']}"))
        conn.commit()

        c.execute("SELECT coins, xp, level FROM users WHERE telegram_id = %s", (telegram_id,))
        user = c.fetchone()
        conn.close()

        return {"status":"ok",
                "reward":{"coins":mission["reward_coins"],"xp":mission["reward_xp"]},
                "user": dict(user) if user else None}
    except HTTPException:
        conn.close(); raise
    except Exception as e:
        conn.close()
        logger.error(f"Claim mission error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── USER / SHOP / PREMIUM ─────────────────────────────────────────────────────
@app.get("/user/profile")
async def get_user_profile(telegram_id: int = Query(...)):
    try:
        upsert_user(telegram_id)
        user = get_user(telegram_id)
        if not user: raise HTTPException(status_code=404, detail="User not found")
        return {"status":"ok","user":user}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/shop")
async def get_shop():
    try:
        return {"status":"ok","items":get_shop_items()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/shop/buy")
async def buy_shop_item(req: Request):
    data = await req.json()
    telegram_id = data.get("telegram_id")
    item_id     = data.get("item_id")
    if not telegram_id or not item_id:
        raise HTTPException(status_code=400, detail="Missing telegram_id or item_id")
    try:
        result = buy_item(telegram_id, item_id)
        return {"status":"ok", **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/premium/buy")
async def buy_premium(req: Request):
    data = await req.json()
    telegram_id = data.get("telegram_id")
    if not telegram_id:
        raise HTTPException(status_code=400, detail="Missing telegram_id")
    try:
        premium_until = activate_premium(telegram_id, days=30)
        return {"status":"ok","message":"Premium активирован!",
                "premium_until":premium_until.isoformat(),
                "features":{"missions":3,"ai_requests":100}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/premium/status")
async def get_premium_status(telegram_id: int = Query(...)):
    try:
        premium = is_premium(telegram_id)
        ai_limit = check_ai_limit(telegram_id)
        user = get_user(telegram_id)
        return {"status":"ok","premium":premium,
                "premium_until": user.get('premium_until').isoformat() if user and user.get('premium_until') else None,
                "ai_requests":{"used":user.get('ai_requests_used',0) if user else 0,
                               "limit":ai_limit["limit"],"remaining":ai_limit["remaining"]},
                "missions_limit": 3 if premium else 1}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── TELEGRAM WEBHOOK ──────────────────────────────────────────────────────────
async def tg_send(chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup: payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload)

def format_player_message(data: dict) -> str:
    p = data["profile"]; s = data["stats"]; t = data.get("trend",{})
    streak = t.get("streak",{})
    src_icon = "⚡" if data.get("source") == "stratz" else "📊"
    anon = " 🔒" if p.get("is_anonymous") else ""
    streak_str = ""
    if streak.get("count",0) >= 2:
        emoji = "🔥" if streak["type"]=="win" else "❄️"
        streak_str = f"\n{emoji} Streak: {streak['count']} {'побед' if streak['type']=='win' else 'поражений'} подряд"
    heroes_lines = ""
    for i, h in enumerate(data.get("top_heroes",[])[:5],1):
        name = h.get("hero_name") or f"Hero#{h.get('hero_id','?')}"
        heroes_lines += f"  {i}. {name} — {h['matches']}г, WR {h['winrate']}%, KDA {h['kda']}\n"
    matches_lines = ""
    for m in data.get("recent_matches",[])[:5]:
        hero = m.get("hero") or f"Hero#{m.get('hero_id','?')}"
        result = "✅" if m["win"] else "❌"
        matches_lines += f"  {result} {hero} — {m['kills']}/{m['deaths']}/{m['assists']} ({m['duration_min']}:{m['duration_sec']:02d})\n"
    mmr_str = f"\n📊 MMR: ~{p['mmr_estimate']}" if p.get("mmr_estimate") else ""
    return (f"{src_icon} <b>{p['name']}</b>{anon}\n"
            f"🏅 Ранг: {p.get('rank','?')}{mmr_str}\n"
            f"📈 Винрейт: {s['winrate']}% ({s['wins']}П / {s['losses']}П)\n"
            f"🎮 Матчей: {s['total_matches']}\n"
            f"\n📊 Тренды:\n"
            f"  Последние 5:  WR {t.get('last5_winrate','?')}%, KDA {t.get('last5_avg_kda','?')}\n"
            f"  Последние 20: WR {t.get('last20_winrate','?')}%, GPM {t.get('last20_avg_gpm','?')}\n"
            f"{streak_str}\n"
            f"\n🦸 Топ герои:\n{heroes_lines}"
            f"\n🕹 Последние матчи:\n{matches_lines}"
            f"\n🔗 <a href='https://stratz.com/players/{data['account_id']}'>Открыть на Stratz</a>")

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    if "message" not in data: return {"ok": True}
    chat_id  = data["message"]["chat"]["id"]
    text     = data["message"].get("text","").strip()
    username = data["message"]["from"].get("username","")
    try: upsert_user(chat_id, username)
    except: pass

    def webapp_btn(label="🎮 Открыть анализатор", url=None):
        u = url or WEBAPP_URL
        if not u: return None
        return {"inline_keyboard":[[{"text":label,"web_app":{"url":u}}]]}

    if text == "/start":
        user = get_user(chat_id)
        steam_linked = user and user.get("steam_id")
        if steam_linked:
            msg = (f"👋 С возвращением, <b>{username or 'игрок'}</b>!\n\n"
                   f"🎮 Steam: <code>{user['steam_id']}</code>\n\n"
                   "• /stats — статистика\n• /missions — миссии\n• /shop — магазин\n• /profile — профиль")
        else:
            msg = ("👋 <b>Dota 2 Analyzer</b>\n\nОтправь ник или Steam ID игрока для анализа.\n\n"
                   "🔗 Привязать: <code>привязать 105248644</code>\n\nИли открой Web App 👇")
        await tg_send(chat_id, msg, reply_markup=webapp_btn())

    elif text.lower().startswith("привязать ") or text.lower().startswith("link "):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await tg_send(chat_id, "❌ Формат: <code>привязать 105248644</code>"); return {"ok":True}
        sid = int(parts[1])
        if sid > 76561197960265728: sid = steam64_to_account_id(sid)
        try:
            link_steam(chat_id, sid, username)
            await tg_send(chat_id, f"✅ Steam ID <code>{sid}</code> привязан!")
        except Exception as e:
            await tg_send(chat_id, f"❌ Ошибка: {e}")

    elif text == "/unlink":
        try: unlink_steam(chat_id); await tg_send(chat_id, "✅ Steam отвязан.")
        except Exception as e: await tg_send(chat_id, f"❌ Ошибка: {e}")

    elif text in ("/stats","Моя статистика"):
        user = get_user(chat_id)
        if not user or not user.get("steam_id"):
            await tg_send(chat_id, "❌ Сначала привяжи Steam:\n<code>привязать 105248644</code>"); return {"ok":True}
        await tg_send(chat_id, "🔍 Загружаю...")
        try:
            result = await _load_player(str(user["steam_id"]))
            await tg_send(chat_id, format_player_message(result),
                          reply_markup=webapp_btn("📊 Подробный анализ",
                          f"{WEBAPP_URL}?player_id={user['steam_id']}" if WEBAPP_URL else None))
        except Exception as e: await tg_send(chat_id, f"❌ Ошибка: {e}")

    elif text in ("/profile","Профиль"):
        user = get_user(chat_id)
        if not user: await tg_send(chat_id, "❌ Используй /start"); return {"ok":True}
        await tg_send(chat_id, (f"👤 <b>Профиль</b>\n\n"
            f"🎮 Steam: {steam_str}\n"
            f"💰 Монеты: {user.get('coins',0)}\n"
            f"⭐ Уровень: {user.get('level',1)} (XP: {user.get('xp',0)})"))

    elif text in ("/missions","Миссии"):
        user = get_user(chat_id)
        if not user or not user.get("steam_id"):
            await tg_send(chat_id, "❌ Сначала привяжи Steam:\n<code>привязать 105248644</code>"); return {"ok":True}
        try:
            assign_user_missions(chat_id)
            try:
                player_data = await _load_player(str(user["steam_id"]))
                update_mission_progress(chat_id, player_data)
            except Exception as e:
                logger.warning(f"Mission progress update failed: {e}")
            missions = get_user_missions(chat_id)
            if not missions:
                await tg_send(chat_id, "📭 Нет активных миссий."); return {"ok":True}
            lines = []
            for m in missions:
                done = "✅" if m["completed"] else "⏳"
                pct  = min(100, int(m["progress"] / max(m["target_value"],1) * 100))
                claim_text = f"\n   💡 Используй: <code>забрать {m['id']}</code>" if m["completed"] and not m["claimed"] else ""
                lines.append(f"{done} {m['icon']} <b>{m['title']}</b>\n   {m['description']}\n   Прогресс: {m['progress']}/{m['target_value']} ({pct}%)\n   Награда: 💰{m['reward_coins']} ⭐{m['reward_xp']}{claim_text}")
            await tg_send(chat_id, "🎯 <b>Твои миссии</b>\n\n" + "\n\n".join(lines))
        except Exception as e: await tg_send(chat_id, f"❌ Ошибка: {e}")

    elif text.lower().startswith("забрать "):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await tg_send(chat_id, "❌ Формат: <code>забрать 1</code>"); return {"ok":True}
        mission_id = int(parts[1])
        try:
            conn = get_db_connection(); c = conn.cursor()
            c.execute("""
                SELECT um.id, um.completed, um.claimed, um.progress,
                       m.reward_coins, m.reward_xp, m.title, m.target_value
                FROM user_missions um JOIN missions m ON um.mission_id = m.id
                WHERE um.id = %s AND um.telegram_id = %s
            """, (mission_id, chat_id))
            mission = c.fetchone()
            if not mission:
                conn.close(); await tg_send(chat_id, "❌ Миссия не найдена"); return {"ok":True}
            if mission["claimed"]:
                conn.close(); await tg_send(chat_id, "❌ Награда уже получена"); return {"ok":True}
            is_completed = bool(mission["completed"]) or (int(mission["progress"]) >= int(mission["target_value"]))
            if not is_completed:
                conn.close(); await tg_send(chat_id, "❌ Миссия ещё не выполнена"); return {"ok":True}
            if not bool(mission["completed"]):
                c.execute("UPDATE user_missions SET completed=TRUE, completed_at=CURRENT_TIMESTAMP WHERE id=%s", (mission_id,))
            c.execute("UPDATE user_missions SET claimed=TRUE WHERE id=%s", (mission_id,))
            c.execute("UPDATE users SET coins=coins+%s, xp=xp+%s WHERE telegram_id=%s",
                      (mission["reward_coins"], mission["reward_xp"], chat_id))
            c.execute("INSERT INTO transactions (telegram_id,type,amount,description) VALUES(%s,'earn',%s,%s)",
                      (chat_id, mission["reward_coins"], f"Миссия: {mission['title']}"))
            conn.commit()
            c.execute("SELECT coins, xp, level FROM users WHERE telegram_id=%s", (chat_id,))
            user = c.fetchone(); conn.close()
            await tg_send(chat_id, f"🎉 <b>Награда получена!</b>\n\n💰 +{mission['reward_coins']} монет\n⭐ +{mission['reward_xp']} XP\n\nБаланс: 💰{user['coins']} | ⭐ Уровень {user['level']}")
        except Exception as e:
            logger.error(f"Claim error: {e}"); await tg_send(chat_id, f"❌ Ошибка: {e}")

    elif text in ("/shop","Магазин"):
        user = get_user(chat_id)
        coins = user.get("coins",0) if user else 0
        try:
            items = get_shop_items()
            if not items: await tg_send(chat_id, "🛒 Магазин пуст."); return {"ok":True}
            lines = [f"🛒 <b>Магазин</b> | Баланс: 💰{coins}\n"]
            for item in items[:10]:
                can = "✅" if coins >= item["price"] else "❌"
                lines.append(f"{can} {item['icon']} <b>{item['name']}</b> — {item['price']}💰\n   {item['description']}")
            lines.append("\n💡 Для покупки: <code>купить ID</code>")
            await tg_send(chat_id, "\n".join(lines))
        except Exception as e: await tg_send(chat_id, f"❌ Ошибка: {e}")

    elif text.lower().startswith("купить "):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await tg_send(chat_id, "❌ Формат: <code>купить 1</code>"); return {"ok":True}
        try:
            result = buy_item(chat_id, int(parts[1]))
            await tg_send(chat_id, f"✅ Куплено: <b>{result['item_name']}</b>!\nОсталось: 💰{result['coins_left']}")
        except Exception as e: await tg_send(chat_id, f"❌ {e}")

    elif text == "/help":
        await tg_send(chat_id, ("📖 <b>Команды:</b>\n\n"
            "/start — меню\n/stats — статистика\n/profile — профиль\n/missions — миссии\n/shop — магазин\n/unlink — отвязать Steam\n\n"
            "🔗 Привязка: <code>привязать 105248644</code>\n"
            "💡 Забрать миссию: <code>забрать ID</code>"))

    elif text.startswith("/"):
        await tg_send(chat_id, "❓ Неизвестная команда. /help")

    else:
        await tg_send(chat_id, f"🔍 Ищу <b>{text}</b>...")
        try:
            query = text
            if query.isdigit():
                q_int = int(query)
                account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
            else:
                results = await search_combined(query)
                if not results: await tg_send(chat_id, "❌ Игрок не найден."); return {"ok":True}
                account_id = results[0]["account_id"]
            result = await _load_player(str(account_id))
            await tg_send(chat_id, format_player_message(result),
                          reply_markup=webapp_btn("📊 Подробный анализ",
                          f"{WEBAPP_URL}?player_id={account_id}" if WEBAPP_URL else None))
        except Exception as e:
            logger.error(f"Webhook error: {e}"); await tg_send(chat_id, "⚠️ Ошибка загрузки. Попробуй позже.")

    return {"ok": True}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 8000)))
