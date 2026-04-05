import logging
import time
import asyncio
import os
import httpx

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
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")

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

    if text == "/start":
        keyboard = None
        if WEBAPP_URL:
            keyboard = {
                "inline_keyboard": [[
                    {"text": "🎮 Open Analyzer", "web_app": {"url": WEBAPP_URL}}
                ]]
            }
        await tg_send(
            chat_id,
            "👋 <b>Dota 2 Analyzer</b>\n\n"
            "Send me a nickname or Steam ID and I'll show:\n"
            "• Rank and winrate\n"
            "• Top heroes\n"
            "• Recent matches\n"
            "• Win/loss streaks\n"
            "• KDA and GPM trends\n\n"
            "Or open the Web App 👇",
            reply_markup=keyboard,
        )

    elif text == "/help":
        await tg_send(
            chat_id,
            "📖 <b>How to use:</b>\n\n"
            "• Send nickname: <code>Miracle-</code>\n"
            "• Or Steam32 ID: <code>105248644</code>\n"
            "• Or Steam64 ID: <code>76561198065514372</code>\n\n"
            "Data from Stratz API (fallback to OpenDota).",
        )

    elif text.startswith("/"):
        await tg_send(chat_id, "❓ Unknown command. Use /help")

    else:
        await tg_send(chat_id, f"🔍 Looking up <b>{text}</b>...")
        try:
            query = text
            if query.isdigit():
                q_int = int(query)
                account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
            else:
                results = await search_combined(query)
                if not results:
                    await tg_send(chat_id, "❌ Player not found. Check nickname or ID.")
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
                        await tg_send(chat_id, "❌ Profile not found or private.")
                        return {"ok": True}
                    result = build_from_opendota(player, wl, matches, heroes, account_id)
                set_cache(f"player:{query}", result)

            msg = format_player_message(result)
            keyboard = None
            if WEBAPP_URL:
                keyboard = {
                    "inline_keyboard": [[
                        {
                            "text": "📊 Full Analysis",
                            "web_app": {"url": f"{WEBAPP_URL}?player_id={account_id}"}
                        }
                    ]]
                }
            await tg_send(chat_id, msg, reply_markup=keyboard)

        except Exception as e:
            logger.error(f"Webhook error: {e}")
            await tg_send(chat_id, "⚠️ Error loading data. Try again later.")

    return {"ok": True}
