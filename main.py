# -*- coding: utf-8 -*-
import logging
import time
import asyncio
import os
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import hmac
import hashlib
import urllib.parse
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Dota 2 Analyzer API", version="2.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── ENV ─────────────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
WEBAPP_URL   = os.getenv("WEBAPP_URL", "")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

OPENDOTA_BASE = "https://api.opendota.com/api"
STRATZ_GQL    = "https://api.stratz.com/graphql"
STRATZ_BASE   = "https://api.stratz.com/api/v1"

# ─── CACHE ───────────────────────────────────────────────────────────
cache: Dict[str, Any] = {}
CACHE_TTL = 300
def get_cache(k: str): return cache[k]["data"] if k in cache and time.time() - cache[k]["ts"] < CACHE_TTL else cache.pop(k, None)
def set_cache(k: str, v): cache[k] = {"data": v, "ts": time.time()}
def steam64_to_account_id(s64: int) -> int: return s64 - 76561197960265728

# ─── DATABASE ────────────────────────────────────────────────────────
def get_db():
    if not DATABASE_URL: raise Exception("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id BIGINT PRIMARY KEY, steam_id BIGINT, username TEXT,
        coins INTEGER DEFAULT 0, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
        premium_until TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS missions (
        id SERIAL PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
        requirement TEXT NOT NULL, target_value INTEGER NOT NULL, reward_coins INTEGER NOT NULL,
        reward_xp INTEGER NOT NULL, icon TEXT DEFAULT '🎯')""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_missions (
        id SERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        mission_id INTEGER NOT NULL REFERENCES missions(id), progress INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0, claimed INTEGER DEFAULT 0,
        assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS shop_items (
        id SERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, type TEXT NOT NULL,
        price INTEGER NOT NULL, icon TEXT DEFAULT '🎁', data TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_inventory (
        id SERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        item_id INTEGER NOT NULL REFERENCES shop_items(id), quantity INTEGER DEFAULT 1,
        acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(telegram_id, item_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        type TEXT NOT NULL, amount INTEGER NOT NULL, description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()

    if c.execute("SELECT COUNT(*) FROM missions"): count = c.fetchone()['count']
    if count == 0:
        c.executemany("INSERT INTO missions (type, title, description, requirement, target_value, reward_coins, reward_xp, icon) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", [
            ("daily", "Первая кровь", "Получи First Blood", "first_blood", 1, 50, 100, "🩸"),
            ("daily", "Победная серия", "Выиграй 3 игры подряд", "win_streak", 3, 100, 150, "🔥"),
            ("daily", "Мастер фарма", "Набери 600+ GPM в матче", "gpm", 600, 75, 120, "💰"),
            ("daily", "Безупречная игра", "Сыграй матч с KDA 10+", "kda", 10, 80, 130, "⭐"),
            ("daily", "Командный игрок", "Сделай 20+ ассистов", "assists", 20, 60, 100, "🤝"),
            ("weekly", "Марафонец", "Сыграй 20 матчей", "matches", 20, 300, 500, "🏃"),
            ("weekly", "Универсал", "Сыграй на 10 героях", "unique_heroes", 10, 250, 400, "🎭"),
            ("weekly", "Доминатор", "Выиграй 15 игр", "wins", 15, 400, 600, "👑"),
            ("weekly", "Разрушитель", "1M урона по строениям", "tower_damage", 1000000, 200, 350, "🏰"),
            ("weekly", "Целитель", "50K HP союзникам", "healing", 50000, 180, 300, "💚"),
            ("monthly", "Легенда", "50 побед", "wins", 50, 1000, 2000, "🏆"),
            ("monthly", "Мастер героя", "30 игр на одном", "hero_matches", 30, 800, 1500, "🦸"),
            ("monthly", "Несокрушимый", "Винрейт 60%+", "winrate", 60, 1200, 2500, "💎"),
            ("monthly", "Профессионал", "Средний KDA 4.0+", "avg_kda", 4, 900, 1800, "🎯"),
            ("monthly", "Богатей", "Накопи 10K монет", "total_coins", 10000, 1500, 3000, "💸"),
        ]); conn.commit()

    if c.execute("SELECT COUNT(*) FROM shop_items"): count = c.fetchone()['count']
    if count == 0:
        c.executemany("INSERT INTO shop_items (name, description, type, price, icon, data) VALUES (%s,%s,%s,%s,%s,%s)", [
            ("XP Booster x2", "Удваивает опыт на 24ч", "booster_xp", 500, "⚡", "duration:24,multiplier:2"),
            ("Coin Booster x2", "Удваивает монеты на 24ч", "booster_coins", 600, "💰", "duration:24,multiplier:2"),
            ("Mega Booster", "x2 XP и монеты на 48ч", "booster_mega", 1500, "🚀", "duration:48,xp:2,coins:2"),
            ("Золотая рамка", "Золотая рамка профиля", "cosmetic_frame", 300, "🖼️", "color:gold"),
            ("Алмазная рамка", "Алмазная рамка профиля", "cosmetic_frame", 800, "💎", "color:diamond"),
            ("Титул: Новичок", "Отображается в профиле", "cosmetic_title", 200, "🏷️", "title:Новичок"),
            ("Титул: Легенда", "Отображается в профиле", "cosmetic_title", 1000, "👑", "title:Легенда"),
            ("Сброс миссий", "Обновляет текущие миссии", "special_refresh", 300, "🔄", "refresh:all"),
            ("AI Запросы x10", "10 доп. запросов к AI", "special_ai", 250, "🤖", "queries:10"),
        ]); conn.commit()
    conn.close()
init_db()

# ─── HELPERS ─────────────────────────────────────────────────────────
def calc_kda(k,d,a): return round((k+a)/max(d,1),2)
def rank_name(rt):
    if not rt: return "Uncalibrated"
    t,s = int(str(rt)[0]), int(str(rt)[-1]) if len(str(rt))>1 else 0
    names = {1:"Herald",2:"Guardian",3:"Crusader",4:"Archon",5:"Legend",6:"Ancient",7:"Divine",8:"Immortal"}
    return f"{names.get(t,'')} {'★'*s}" if s else names.get(t,'')
def calc_level(xp): return (xp // 500) + 1

# ─── STRATZ & OPENDOTA ───────────────────────────────────────────────
async def stratz_player(aid: int):
    q = """query Player($id: Long!) { player(steamAccountId: $id) { steamAccount{id name avatar isAnonymous seasonRank} winCount matchCount heroesPerformance(request:{take:10}){hero{displayName shortName} winCount matchCount avgKills avgDeaths avgAssists avgGoldPerMinute avgExperiencePerMinute avgNetworth} matches(request:{take:20 orderBy:END_DATE_TIME}){id didRadiantWin durationSeconds endDateTime players(steamAccountId:$id){isRadiant kills deaths assists goldPerMinute experiencePerMinute networth heroDamage towerDamage heroHealingDone numLastHits numDenies hero{displayName shortName}}} }}"""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(STRATZ_GQL, json={"query":q,"variables":{"id":aid}}, headers={"Authorization":f"Bearer {STRATZ_TOKEN}"})
            d = r.json()
            return d.get("data",{}).get("player") if "errors" not in d else None
    except: return None

async def stratz_search(nick: str):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{STRATZ_BASE}/search", params={"query":nick}, headers={"Authorization":f"Bearer {STRATZ_TOKEN}"})
            d = r.json()
            return [{"account_id":p["steamAccount"]["id"],"personaname":p["steamAccount"].get("name","Unknown"),"avatarfull":p["steamAccount"].get("avatar","")} for p in d.get("players",[])]
    except: return []

async def od_get(path, params=None):
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(f"{OPENDOTA_BASE}{path}", params=params)
            return r.json() if r.status_code==200 else None
    except: return None
async def od_player(aid): return await od_get(f"/players/{aid}")
async def od_wl(aid): return await od_get(f"/players/{aid}/wl")
async def od_matches(aid): return await od_get(f"/players/{aid}/recentMatches",{"limit":20})
async def od_heroes(aid): return await od_get(f"/players/{aid}/heroes",{"limit":10})
async def od_search(nick): return await od_get("/search",{"q":nick})

async def search_combined(q):
    res = await stratz_search(q) if STRATZ_TOKEN else []
    if not res:
        od = await od_search(q)
        if od: res = [{"account_id":p.get("account_id"),"personaname":p.get("personaname","Unknown"),"avatarfull":p.get("avatarfull","")} for p in od]
    return res

# ─── TELEGRAM AUTH ───────────────────────────────────────────────────
def verify_init_data(init_data: str):
    if not init_data: return None
    p = urllib.parse.parse_qs(init_data)
    h = p.pop('hash',[None])[0]
    if not h: return None
    dcs = "\n".join(f"{k}={v[0]}" for k,v in sorted(p.items()))
    sk = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return json.loads(p.get('user',['{}'])[0]) if hmac.new(sk, dcs.encode(), hashlib.sha256).hexdigest() == h else None

# ─── ENDPOINTS ───────────────────────────────────────────────────────
@app.get("/")
async def root(): return {"status":"ok","version":"2.5.0"}

@app.post("/webapp/auth")
async def auth(req: Request):
    body = await req.json()
    u = verify_init_data(body.get("initData",""))
    if not u: raise HTTPException(401, "Invalid initData")
    conn = get_db(); c = conn.cursor()
    c.execute("INSERT INTO users (telegram_id, username, last_seen) VALUES (%s,%s,NOW()) ON CONFLICT (telegram_id) DO UPDATE SET username=EXCLUDED.username, last_seen=NOW() RETURNING telegram_id, coins, xp, level", (u['id'], u.get('username','')))
    r = c.fetchone(); conn.close()
    return {"auth":True, "user":{**r, "tg_username":u.get('username','')}}

@app.get("/user/profile")
async def profile(telegram_id: int = Query(...)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT telegram_id, username, coins, xp, level, created_at FROM users WHERE telegram_id=%s", (telegram_id,))
    u = c.fetchone()
    c.execute("SELECT si.name, si.icon, ui.quantity FROM user_inventory ui JOIN shop_items si ON ui.item_id=si.id WHERE ui.telegram_id=%s LIMIT 20", (telegram_id,))
    inv = c.fetchall(); conn.close()
    return {"user":u, "inventory":inv}

@app.get("/player")
async def find_player(query: str = Query(..., min_length=1)):
    query = query.strip()
    cached = get_cache(f"player:{query}")
    if cached: return cached
    aid = int(query) if query.isdigit() and int(query)<=76561197960265728 else (steam64_to_account_id(int(query)) if query.isdigit() else None)
    if not aid:
        res = await search_combined(query)
        if not res: raise HTTPException(404, "Player not found")
        aid = res[0]["account_id"]
    result = None
    if STRATZ_TOKEN:
        sd = await stratz_player(aid)
        if sd:
            sa = sd.get("steamAccount",{})
            win, total = sd.get("winCount",0), sd.get("matchCount",1) or 1
            heroes = [{"hero_name":h.get("hero",{}).get("displayName","?"), "matches":h.get("matchCount",1), "wins":h.get("winCount",0), "winrate":round(h.get("winCount",0)/h.get("matchCount",1)*100,1), "kda":calc_kda(h.get("avgKills",0),h.get("avgDeaths",1),h.get("avgAssists",0))} for h in (sd.get("heroesPerformance",[]) or [])]
            matches = []
            for m in (sd.get("matches",[]) or []):
                ps = (m.get("players") or [{}])[0]
                is_rad = ps.get("isRadiant",True)
                matches.append({"match_id":m.get("id"), "hero":ps.get("hero",{}).get("displayName","?"), "win":(is_rad and m.get("didRadiantWin")) or (not is_rad and not m.get("didRadiantWin")), "kills":ps.get("kills",0), "deaths":ps.get("deaths",0), "assists":ps.get("assists",0), "kda":calc_kda(ps.get("kills",0),ps.get("deaths",0),ps.get("assists",0)), "gpm":ps.get("goldPerMinute",0), "tower_damage":ps.get("towerDamage",0), "healing":ps.get("heroHealingDone",0)})
            result = {"source":"stratz","account_id":aid,"profile":{"name":sa.get("name","?"),"avatar":sa.get("avatar",""),"rank":rank_name(sa.get("seasonRank")),"is_anonymous":sa.get("isAnonymous",False)}, "stats":{"wins":win,"losses":total-win,"total_matches":total,"winrate":round(win/total*100,1)}, "top_heroes":heroes[:10], "recent_matches":matches[:20]}
    if not result:
        p, wl, m, h = await asyncio.gather(od_player(aid), od_wl(aid), od_matches(aid), od_heroes(aid))
        if not p: raise HTTPException(404, "Profile not found")
        pd = p.get("profile",{})
        win, loss = (wl or {}).get("win",0), (wl or {}).get("lose",0)
        total = win+loss or 1
        matches_out = [{"match_id":x.get("match_id"), "hero":"", "win":(x.get("player_slot",0)<128 and x.get("radiant_win")) or (x.get("player_slot",0)>=128 and not x.get("radiant_win")), "kills":x.get("kills",0), "deaths":x.get("deaths",0), "assists":x.get("assists",0), "kda":calc_kda(x.get("kills",0),x.get("deaths",0),x.get("assists",0)), "gpm":x.get("gold_per_min",0), "tower_damage":x.get("tower_damage",0), "healing":x.get("hero_healing",0)} for x in (m or [])[:20]]
        result = {"source":"opendota","account_id":aid,"profile":{"name":pd.get("personaname","?"),"avatar":pd.get("avatarfull",""),"rank":rank_name(p.get("rank_tier")),"is_anonymous":False}, "stats":{"wins":win,"losses":loss,"total_matches":total,"winrate":round(win/total*100,1)}, "top_heroes":[], "recent_matches":matches_out}
    if (not result.get("recent_matches") or len(result["recent_matches"])==0) and result["stats"]["total_matches"]<=1:
        raise HTTPException(403, "Private profile")
    set_cache(f"player:{query}", result)
    return result

@app.get("/search")
async def search(q: str = Query(..., min_length=1)): return (await search_combined(q))[:5]

# ─── SHOP ────────────────────────────────────────────────────────────
@app.get("/shop/items")
async def shop_items(telegram_id: int = Query(...)):
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT * FROM shop_items ORDER BY price ASC")
    items = c.fetchall()
    c.execute("SELECT item_id, quantity FROM user_inventory WHERE telegram_id=%s", (telegram_id,))
    inv = {r["item_id"]:r["quantity"] for r in c.fetchall()}
    conn.close()
    return {"items":[{**i, "owned":inv.get(i["id"],0)} for i in items]}

@app.post("/shop/buy")
async def buy_item(req: Request):
    body = await req.json()
    tid, iid = body.get("telegram_id"), body.get("item_id")
    if not tid or not iid: raise HTTPException(400, "Missing params")
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT coins FROM users WHERE telegram_id=%s FOR UPDATE", (tid,))
    user = c.fetchone()
    if not user: raise HTTPException(404, "User not found")
    c.execute("SELECT id, name, price FROM shop_items WHERE id=%s", (iid,))
    item = c.fetchone()
    if not item: raise HTTPException(404, "Item not found")
    if user["coins"] < item["price"]: raise HTTPException(400, "Not enough coins")
    c.execute("UPDATE users SET coins = coins - %s WHERE telegram_id=%s", (item["price"], tid))
    c.execute("INSERT INTO user_inventory (telegram_id, item_id, quantity) VALUES (%s,%s,1) ON CONFLICT (telegram_id, item_id) DO UPDATE SET quantity = user_inventory.quantity + 1", (tid, iid))
    c.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (%s,'purchase',-%s,%s)", (tid, item["price"], f"Bought {item['name']}"))
    conn.commit(); conn.close()
    return {"success":True, "new_coins":user["coins"]-item["price"], "item":item["name"]}

# ─── MISSIONS & TRACKER ─────────────────────────────────────────────
@app.post("/missions/assign")
async def assign_missions(req: Request):
    body = await req.json()
    tid = body.get("telegram_id")
    if not tid: raise HTTPException(400, "telegram_id required")
    conn = get_db(); c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT m.*, um.progress, um.completed, um.claimed FROM missions m JOIN user_missions um ON m.id=um.mission_id WHERE um.telegram_id=%s AND um.assigned_at::date=%s", (tid, today))
    missions = c.fetchall()
    if not missions:
        c.execute("SELECT id, reward_coins, reward_xp FROM missions ORDER BY RANDOM() LIMIT 5")
        new = c.fetchall()
        for m in new: c.execute("INSERT INTO user_missions (telegram_id, mission_id) VALUES (%s,%s)", (tid, m["id"]))
        conn.commit()
        c.execute("SELECT m.*, um.progress, um.completed, um.claimed FROM missions m JOIN user_missions um ON m.id=um.mission_id WHERE um.telegram_id=%s AND um.assigned_at::date=%s", (tid, today))
        missions = c.fetchall()
    conn.close()
    return {"missions":missions}

@app.post("/missions/claim")
async def claim_mission(req: Request):
    body = await req.json()
    tid, mid = body.get("telegram_id"), body.get("mission_id")
    if not tid or not mid: raise HTTPException(400, "Missing params")
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT um.id, um.claimed, m.reward_coins, m.reward_xp FROM user_missions um JOIN missions m ON um.mission_id=m.id WHERE um.telegram_id=%s AND um.mission_id=%s AND um.completed=1 AND um.claimed=0", (tid, mid))
    row = c.fetchone()
    if not row: raise HTTPException(400, "Already claimed or not completed")
    c.execute("UPDATE user_missions SET claimed=1, completed_at=NOW() WHERE id=%s", (row["id"],))
    c.execute("UPDATE users SET coins=coins+%s, xp=xp+%s WHERE telegram_id=%s RETURNING xp, level", (row["reward_coins"], row["reward_xp"], tid))
    usr = c.fetchone()
    c.execute("INSERT INTO transactions (telegram_id, type, amount, description) VALUES (%s,'mission_reward',%s,%s)", (tid, row["reward_coins"], f"Claimed mission {mid}"))
    conn.commit(); conn.close()
    return {"success":True, "new_xp":usr["xp"], "new_level":calc_level(usr["xp"])}

@app.post("/missions/sync")
async def sync_missions(req: Request):
    body = await req.json()
    tid, aid = body.get("telegram_id"), body.get("account_id")
    if not tid or not aid: raise HTTPException(400, "Missing params")
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT m.id, m.requirement, m.target_value, um.progress, um.completed FROM user_missions um JOIN missions m ON m.id=um.mission_id WHERE um.telegram_id=%s AND um.completed=0", (tid,))
    active = c.fetchall()
    if not active: conn.close(); return {"synced":0}
    try:
        res = await find_player(str(aid))
        matches = res.get("recent_matches", [])
    except: matches = []
    updated = 0
    for m in active:
        prog = m["progress"]
        if m["requirement"] == "matches": prog = len(matches)
        elif m["requirement"] == "wins": prog = sum(1 for x in matches if x.get("win"))
        elif m["requirement"] == "gpm": prog = max((x.get("gpm",0) for x in matches), default=0)
        elif m["requirement"] == "kda": prog = max((x.get("kda",0) for x in matches), default=0)
        elif m["requirement"] == "assists": prog = max((x.get("assists",0) for x in matches), default=0)
        elif m["requirement"] == "tower_damage": prog = max((x.get("tower_damage",0) for x in matches), default=0)
        elif m["requirement"] == "healing": prog = max((x.get("healing",0) for x in matches), default=0)
        completed = 1 if prog >= m["target_value"] else 0
        if prog != m["progress"] or completed:
            c.execute("UPDATE user_missions SET progress=%s, completed=%s, completed_at=%s WHERE id=%s AND telegram_id=%s", (prog, completed, datetime.now() if completed else None, m["id"], tid))
            updated += 1
    conn.commit(); conn.close()
    return {"synced": updated}

# ─── AI ──────────────────────────────────────────────────────────────
class AIReq(BaseModel): message: str; player_context: str = ""; history: list = []
@app.post("/ai")
async def ai_chat(req: AIReq):
    if not GROQ_API_KEY: raise HTTPException(503, "AI not configured")
    sys = "You are an expert Dota 2 coach. Give concise, actionable advice based on stats. Use bullet points. Respond in user's language. Keep under 300 words."
    msgs = req.history[-6:] + [{"role":"user","content":req.message}] if req.history else [{"role":"user","content":f"Player data:\n{req.player_context}\n\n---\n{req.message}" if req.player_context else req.message}]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"}, json={"model":"llama-3.3-70b-versatile","messages":[{"role":"system","content":sys}]+msgs,"max_tokens":1000,"temperature":0.7})
    if r.status_code!=200: raise HTTPException(502, f"AI failed: {r.text[:100]}")
    return {"reply":r.json()["choices"][0]["message"]["content"]}

# ─── TELEGRAM WEBHOOK ────────────────────────────────────────────────
async def tg_send(cid, text, kb=None):
    if not BOT_TOKEN: return
    async with httpx.AsyncClient(timeout=10) as c: await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":cid,"text":text,"parse_mode":"HTML","reply_markup":kb})

@app.post("/webhook")
async def webhook(req: Request):
    d = await req.json()
    if "message" not in d: return {"ok":True}
    cid = d["message"]["chat"]["id"]; txt = d["message"].get("text","").strip()
    kb = {"inline_keyboard":[[{"text":"🎮 Открыть WebApp","web_app":{"url":WEBAPP_URL}}]]} if WEBAPP_URL else None
    if txt=="/start": await tg_send(cid, "👋 <b>Dota 2 Analyzer</b>\nОтправь никнейм/ID или открой WebApp 👇", kb)
    elif txt=="/help": await tg_send(cid, "📖 <b>Как использовать:</b>\n• Ник: <code>Miracle-</code>\n• Steam32: <code>105248644</code>\n• Steam64: <code>76561198065514372</code>")
    elif txt.startswith("/"): await tg_send(cid, "❓ Неизвестная команда. /help")
    else:
        await tg_send(cid, f"🔍 Ищу <b>{txt}</b>...")
        try:
            aid = int(txt) if txt.isdigit() and int(txt)<=76561197960265728 else (steam64_to_account_id(int(txt)) if txt.isdigit() else None)
            if not aid:
                res = await search_combined(txt)
                if not res: await tg_send(cid, "❌ Не найден."); return {"ok":True}
                aid = res[0]["account_id"]
            r = get_cache(f"player:{txt}")
            if not r: r = await find_player(txt)
            set_cache(f"player:{txt}", r)
            p, s = r["profile"], r["stats"]
            msg = f"⚡ <b>{p['name']}</b>\n🏅 {p['rank']}\n📈 WR: {s['winrate']}% ({s['wins']}W/{s['losses']}L)\n🎮 Всего: {s['total_matches']}"
            kb2 = {"inline_keyboard":[[{"text":"📊 Полный анализ","web_app":{"url":f"{WEBAPP_URL}?player_id={aid}&telegram_id={cid}"}}]]} if WEBAPP_URL else None
            await tg_send(cid, msg, kb2)
        except Exception as e:
            logger.error(f"Webhook: {e}")
            await tg_send(cid, "⚠️ Ошибка. Попробуй позже.")
    return {"ok":True}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 8000)))