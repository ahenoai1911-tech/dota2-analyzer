import logging
import time
import asyncio
import os
import httpx
import sqlite3
import random
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

# ── ENV ──────────────────────────────────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN", "")
WEBAPP_URL       = os.getenv("WEBAPP_URL", "")
STRATZ_TOKEN     = os.getenv("STRATZ_TOKEN", "")
ANTHROPIC_API_KEY= os.getenv("ANTHROPIC_API_KEY", "")

OPENDOTA_BASE = "https://api.opendota.com/api"
STRATZ_GQL    = "https://api.stratz.com/graphql"
STRATZ_BASE   = "https://api.stratz.com/api/v1"

# ── DATABASE ─────────────────────────────────────────────────────────────────
DB_PATH = "dota_analyzer.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            steam_id INTEGER,
            username TEXT,
            coins INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            premium_until TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Missions table
    c.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    
    # User missions progress
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            mission_id INTEGER NOT NULL,
            progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
            FOREIGN KEY (mission_id) REFERENCES missions(id)
        )
    """)
    
    # Shop items
    c.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            type TEXT NOT NULL,
            price INTEGER NOT NULL,
            icon TEXT DEFAULT '🎁',
            data TEXT
        )
    """)
    
    # User inventory
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            acquired_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
            FOREIGN KEY (item_id) REFERENCES shop_items(id)
        )
    """)
    
    # Transactions log
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )
    """)
    
    conn.commit()
    
    # Insert default missions if empty
    c.execute("SELECT COUNT(*) FROM missions")
    if c.fetchone()[0] == 0:
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
    c.execute("SELECT COUNT(*) FROM shop_items")
    if c.fetchone()[0] == 0:
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

# ── USER MANAGEMENT ──────────────────────────────────────────────────────────
def get_or_create_user(telegram_id: int, username: str = None, steam_id: int = None) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = c.fetchone()
    
    if not user:
        c.execute("""
            INSERT INTO users (telegram_id, username, steam_id, coins, xp, level)
            VALUES (?, ?, ?, 0, 0, 1)
        """, (telegram_id, username, steam_id))
        conn.commit()
        c.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        user = c.fetchone()
    else:
        # Update last seen
        c.execute("UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE telegram_id = ?", (telegram_id,))
        conn.commit()
    
    conn.close()
    
    return {
        "telegram_id": user[0],
        "steam_id": user[1],
        "username": user[2],
        "coins": user[3],
        "xp": user[4],
        "level": user[5],
        "premium_until": user[6],
        "created_at": user[7],
        "last_seen": user[8]
    }

def is_premium(user: dict) -> bool:
    if not user.get("premium_until"):
        return False
    premium_until = datetime.fromisoformat(user["premium_until"])
    return datetime.now() < premium_until

def add_coins(telegram_id: int, amount: int, description: str = ""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check if user is premium
    c.execute("SELECT coins, premium_until FROM users WHERE telegram_id = ?", (telegram_id,))
    result = c.fetchone()
    current_coins = result[0]
    premium_until = result[1]
    
    # Check coin limit for non-premium users
    is_premium_user = premium_until and datetime.fromisoformat(premium_until) > datetime.now()
    if not is_premium_user and current_coins >= 1000:
        new_coins = 1000  # Cap at 1000 for free users
    else:
        new_coins = current_coins + amount
        if not is_premium_user and new_coins > 1000:
            new_coins = 1000
    
    c.execute("UPDATE users SET coins = ? WHERE telegram_id = ?", (new_coins, telegram_id))
    c.execute("""
        INSERT INTO transactions (telegram_id, type, amount, description)
        VALUES (?, 'earn', ?, ?)
    """, (telegram_id, amount, description))
    conn.commit()
    conn.close()
    
    return new_coins

def add_xp(telegram_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT xp, level FROM users WHERE telegram_id = ?", (telegram_id,))
    result = c.fetchone()
    current_xp = result[0]
    current_level = result[1]
    
    new_xp = current_xp + amount
    new_level = current_level
    
    # Level up calculation (1000 XP per level)
    while new_xp >= new_level * 1000:
        new_xp -= new_level * 1000
        new_level += 1
        # Reward coins on level up
        add_coins(telegram_id, new_level * 50, f"Level {new_level} reward")
    
    c.execute("UPDATE users SET xp = ?, level = ? WHERE telegram_id = ?", (new_xp, new_level, telegram_id))
    conn.commit()
    conn.close()
    
    return {"level": new_level, "xp": new_xp}

def link_steam_account(telegram_id: int, steam_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET steam_id = ? WHERE telegram_id = ?", (steam_id, telegram_id))
    conn.commit()
    conn.close()

# ── MISSIONS ─────────────────────────────────────────────────────────────────
def assign_daily_missions(telegram_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check if user is premium
    c.execute("SELECT premium_until FROM users WHERE telegram_id = ?", (telegram_id,))
    result = c.fetchone()
    is_premium_user = result and result[0] and datetime.fromisoformat(result[0]) > datetime.now()
    
    # Get all daily missions
    c.execute("SELECT id FROM missions WHERE type = 'daily'")
    all_daily = [row[0] for row in c.fetchall()]
    
    # Check existing missions for today
    c.execute("""
        SELECT mission_id FROM user_missions 
        WHERE telegram_id = ? AND DATE(assigned_at) = DATE('now')
    """, (telegram_id,))
    existing = [row[0] for row in c.fetchall()]
    
    if existing:
        conn.close()
        return  # Already assigned today
    
    # Assign missions
    num_missions = len(all_daily) if is_premium_user else 1
    selected = random.sample(all_daily, min(num_missions, len(all_daily)))
    
    for mission_id in selected:
        c.execute("""
            INSERT INTO user_missions (telegram_id, mission_id, progress, completed, claimed)
            VALUES (?, ?, 0, 0, 0)
        """, (telegram_id, mission_id))
    
    conn.commit()
    conn.close()

def assign_weekly_missions(telegram_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT premium_until FROM users WHERE telegram_id = ?", (telegram_id,))
    result = c.fetchone()
    is_premium_user = result and result[0] and datetime.fromisoformat(result[0]) > datetime.now()
    
    c.execute("SELECT id FROM missions WHERE type = 'weekly'")
    all_weekly = [row[0] for row in c.fetchall()]
    
    # Check if already assigned this week
    c.execute("""
        SELECT mission_id FROM user_missions 
        WHERE telegram_id = ? AND DATE(assigned_at) >= DATE('now', '-7 days')
        AND mission_id IN (SELECT id FROM missions WHERE type = 'weekly')
    """, (telegram_id,))
    existing = [row[0] for row in c.fetchall()]
    
    if existing:
        conn.close()
        return
    
    num_missions = len(all_weekly) if is_premium_user else 1
    selected = random.sample(all_weekly, min(num_missions, len(all_weekly)))
    
    for mission_id in selected:
        c.execute("""
            INSERT INTO user_missions (telegram_id, mission_id, progress, completed, claimed)
            VALUES (?, ?, 0, 0, 0)
        """, (telegram_id, mission_id))
    
    conn.commit()
    conn.close()

def assign_monthly_missions(telegram_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT premium_until FROM users WHERE telegram_id = ?", (telegram_id,))
    result = c.fetchone()
    is_premium_user = result and result[0] and datetime.fromisoformat(result[0]) > datetime.now()
    
    c.execute("SELECT id FROM missions WHERE type = 'monthly'")
    all_monthly = [row[0] for row in c.fetchall()]
    
    c.execute("""
        SELECT mission_id FROM user_missions 
        WHERE telegram_id = ? AND DATE(assigned_at) >= DATE('now', '-30 days')
        AND mission_id IN (SELECT id FROM missions WHERE type = 'monthly')
    """, (telegram_id,))
    existing = [row[0] for row in c.fetchall()]
    
    if existing:
        conn.close()
        return
    
    num_missions = len(all_monthly) if is_premium_user else 1
    selected = random.sample(all_monthly, min(num_missions, len(all_monthly)))
    
    for mission_id in selected:
        c.execute("""
            INSERT INTO user_missions (telegram_id, mission_id, progress, completed, claimed)
            VALUES (?, ?, 0, 0, 0)
        """, (telegram_id, mission_id))
    
    conn.commit()
    conn.close()

def get_user_missions(telegram_id: int) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("""
        SELECT um.id, m.type, m.title, m.description, m.requirement, m.target_value,
               m.reward_coins, m.reward_xp, m.icon, um.progress, um.completed, um.claimed
        FROM user_missions um
        JOIN missions m ON um.mission_id = m.id
        WHERE um.telegram_id = ? AND um.claimed = 0
        ORDER BY um.completed DESC, m.type, um.assigned_at
    """, (telegram_id,))
    
    missions = []
    for row in c.fetchall():
        missions.append({
            "id": row[0],
            "type": row[1],
            "title": row[2],
            "description": row[3],
            "requirement": row[4],
            "target_value": row[5],
            "reward_coins": row[6],
            "reward_xp": row[7],
            "icon": row[8],
            "progress": row[9],
            "completed": row[10],
            "claimed": row[11]
        })
    
    conn.close()
    return missions

def update_mission_progress(telegram_id: int, player_data: dict):
    """Update mission progress based on player stats"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get active missions
    missions = get_user_missions(telegram_id)
    
    for mission in missions:
        if mission["completed"]:
            continue
        
        req = mission["requirement"]
        target = mission["target_value"]
        current_progress = mission["progress"]
        
        # Calculate progress based on requirement type
        new_progress = current_progress
        
        if req == "wins":
            new_progress = player_data.get("stats", {}).get("wins", 0)
        elif req == "matches":
            new_progress = player_data.get("stats", {}).get("total_matches", 0)
        elif req == "winrate":
            new_progress = int(player_data.get("stats", {}).get("winrate", 0))
        elif req == "avg_kda":
            trend = player_data.get("trend", {})
            new_progress = int(trend.get("last20_avg_kda", 0) * 10)  # Store as int
        
        # Check if completed
        if new_progress >= target:
            c.execute("""
                UPDATE user_missions 
                SET progress = ?, completed = 1, completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_progress, mission["id"]))
        else:
            c.execute("UPDATE user_missions SET progress = ? WHERE id = ?", (new_progress, mission["id"]))
    
    conn.commit()
    conn.close()

def claim_mission_reward(telegram_id: int, mission_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("""
        SELECT um.completed, m.reward_coins, m.reward_xp
        FROM user_missions um
        JOIN missions m ON um.mission_id = m.id
        WHERE um.id = ? AND um.telegram_id = ? AND um.claimed = 0
    """, (mission_id, telegram_id))
    
    result = c.fetchone()
    if not result or not result[0]:
        conn.close()
        raise HTTPException(status_code=400, detail="Mission not completed or already claimed")
    
    reward_coins = result[1]
    reward_xp = result[2]
    
    # Mark as claimed
    c.execute("UPDATE user_missions SET claimed = 1 WHERE id = ?", (mission_id,))
    conn.commit()
    conn.close()
    
    # Add rewards
    add_coins(telegram_id, reward_coins, "Mission reward")
    xp_result = add_xp(telegram_id, reward_xp)
    
    return {
        "coins": reward_coins,
        "xp": reward_xp,
        "new_level": xp_result["level"]
    }

# ── STRATZ ───────────────────────────────────────────────────────────────────
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

# ── OPENDOTA ─────────────────────────────────────────────────────────────────
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

# ── ANALYSIS ─────────────────────────────────────────────────────────────────
def calc_kda(kills, deaths, assists):
    return round((kills + assists) / max(deaths, 1), 2)

def rank_tier_to_name(rank_tier) -> str:
    if rank_tier is None:
        return "Uncalibrated"
    tiers = {1:"Herald",2:"Guardian",3:"Crusader",4:"Archon",5:"Legend",6:"Ancient",7:"Divine",8:"Immortal"}
    tier  = int(str(rank_tier)[0]) if rank_tier else 0
    star  = int(str(rank_tier)[-1]) if rank_tier and len(str(rank_tier)) > 1 else 0
    name  = tiers.get(tier, "Unknown")
    return f"{name} {'★' * star}" if star else name

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

# ── SEARCH ───────────────────────────────────────────────────────────────────
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

# ── ENDPOINTS ─────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "message": "Dota 2 Analyzer API v2.1"}

# ── USER ENDPOINTS ───────────────────────────────────────────────────────────
@app.get("/user/profile")
async def get_user_profile(telegram_id: int = Query(...)):
    user = get_or_create_user(telegram_id)
    return {
        "user": user,
        "is_premium": is_premium(user),
        "coin_limit": None if is_premium(user) else 1000
    }

@app.post("/user/link_steam")
async def link_steam(telegram_id: int, steam_id: int):
    link_steam_account(telegram_id, steam_id)
    return {"status": "ok", "message": "Steam account linked"}

@app.post("/user/subscribe")
async def subscribe_premium(telegram_id: int, stars_paid: int):
    """Handle Telegram Stars payment for premium subscription"""
    if stars_paid < 129:
        raise HTTPException(status_code=400, detail="Insufficient Stars payment")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Add 30 days of premium
    c.execute("SELECT premium_until FROM users WHERE telegram_id = ?", (telegram_id,))
    result = c.fetchone()
    
    if result and result[0]:
        current_premium = datetime.fromisoformat(result[0])
        if current_premium > datetime.now():
            new_premium = current_premium + timedelta(days=30)
        else:
            new_premium = datetime.now() + timedelta(days=30)
    else:
        new_premium = datetime.now() + timedelta(days=30)
    
    c.execute("UPDATE users SET premium_until = ? WHERE telegram_id = ?", 
              (new_premium.isoformat(), telegram_id))
    conn.commit()
    conn.close()
    
    return {
        "status": "ok",
        "premium_until": new_premium.isoformat(),
        "message": "Premium activated for 30 days"
    }

# ── MISSIONS ENDPOINTS ───────────────────────────────────────────────────────
@app.get("/missions")
async def get_missions(telegram_id: int = Query(...)):
    # Ensure user exists
    get_or_create_user(telegram_id)
    
    # Assign missions if needed
    assign_daily_missions(telegram_id)
    assign_weekly_missions(telegram_id)
    assign_monthly_missions(telegram_id)
    
    missions = get_user_missions(telegram_id)
    return {"missions": missions}

@app.post("/missions/claim")
async def claim_mission(telegram_id: int, mission_id: int):
    reward = claim_mission_reward(telegram_id, mission_id)
    user = get_or_create_user(telegram_id)
    return {
        "status": "ok",
        "reward": reward,
        "user": user
    }

@app.post("/missions/update")
async def update_missions(telegram_id: int, player_data: dict):
    """Update mission progress based on player stats"""
    update_mission_progress(telegram_id, player_data)
    missions = get_user_missions(telegram_id)
    return {"missions": missions}

# ── SHOP ENDPOINTS ───────────────────────────────────────────────────────────
@app.get("/shop")
async def get_shop():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT * FROM shop_items ORDER BY type, price")
    items = []
    for row in c.fetchall():
        items.append({
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "type": row[3],
            "price": row[4],
            "icon": row[5],
            "data": row[6]
        })
    
    conn.close()
    return {"items": items}

@app.post("/shop/buy")
async def buy_item(telegram_id: int, item_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get item
    c.execute("SELECT name, price, type FROM shop_items WHERE id = ?", (item_id,))
    item = c.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Item not found")
    
    item_name, price, item_type = item
    
    # Get user coins
    c.execute("SELECT coins FROM users WHERE telegram_id = ?", (telegram_id,))
    user = c.fetchone()
    if not user or user[0] < price:
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient coins")
    
    # Deduct coins
    new_coins = user[0] - price
    c.execute("UPDATE users SET coins = ? WHERE telegram_id = ?", (new_coins, telegram_id))
    
    # Add to inventory
    c.execute("""
        INSERT INTO user_inventory (telegram_id, item_id, quantity)
        VALUES (?, ?, 1)
        ON CONFLICT(telegram_id, item_id) DO UPDATE SET quantity = quantity + 1
    """, (telegram_id, item_id))
    
    # Log transaction
    c.execute("""
        INSERT INTO transactions (telegram_id, type, amount, description)
        VALUES (?, 'spend', ?, ?)
    """, (telegram_id, -price, f"Bought {item_name}"))
    
    conn.commit()
    conn.close()
    
    return {
        "status": "ok",
        "item": item_name,
        "coins_left": new_coins
    }

@app.get("/shop/inventory")
async def get_inventory(telegram_id: int = Query(...)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("""
        SELECT si.name, si.type, si.icon, ui.quantity, ui.acquired_at
        FROM user_inventory ui
        JOIN shop_items si ON ui.item_id = si.id
        WHERE ui.telegram_id = ?
        ORDER BY ui.acquired_at DESC
    """, (telegram_id,))
    
    inventory = []
    for row in c.fetchall():
        inventory.append({
            "name": row[0],
            "type": row[1],
            "icon": row[2],
            "quantity": row[3],
            "acquired_at": row[4]
        })
    
    conn.close()
    return {"inventory": inventory}

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

# ── AI ENDPOINT ───────────────────────────────────────────────────────────────
class AIRequest(BaseModel):
    message: str
    player_context: str = ""
    history: list = []

@app.post("/ai")
async def ai_chat(req: AIRequest):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="AI not configured. Set ANTHROPIC_API_KEY in Railway.")

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
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1000,
                    "system": system,
                    "messages": messages,
                }
            )
        if not r.is_success:
            logger.error(f"Anthropic error: {r.status_code} {r.text}")
            raise HTTPException(status_code=502, detail="AI request failed")

        data = r.json()
        text = "".join(c.get("text", "") for c in data.get("content", []))
        return {"reply": text}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
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
        streak_str = f"\n{emoji} Streak: {streak['count']} {'wins' if streak['type']=='win' else 'losses'} in a row"

    heroes_lines = ""
    for i, h in enumerate(data.get("top_heroes", [])[:5], 1):
        name = h.get("hero_name") or f"Hero#{h.get('hero_id','?')}"
        heroes_lines += f"  {i}. {name} — {h['matches']}g, WR {h['winrate']}%, KDA {h['kda']}\n"

    matches_lines = ""
    for m in data.get("recent_matches", [])[:5]:
        hero   = m.get("hero") or f"Hero#{m.get('hero_id','?')}"
        result = "✅" if m["win"] else "❌"
        matches_lines += f"  {result} {hero} — {m['kills']}/{m['deaths']}/{m['assists']} ({m['duration_min']}:{m['duration_sec']:02d})\n"

    mmr_str = ""
    if p.get("mmr_estimate"):
        mmr_str = f"\n📊 MMR: ~{p['mmr_estimate']}"

    return (
        f"{src_icon} <b>{p['name']}</b>{anon}\n"
        f"🏅 Rank: {p.get('rank','?')}{mmr_str}\n"
        f"📈 Winrate: {s['winrate']}% ({s['wins']}W / {s['losses']}L)\n"
        f"🎮 Total matches: {s['total_matches']}\n"
        f"\n📊 Trends:\n"
        f"  Last 5:  WR {t.get('last5_winrate','?')}%, KDA {t.get('last5_avg_kda','?')}\n"
        f"  Last 20: WR {t.get('last20_winrate','?')}%, GPM {t.get('last20_avg_gpm','?')}\n"
        f"{streak_str}\n"
        f"\n🦸 Top heroes:\n{heroes_lines}"
        f"\n🕹 Recent matches:\n{matches_lines}"
        f"\n🔗 <a href='https://stratz.com/players/{data['account_id']}'>Open on Stratz</a>"
    )

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    if "message" not in data:
        return {"ok": True}

    chat_id = data["message"]["chat"]["id"]
    text    = data["message"].get("text", "").strip()
    username = data["message"]["from"].get("username", "")

    # Проверяем есть ли пользователь
    user = get_or_create_user(chat_id, username)
    
    # Главная клавиатура (кнопки снизу)
    def get_main_keyboard():
        return {
            "keyboard": [
                ["📊 Моя статистика", "🎯 Миссии"],
                ["🛒 Магазин", "👤 Профиль"],
                ["⭐ Premium", "❓ Помощь"]
            ],
            "resize_keyboard": True,
            "persistent": True
        }

    if text == "/start":
        # Проверяем привязан ли Steam
        if not user["steam_id"]:
            await tg_send(
                chat_id,
                "👋 <b>Добро пожаловать в Dota 2 Analyzer!</b>\n\n"
                "Для начала работы привяжите ваш Steam аккаунт.\n\n"
                "📝 <b>Отправьте мне:</b>\n"
                "• Ваш Steam ID (например: 105248644)\n"
                "• Или Steam64 ID (например: 76561198065514372)\n"
                "• Или ваш никнейм в Dota 2\n\n"
                "После привязки вы получите доступ ко всем функциям бота!"
            )
        else:
            # Уже авторизован - показываем статистику
            await show_user_stats(chat_id, user["steam_id"])
            await tg_send(
                chat_id,
                "✅ Вы уже авторизованы!\n\nИспользуйте кнопки ниже для навигации 👇",
                reply_markup=get_main_keyboard()
            )

    elif text == "📊 Моя статистика" or text == "/stats":
        if not user["steam_id"]:
            await tg_send(chat_id, "❌ Сначала привяжите Steam аккаунт!\nОтправьте /start")
            return {"ok": True}
        
        await show_user_stats(chat_id, user["steam_id"])

    elif text == "🎯 Миссии" or text == "/missions":
        if not user["steam_id"]:
            await tg_send(chat_id, "❌ Сначала привяжите Steam аккаунт!\nОтправьте /start")
            return {"ok": True}
        
        # Назначаем миссии
        assign_daily_missions(chat_id)
        assign_weekly_missions(chat_id)
        assign_monthly_missions(chat_id)
        
        missions = get_user_missions(chat_id)
        if not missions:
            await tg_send(
                chat_id, 
                "📭 У вас пока нет активных миссий.\n\nСыграйте несколько игр, и миссии появятся!",
                reply_markup=get_main_keyboard()
            )
            return {"ok": True}
        
        msg = "🎯 <b>Ваши миссии</b>\n\n"
        
        # Группируем по типам
        daily = [m for m in missions if m["type"] == "daily"]
        weekly = [m for m in missions if m["type"] == "weekly"]
        monthly = [m for m in missions if m["type"] == "monthly"]
        
        if daily:
            msg += "📅 <b>Ежедневные:</b>\n"
            for m in daily:
                status = "✅" if m["completed"] else "⏳"
                progress = f"{m['progress']}/{m['target_value']}"
                msg += f"{status} {m['icon']} {m['title']}\n"
                msg += f"   {m['description']}\n"
                msg += f"   Прогресс: {progress}\n"
                msg += f"   Награда: {m['reward_coins']}💰 {m['reward_xp']}⭐\n\n"
        
        if weekly:
            msg += "📆 <b>Недельные:</b>\n"
            for m in weekly:
                status = "✅" if m["completed"] else "⏳"
                progress = f"{m['progress']}/{m['target_value']}"
                msg += f"{status} {m['icon']} {m['title']}\n"
                msg += f"   {m['description']}\n"
                msg += f"   Прогресс: {progress}\n"
                msg += f"   Награда: {m['reward_coins']}💰 {m['reward_xp']}⭐\n\n"
        
        if monthly:
            msg += "📊 <b>Месячные:</b>\n"
            for m in monthly:
                status = "✅" if m["completed"] else "⏳"
                progress = f"{m['progress']}/{m['target_value']}"
                msg += f"{status} {m['icon']} {m['title']}\n"
                msg += f"   {m['description']}\n"
                msg += f"   Прогресс: {progress}\n"
                msg += f"   Награда: {m['reward_coins']}💰 {m['reward_xp']}⭐\n\n"
        
        if not is_premium(user):
            msg += "\n💎 <b>Premium открывает ВСЕ миссии!</b>\nНажмите ⭐ Premium"
        
        await tg_send(chat_id, msg, reply_markup=get_main_keyboard())

    elif text == "🛒 Магазин" or text == "/shop":
        if not user["steam_id"]:
            await tg_send(chat_id, "❌ Сначала привяжите Steam аккаунт!\nОтправьте /start")
            return {"ok": True}
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT * FROM shop_items ORDER BY type, price LIMIT 12")
        items = c.fetchall()
        conn.close()
        
        msg = f"🛒 <b>Магазин</b>\n\n💰 Ваш баланс: {user['coins']} монет\n\n"
        
        # Группируем по типам
        boosters = [i for i in items if 'booster' in i[3]]
        cosmetics = [i for i in items if 'cosmetic' in i[3]]
        special = [i for i in items if 'special' in i[3]]
        
        if boosters:
            msg += "⚡ <b>Бустеры:</b>\n"
            for item in boosters:
                msg += f"{item[5]} <b>{item[1]}</b> - {item[4]}💰\n"
                msg += f"   {item[2]}\n\n"
        
        if cosmetics:
            msg += "🎨 <b>Косметика:</b>\n"
            for item in cosmetics:
                msg += f"{item[5]} <b>{item[1]}</b> - {item[4]}💰\n"
                msg += f"   {item[2]}\n\n"
        
        if special:
            msg += "🎁 <b>Специальное:</b>\n"
            for item in special:
                msg += f"{item[5]} <b>{item[1]}</b> - {item[4]}💰\n"
                msg += f"   {item[2]}\n\n"
        
        msg += "💡 Откройте Web App для покупки предметов!"
        
        # Добавляем кнопку Web App
        keyboard = get_main_keyboard()
        if WEBAPP_URL:
            keyboard["inline_keyboard"] = [[
                {"text": "🛒 Открыть магазин", "web_app": {"url": f"{WEBAPP_URL}?tab=shop&telegram_id={chat_id}"}}
            ]]
        
        await tg_send(chat_id, msg, reply_markup=keyboard)

    elif text == "👤 Профиль" or text == "/profile":
        if not user["steam_id"]:
            await tg_send(chat_id, "❌ Сначала привяжите Steam аккаунт!\nОтправьте /start")
            return {"ok": True}
        
        premium = is_premium(user)
        premium_text = f"✅ Активна до {user['premium_until'][:10]}" if premium else "❌ Не активна"
        
        # Считаем выполненные миссии
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM user_missions WHERE telegram_id = ? AND completed = 1", (chat_id,))
        completed_missions = c.fetchone()[0]
        conn.close()
        
        msg = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"🆔 Steam ID: {user['steam_id']}\n"
            f"💰 Монеты: {user['coins']}/{'∞' if premium else '1000'}\n"
            f"⭐ Уровень: {user['level']}\n"
            f"📊 Опыт: {user['xp']}/{user['level'] * 1000}\n"
            f"🎯 Выполнено миссий: {completed_missions}\n"
            f"👑 Premium: {premium_text}\n\n"
        )
        
        if not premium:
            msg += "💎 Хотите больше миссий и безлимитные монеты?\nНажмите ⭐ Premium"
        
        await tg_send(chat_id, msg, reply_markup=get_main_keyboard())

    elif text == "⭐ Premium" or text == "/premium":
        premium = is_premium(user)
        if premium:
            await tg_send(
                chat_id,
                f"✅ <b>У вас уже есть Premium!</b>\n\n"
                f"Активна до: {user['premium_until'][:10]}\n\n"
                f"Преимущества:\n"
                f"• 🎯 Все миссии (15 вместо 3)\n"
                f"• 💰 Безлимитные монеты\n"
                f"• 🎁 Эксклюзивные предметы",
                reply_markup=get_main_keyboard()
            )
        else:
            keyboard = {
                "inline_keyboard": [[
                    {"text": "⭐ Купить Premium (129 Stars)", "callback_data": "buy_premium"}
                ]]
            }
            await tg_send(
                chat_id,
                "💎 <b>Premium подписка</b>\n\n"
                "<b>Преимущества:</b>\n"
                "• 🎯 Доступ ко ВСЕМ миссиям (15 вместо 3)\n"
                "• 💰 Безлимитное хранение монет\n"
                "• 🚀 Эксклюзивные бустеры\n"
                "• 🎁 Ежемесячные награды\n\n"
                "💵 Цена: 129 Telegram Stars\n"
                "⏱ Длительность: 30 дней",
                reply_markup=keyboard
            )

    elif text == "❓ Помощь" or text == "/help":
        msg = (
            "📖 <b>Как пользоваться ботом:</b>\n\n"
            "📊 <b>Моя статистика</b> - ваша статистика в Dota 2\n"
            "🎯 <b>Миссии</b> - активные задания и награды\n"
            "🛒 <b>Магазин</b> - покупка бустеров и косметики\n"
            "👤 <b>Профиль</b> - ваш профиль и прогресс\n"
            "⭐ <b>Premium</b> - информация о подписке\n\n"
            "<b>Как работают миссии:</b>\n"
            "1. Играйте в Dota 2\n"
            "2. Бот автоматически отслеживает прогресс\n"
            "3. Выполняйте миссии и получайте награды\n"
            "4. Тратьте монеты в магазине\n\n"
            "<b>Как привязать другой аккаунт:</b>\n"
            "Отправьте новый Steam ID или никнейм"
        )
        await tg_send(chat_id, msg, reply_markup=get_main_keyboard())

    elif text.startswith("/"):
        await tg_send(
            chat_id, 
            "❓ Неизвестная команда.\nИспользуйте кнопки ниже 👇",
            reply_markup=get_main_keyboard()
        )

    else:
        # Пользователь отправил текст - пытаемся найти игрока
        await tg_send(chat_id, f"🔍 Ищу игрока <b>{text}</b>...")
        try:
            query = text
            if query.isdigit():
                q_int = int(query)
                account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
            else:
                results = await search_combined(query)
                if not results:
                    await tg_send(
                        chat_id, 
                        "❌ Игрок не найден.\n\nПроверьте правильность никнейма или Steam ID.",
                        reply_markup=get_main_keyboard()
                    )
                    return {"ok": True}
                account_id = results[0]["account_id"]

            result = get_cache(f"player:{query}")
            if not result:
                if STRATZ_TOKEN:
                    stratz_data = await stratz_player(account_id)
                    if stratz_data:
                        result = build_from_stratz(stratz_data, account_id)
                if not result:
                    player, wl, matches, heroes = await asyncio.gather(
                        od_player(account_id), od_wl(account_id),
                        od_matches(account_id), od_heroes(account_id),
                    )
                    if not player:
                        await tg_send(
                            chat_id, 
                            "❌ Профиль не найден или приватный.",
                            reply_markup=get_main_keyboard()
                        )
                        return {"ok": True}
                    result = build_from_opendota(player, wl, matches, heroes, account_id)
                set_cache(f"player:{query}", result)

            # Привязываем Steam аккаунт
            link_steam_account(chat_id, account_id)
            
            # Обновляем прогресс миссий
            update_mission_progress(chat_id, result)
            
            # Показываем статистику
            msg = format_player_message(result)
            
            keyboard = get_main_keyboard()
            if WEBAPP_URL:
                keyboard["inline_keyboard"] = [[
                    {"text": "📊 Полный анализ", "web_app": {"url": f"{WEBAPP_URL}?player_id={account_id}&telegram_id={chat_id}"}}
                ]]
            
            await tg_send(chat_id, msg, reply_markup=keyboard)
            
            # Если это первая привязка - показываем приветствие
            if not user["steam_id"]:
                await tg_send(
                    chat_id,
                    "✅ <b>Steam аккаунт успешно привязан!</b>\n\n"
                    "Теперь вы можете:\n"
                    "• Выполнять миссии и получать награды\n"
                    "• Покупать предметы в магазине\n"
                    "• Отслеживать свой прогресс\n\n"
                    "Используйте кнопки ниже для навигации 👇",
                    reply_markup=get_main_keyboard()
                )

        except Exception as e:
            logger.error(f"Webhook error: {e}")
            await tg_send(
                chat_id, 
                "⚠️ Ошибка при загрузке данных. Попробуйте позже.",
                reply_markup=get_main_keyboard()
            )

    return {"ok": True}

# Функция для показа статистики пользователя
async def show_user_stats(chat_id: int, steam_id: int):
    try:
        result = get_cache(f"player:{steam_id}")
        if not result:
            if STRATZ_TOKEN:
                stratz_data = await stratz_player(steam_id)
                if stratz_data:
                    result = build_from_stratz(stratz_data, steam_id)
            if not result:
                player, wl, matches, heroes = await asyncio.gather(
                    od_player(steam_id), od_wl(steam_id),
                    od_matches(steam_id), od_heroes(steam_id),
                )
                if not player:
                    await tg_send(chat_id, "❌ Не удалось загрузить статистику.")
                    return
                result = build_from_opendota(player, wl, matches, heroes, steam_id)
            set_cache(f"player:{steam_id}", result)
        
        # Обновляем прогресс миссий
        update_mission_progress(chat_id, result)
        
        msg = format_player_message(result)
        
        keyboard = {
            "keyboard": [
                ["📊 Моя статистика", "🎯 Миссии"],
                ["🛒 Магазин", "👤 Профиль"],
                ["⭐ Premium", "❓ Помощь"]
            ],
            "resize_keyboard": True,
            "persistent": True
        }
        
        if WEBAPP_URL:
            keyboard["inline_keyboard"] = [[
                {"text": "📊 Полный анализ", "web_app": {"url": f"{WEBAPP_URL}?player_id={steam_id}&telegram_id={chat_id}"}}
            ]]
        
        await tg_send(chat_id, msg, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Show stats error: {e}")
        await tg_send(chat_id, "⚠️ Ошибка при загрузке статистики.")

# ── CALLBACK QUERY HANDLER ───────────────────────────────────────────────────
@app.post("/webhook/callback")
async def telegram_callback(req: Request):
    """Handle Telegram callback queries (button clicks)"""
    data = await req.json()
    
    if "callback_query" not in data:
        return {"ok": True}
    
    callback = data["callback_query"]
    chat_id = callback["from"]["id"]
    callback_data = callback.get("data", "")
    
    if callback_data == "buy_premium":
        # Send invoice for Telegram Stars payment
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendInvoice"
        invoice_payload = {
            "chat_id": chat_id,
            "title": "Premium Subscription",
            "description": "30 days of Premium access with unlimited missions and coins",
            "payload": f"premium_30d_{chat_id}",
            "provider_token": "",  # Empty for Stars
            "currency": "XTR",  # Telegram Stars
            "prices": [{"label": "Premium 30 days", "amount": 129}]
        }
        
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=invoice_payload)
    
    # Answer callback to remove loading state
    answer_url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(answer_url, json={"callback_query_id": callback["id"]})
    
    return {"ok": True}

# ── PAYMENT HANDLER ──────────────────────────────────────────────────────────
@app.post("/webhook/payment")
async def telegram_payment(req: Request):
    """Handle successful Telegram Stars payments"""
    data = await req.json()
    
    if "pre_checkout_query" in data:
        # Approve pre-checkout
        query_id = data["pre_checkout_query"]["id"]
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"pre_checkout_query_id": query_id, "ok": True})
        return {"ok": True}
    
    if "message" in data and "successful_payment" in data["message"]:
        # Payment successful
        chat_id = data["message"]["chat"]["id"]
        payment = data["message"]["successful_payment"]
        
        if payment["currency"] == "XTR" and payment["total_amount"] == 129:
            # Activate premium
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute("SELECT premium_until FROM users WHERE telegram_id = ?", (chat_id,))
            result = c.fetchone()
            
            if result and result[0]:
                current_premium = datetime.fromisoformat(result[0])
                if current_premium > datetime.now():
                    new_premium = current_premium + timedelta(days=30)
                else:
                    new_premium = datetime.now() + timedelta(days=30)
            else:
                new_premium = datetime.now() + timedelta(days=30)
            
            c.execute("UPDATE users SET premium_until = ? WHERE telegram_id = ?", 
                      (new_premium.isoformat(), chat_id))
            conn.commit()
            conn.close()
            
            await tg_send(
                chat_id,
                "🎉 <b>Premium Activated!</b>\n\n"
                f"Your premium subscription is now active until {new_premium.strftime('%Y-%m-%d')}\n\n"
                "Benefits unlocked:\n"
                "• 🎯 All missions available\n"
                "• 💰 Unlimited coins\n"
                "• 🚀 Exclusive items\n\n"
                "Use /missions to see all your tasks!"
            )
    
    return {"ok": True}
