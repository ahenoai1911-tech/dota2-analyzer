import logging
import time
import asyncio
import os
import httpx

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

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
BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
WEBAPP_URL   = os.getenv("WEBAPP_URL", "")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN", "")

OPENDOTA_BASE = "https://api.opendota.com/api"
STRATZ_BASE   = "https://api.stratz.com/api/v1"
STRATZ_GQL    = "https://api.stratz.com/graphql"

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


# ── RANK (English only) ───────────────────────────────────────────────────────
def rank_tier_to_name(rank_tier) -> str:
    if rank_tier is None:
        return "Uncalibrated"
    tiers = {
        1: "Herald", 2: "Guardian", 3: "Crusader", 4: "Archon",
        5: "Legend",  6: "Ancient",  7: "Divine",   8: "Immortal",
    }
    s    = str(rank_tier)
    tier = int(s[0]) if s else 0
    star = int(s[1]) if len(s) > 1 else 0
    name = tiers.get(tier, "Unknown")
    return f"{name} {'★' * star}" if star else name


# ── STRATZ ────────────────────────────────────────────────────────────────────
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
          id name avatar profileUri isAnonymous seasonRank
        }
        winCount matchCount
        heroesPerformance(request: { take: 10 }) {
          hero { displayName shortName }
          winCount matchCount
          avgKills avgDeaths avgAssists
          avgGoldPerMinute avgExperiencePerMinute avgNetworth avgImp
        }
        matches(request: { take: 20, orderBy: END_DATE_TIME }) {
          id didRadiantWin durationSeconds endDateTime gameMode
          players(steamAccountId: $steamAccountId) {
            isRadiant kills deaths assists
            goldPerMinute experiencePerMinute networth
            heroDamage towerDamage heroHealingDone
            numLastHits numDenies
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
                logger.warning(f"Stratz GQL errors: {data['errors']}")
                return None
            return data.get("data", {}).get("player")
    except Exception as e:
        logger.error(f"Stratz player error: {e}")
        return None

async def stratz_search(nickname: str) -> list:
    """Search players via Stratz — returns up to 10 results"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{STRATZ_BASE}/search",
                params={"query": nickname},
                headers=stratz_headers(),
            )
            if r.status_code != 200:
                return []
            data = r.json()
            return [
                {
                    "account_id": p["steamAccount"]["id"],
                    "personaname": p["steamAccount"].get("name", "Unknown"),
                    "avatarfull":  p["steamAccount"].get("avatar", ""),
                }
                for p in data.get("players", [])
                if p.get("steamAccount")
            ]
    except Exception as e:
        logger.error(f"Stratz search error: {e}")
        return []


# ── OPENDOTA ──────────────────────────────────────────────────────────────────
async def od_get(path: str, params: dict = None):
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{OPENDOTA_BASE}{path}", params=params)
            if r.status_code == 200:
                return r.json()
            logger.warning(f"OpenDota {path} → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"OpenDota error {path}: {e}")
        return None

async def od_player(account_id):  return await od_get(f"/players/{account_id}")
async def od_wl(account_id):      return await od_get(f"/players/{account_id}/wl")
async def od_matches(account_id, limit=20): return await od_get(f"/players/{account_id}/recentMatches", {"limit": limit})
async def od_heroes(account_id):  return await od_get(f"/players/{account_id}/heroes", {"limit": 10})

async def od_search(nickname: str) -> list:
    """Search players via OpenDota"""
    result = await od_get("/search", {"q": nickname})
    if not result:
        return []
    return [
        {
            "account_id": p.get("account_id"),
            "personaname": p.get("personaname", "Unknown"),
            "avatarfull":  p.get("avatarfull", ""),
        }
        for p in result
        if p.get("account_id")
    ]


# ── COMBINED SEARCH (best results) ───────────────────────────────────────────
async def search_combined(query: str, limit: int = 8) -> list:
    """
    Searches both Stratz and OpenDota in parallel,
    merges results by account_id (deduplication), returns best matches.
    """
    # Run both searches in parallel
    stratz_task = stratz_search(query) if STRATZ_TOKEN else asyncio.sleep(0, result=[])
    od_task     = od_search(query)

    stratz_results, od_results = await asyncio.gather(stratz_task, od_task)

    # Merge: Stratz first (usually better), then OpenDota extras
    seen_ids = set()
    merged   = []

    for p in (stratz_results or []):
        aid = p.get("account_id")
        if aid and aid not in seen_ids:
            seen_ids.add(aid)
            merged.append(p)

    for p in (od_results or []):
        aid = p.get("account_id")
        if aid and aid not in seen_ids:
            seen_ids.add(aid)
            merged.append(p)

    # Filter: remove entries with empty names
    merged = [p for p in merged if p.get("personaname") and p["personaname"] != "Unknown"]

    return merged[:limit]


# ── ANALYSIS ──────────────────────────────────────────────────────────────────
def calc_kda(kills, deaths, assists):
    return round((kills + assists) / max(deaths, 1), 2)

def compute_streak(matches: list) -> dict:
    if not matches:
        return {"type": "none", "count": 0}
    first_win = matches[0].get("win", False)
    count = 0
    for m in matches:
        if m.get("win") == first_win:
            count += 1
        else:
            break
    return {"type": "win" if first_win else "loss", "count": count}

def compute_trend(matches: list) -> dict:
    if not matches:
        return {}
    def avg(lst, key):
        vals = [m.get(key) or 0 for m in lst]
        return round(sum(vals) / len(vals), 2) if vals else 0
    def wr(lst):
        return round(sum(1 for m in lst if m.get("win")) / len(lst) * 100, 1) if lst else 0
    last5  = matches[:5]
    last20 = matches[:20]
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
    rank_tier = sa.get("seasonRank")

    heroes = []
    for h in (player_data.get("heroesPerformance") or []):
        hero = h.get("hero") or {}
        hm = h.get("matchCount") or 1
        hw = h.get("winCount", 0)
        heroes.append({
            "hero_name":  hero.get("displayName", "Unknown"),
            "hero_short": hero.get("shortName", ""),
            "matches": hm, "wins": hw,
            "winrate": round(hw / hm * 100, 1),
            "kda": calc_kda(h.get("avgKills",0), h.get("avgDeaths",1), h.get("avgAssists",0)),
            "avg_gpm": round(h.get("avgGoldPerMinute") or 0),
            "avg_xpm": round(h.get("avgExperiencePerMinute") or 0),
            "avg_networth": round(h.get("avgNetworth") or 0),
        })

    matches = []
    for m in (player_data.get("matches") or []):
        ps = ((m.get("players") or [{}])[0])
        hero_info   = ps.get("hero") or {}
        is_radiant  = ps.get("isRadiant", True)
        radiant_win = m.get("didRadiantWin", False)
        won = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        dur = m.get("durationSeconds", 0)
        matches.append({
            "match_id":     m.get("id"),
            "hero":         hero_info.get("displayName", "Unknown"),
            "hero_short":   hero_info.get("shortName", ""),
            "win":          won,
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
            "game_mode":    m.get("gameMode", ""),
        })

    return {
        "source":      "stratz",
        "account_id":  account_id,
        "profile": {
            "name":         sa.get("name", "Unknown"),
            "avatar":       sa.get("avatar", ""),
            "profile_url":  sa.get("profileUri", ""),
            "rank":         rank_tier_to_name(rank_tier),
            "rank_tier":    rank_tier,
            "is_anonymous": sa.get("isAnonymous", False),
        },
        "stats": {
            "wins": win, "losses": loss, "total_matches": total,
            "winrate": round(win / total * 100, 1),
        },
        "top_heroes":     heroes,
        "recent_matches": matches,
        "trend":          compute_trend(matches),
    }

def build_from_opendota(player, wl, matches, heroes, account_id: int) -> dict:
    profile_data = player.get("profile", {})
    rank_tier    = player.get("rank_tier")
    mmr_raw      = player.get("mmr_estimate") or {}
    mmr          = mmr_raw.get("estimate") if isinstance(mmr_raw, dict) else None

    win   = (wl or {}).get("win", 0)
    loss  = (wl or {}).get("lose", 0)
    total = win + loss or 1

    heroes_out = []
    for h in (heroes or [])[:10]:
        hm = h.get("games", 0) or 1
        hw = h.get("win", 0)
        heroes_out.append({
            "hero_id":   h.get("hero_id"),
            "hero_name": str(h.get("hero_id", "")),
            "matches":   hm,
            "wins":      hw,
            "winrate":   round(hw / hm * 100, 1),
            "kda":       0,
        })

    matches_out = []
    for m in (matches or [])[:20]:
        pslot       = m.get("player_slot", 0)
        is_radiant  = pslot < 128
        radiant_win = m.get("radiant_win", False)
        won = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        dur = m.get("duration", 0)
        matches_out.append({
            "match_id":     m.get("match_id"),
            "hero_id":      m.get("hero_id"),
            "win":          won,
            "kills":        m.get("kills", 0),
            "deaths":       m.get("deaths", 0),
            "assists":      m.get("assists", 0),
            "kda":          calc_kda(m.get("kills",0), m.get("deaths",0), m.get("assists",0)),
            "gpm":          m.get("gold_per_min", 0),
            "xpm":          m.get("xp_per_min", 0),
            "hero_damage":  m.get("hero_damage", 0),
            "tower_damage": m.get("tower_damage", 0),
            "healing":      m.get("hero_healing", 0),
            "last_hits":    m.get("last_hits", 0),
            "denies":       m.get("denies", 0),
            "duration_min": dur // 60,
            "duration_sec": dur % 60,
            "end_time":     m.get("start_time"),
            "game_mode":    m.get("game_mode", 0),
        })

    return {
        "source":      "opendota",
        "account_id":  account_id,
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
            "wins": win, "losses": loss, "total_matches": total,
            "winrate": round(win / total * 100, 1),
        },
        "top_heroes":     heroes_out,
        "recent_matches": matches_out,
        "trend":          compute_trend(matches_out),
    }


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "message": "Dota 2 Analyzer API v2.1"}

@app.get("/search")
async def search(q: str = Query(..., min_length=1)):
    """Search players — returns up to 8 results from Stratz + OpenDota combined"""
    results = await search_combined(q, limit=8)
    return results

@app.get("/player")
async def find_player(query: str = Query(..., min_length=1)):
    """Get full player stats. Query = nickname or Steam ID (32-bit or 64-bit)"""
    query = query.strip()
    cache_key = f"player:{query}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    # Resolve account_id
    if query.isdigit():
        q_int      = int(query)
        account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
    else:
        results = await search_combined(query, limit=1)
        if not results:
            raise HTTPException(status_code=404, detail=f"Player '{query}' not found. Try Steam ID instead.")
        account_id = results[0]["account_id"]

    result = None

    # Try Stratz first
    if STRATZ_TOKEN:
        stratz_data = await stratz_player(account_id)
        if stratz_data:
            result = build_from_stratz(stratz_data, account_id)

    # Fallback to OpenDota
    if not result:
        logger.info(f"Falling back to OpenDota for {account_id}")
        player, wl, matches, heroes = await asyncio.gather(
            od_player(account_id),
            od_wl(account_id),
            od_matches(account_id),
            od_heroes(account_id),
        )
        if not player:
            raise HTTPException(status_code=404, detail="Profile not found or private.")
        result = build_from_opendota(player, wl, matches, heroes, account_id)

    set_cache(cache_key, result)
    return result

@app.get("/matches")
async def get_matches(player_id: int = Query(...)):
    cache_key = f"matches:{player_id}"
    cached    = get_cache(cache_key)
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
    cached    = get_cache(cache_key)
    if cached:
        return cached
    heroes = await od_heroes(player_id)
    set_cache(cache_key, heroes)
    return heroes


# ── TELEGRAM WEBHOOK ──────────────────────────────────────────────────────────
async def tg_send(chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.error(f"tg_send error: {e}")

def format_player_message(data: dict) -> str:
    p       = data["profile"]
    s       = data["stats"]
    t       = data.get("trend", {})
    streak  = t.get("streak", {})
    src_icon = "⚡" if data.get("source") == "stratz" else "📊"
    anon     = " 🔒" if p.get("is_anonymous") else ""
    mmr_str  = f"\n💎 MMR: ~{p['mmr_estimate']}" if p.get("mmr_estimate") else ""

    streak_str = ""
    if streak.get("count", 0) >= 2:
        e = "🔥" if streak["type"] == "win" else "❄️"
        streak_str = f"\n{e} Streak: {streak['count']} {'wins' if streak['type'] == 'win' else 'losses'} in a row"

    heroes_lines = ""
    for i, h in enumerate(data.get("top_heroes", [])[:5], 1):
        name = h.get("hero_name") or f"Hero#{h.get('hero_id','?')}"
        heroes_lines += f"  {i}. {name} — {h['matches']} games, WR {h['winrate']}%\n"

    matches_lines = ""
    for m in data.get("recent_matches", [])[:5]:
        hero   = m.get("hero") or f"Hero#{m.get('hero_id','?')}"
        result = "✅" if m["win"] else "❌"
        dur    = f"{m['duration_min']}:{m['duration_sec']:02d}" if m.get("duration_min") is not None else ""
        matches_lines += f"  {result} {hero} — {m['kills']}/{m['deaths']}/{m['assists']}{' ' + dur if dur else ''}\n"

    return (
        f"{src_icon} <b>{p['name']}</b>{anon}\n"
        f"🏅 Rank: <b>{p['rank']}</b>{mmr_str}\n"
        f"📈 WinRate: <b>{s['winrate']}%</b> ({s['wins']}W / {s['losses']}L · {s['total_matches']} games)\n"
        f"{streak_str}\n"
        f"\n📊 Trends:\n"
        f"  Last 5:  WR {t.get('last5_winrate','?')}%  KDA {t.get('last5_avg_kda','?')}\n"
        f"  Last 20: WR {t.get('last20_winrate','?')}%  GPM {t.get('last20_avg_gpm','?')}\n"
        f"\n🦸 Top Heroes:\n{heroes_lines}"
        f"\n🕹 Recent Matches:\n{matches_lines}"
        f"\n🔗 <a href='https://stratz.com/players/{data['account_id']}'>View on Stratz</a>"
    )

def make_webapp_keyboard(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "🎮 Open Analyzer", "web_app": {"url": url}}]]}

@app.post("/webhook")
async def telegram_webhook(req: Request):
    try:
        data = await req.json()
    except Exception:
        return {"ok": True}

    if "message" not in data:
        return {"ok": True}

    msg     = data["message"]
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()

    if text == "/start":
        keyboard = make_webapp_keyboard(WEBAPP_URL) if WEBAPP_URL else None
        await tg_send(chat_id,
            "👋 <b>Dota 2 Analyzer</b>\n\n"
            "Send me a player nickname or Steam ID:\n\n"
            "• <code>Miracle-</code>\n• <code>Dendi</code>\n"
            "• <code>105248644</code>\n\n"
            "Or open the Web App 👇",
            reply_markup=keyboard)
        return {"ok": True}

    if text == "/help":
        keyboard = make_webapp_keyboard(WEBAPP_URL) if WEBAPP_URL else None
        await tg_send(chat_id,
            "📖 <b>Commands:</b>\n\n"
            "Just send a nickname or Steam ID.\n"
            "Data: ⚡ Stratz → 📊 OpenDota fallback\n\n"
            "🌐 Web App has full stats + AI analysis 👇",
            reply_markup=keyboard)
        return {"ok": True}

    if text.startswith("/"):
        await tg_send(chat_id, "❓ Unknown command. Use /help")
        return {"ok": True}

    if not text:
        return {"ok": True}

    await tg_send(chat_id, f"🔍 Searching for <b>{text}</b>...")

    try:
        query = text
        if query.isdigit():
            q_int      = int(query)
            account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
        else:
            results = await search_combined(query, limit=1)
            if not results:
                await tg_send(chat_id, "❌ Player not found. Try Steam ID instead.")
                return {"ok": True}
            account_id = results[0]["account_id"]

        result = get_cache(f"player:{query}") or get_cache(f"player:{account_id}")

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
            set_cache(f"player:{account_id}", result)

        keyboard = None
        if WEBAPP_URL:
            keyboard = {"inline_keyboard": [[{
                "text": "📊 Full Analysis + AI",
                "web_app": {"url": f"{WEBAPP_URL}?player_id={account_id}"}
            }]]}

        await tg_send(chat_id, format_player_message(result), reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        await tg_send(chat_id, "⚠️ Error fetching data. Try again later.")

    return {"ok": True}


# ── WEBHOOK SETUP ─────────────────────────────────────────────────────────────
@app.get("/setup_webhook")
async def setup_webhook(req: Request):
    if not BOT_TOKEN:
        return {"error": "BOT_TOKEN not set"}
    base_url    = str(req.base_url).rstrip("/")
    webhook_url = f"{base_url}/webhook"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            params={"url": webhook_url}
        )
    return {"webhook_url": webhook_url, "telegram_response": r.json()}

@app.get("/webhook_info")
async def webhook_info():
    if not BOT_TOKEN:
        return {"error": "BOT_TOKEN not set"}
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")
    return r.json()
