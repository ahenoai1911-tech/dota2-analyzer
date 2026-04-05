import logging
import time
import asyncio
import os
import httpx

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Dota 2 Analyzer API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ENV ──────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
WEBAPP_URL  = os.getenv("WEBAPP_URL", "")          # URL твоего фронтенда
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN", "")        # https://stratz.com/api

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


# ── STRATZ helpers ───────────────────────────────────────────────────────────
def stratz_headers() -> dict:
    return {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "User-Agent": "Dota2AnalyzerBot/2.0",
    }


async def stratz_player(account_id: int) -> dict | None:
    """Полные данные игрока через Stratz GraphQL"""
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
        performance {
          imp
        }
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
          lobbyType
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
                logger.warning(f"Stratz GQL errors: {data['errors']}")
                return None
            return data.get("data", {}).get("player")
    except Exception as e:
        logger.error(f"Stratz player error: {e}")
        return None


async def stratz_search(nickname: str) -> list:
    """Поиск игрока по нику через Stratz"""
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


# ── OPENDOTA helpers ─────────────────────────────────────────────────────────
async def od_get(path: str, params: dict = None) -> dict | list | None:
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


async def od_player(account_id: int):
    return await od_get(f"/players/{account_id}")


async def od_wl(account_id: int):
    return await od_get(f"/players/{account_id}/wl")


async def od_matches(account_id: int, limit: int = 20):
    return await od_get(f"/players/{account_id}/recentMatches", {"limit": limit})


async def od_heroes(account_id: int):
    return await od_get(f"/players/{account_id}/heroes", {"limit": 10})


async def od_search(nickname: str):
    return await od_get("/search", {"q": nickname})


# ── ANALYSIS ─────────────────────────────────────────────────────────────────
def calc_kda(kills, deaths, assists):
    d = max(deaths, 1)
    return round((kills + assists) / d, 2)


def build_from_stratz(player_data: dict, account_id: int) -> dict:
    sa = player_data.get("steamAccount", {})
    win  = player_data.get("winCount", 0)
    total = player_data.get("matchCount", 1) or 1
    loss = total - win
    winrate = round(win / total * 100, 1)

    rank_tier = sa.get("seasonRank")
    rank_name = rank_tier_to_name(rank_tier)

    heroes_raw = player_data.get("heroesPerformance", []) or []
    heroes = []
    for h in heroes_raw:
        hero = h.get("hero", {}) or {}
        hm = h.get("matchCount", 0) or 1
        hw = h.get("winCount", 0)
        heroes.append({
            "hero_name": hero.get("displayName", "Unknown"),
            "hero_short": hero.get("shortName", ""),
            "matches": hm,
            "wins": hw,
            "winrate": round(hw / hm * 100, 1),
            "kda": calc_kda(h.get("avgKills", 0), h.get("avgDeaths", 1), h.get("avgAssists", 0)),
            "avg_gpm": round(h.get("avgGoldPerMinute", 0)),
            "avg_xpm": round(h.get("avgExperiencePerMinute", 0)),
            "avg_networth": round(h.get("avgNetworth", 0)),
            "avg_imp": round(h.get("avgImp", 0) if h.get("avgImp") else 0),
        })

    matches_raw = player_data.get("matches", []) or []
    matches = []
    for m in matches_raw:
        ps = (m.get("players") or [{}])[0]
        hero_info = ps.get("hero") or {}
        is_radiant = ps.get("isRadiant", True)
        radiant_win = m.get("didRadiantWin", False)
        win_flag = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        dur = m.get("durationSeconds", 0)
        matches.append({
            "match_id": m.get("id"),
            "hero": hero_info.get("displayName", "Unknown"),
            "hero_short": hero_info.get("shortName", ""),
            "win": win_flag,
            "kills": ps.get("kills", 0),
            "deaths": ps.get("deaths", 0),
            "assists": ps.get("assists", 0),
            "kda": calc_kda(ps.get("kills", 0), ps.get("deaths", 0), ps.get("assists", 0)),
            "gpm": ps.get("goldPerMinute", 0),
            "xpm": ps.get("experiencePerMinute", 0),
            "networth": ps.get("networth", 0),
            "hero_damage": ps.get("heroDamage", 0),
            "tower_damage": ps.get("towerDamage", 0),
            "healing": ps.get("heroHealingDone", 0),
            "last_hits": ps.get("numLastHits", 0),
            "denies": ps.get("numDenies", 0),
            "duration_min": dur // 60,
            "duration_sec": dur % 60,
            "end_time": m.get("endDateTime"),
            "game_mode": m.get("gameMode", ""),
        })

    # Тренды по последним 20 матчам
    trend = compute_trend(matches)

    return {
        "source": "stratz",
        "account_id": account_id,
        "profile": {
            "name": sa.get("name", "Unknown"),
            "avatar": sa.get("avatar", ""),
            "profile_url": sa.get("profileUri", ""),
            "rank": rank_name,
            "rank_tier": rank_tier,
            "is_anonymous": sa.get("isAnonymous", False),
        },
        "stats": {
            "wins": win,
            "losses": loss,
            "total_matches": total,
            "winrate": winrate,
        },
        "top_heroes": heroes,
        "recent_matches": matches,
        "trend": trend,
    }


def build_from_opendota(player: dict, wl: dict, matches: list, heroes: list, account_id: int) -> dict:
    profile_data = player.get("profile", {})
    mmr = player.get("mmr_estimate", {}).get("estimate")
    rank_tier = player.get("rank_tier")

    win  = (wl or {}).get("win", 0)
    loss = (wl or {}).get("lose", 0)
    total = win + loss or 1
    winrate = round(win / total * 100, 1)

    heroes_out = []
    for h in (heroes or [])[:10]:
        hm = h.get("games", 0) or 1
        hw = h.get("win", 0)
        heroes_out.append({
            "hero_name": h.get("hero_id", ""),   # ID, имя нужно резолвить
            "hero_id": h.get("hero_id"),
            "matches": hm,
            "wins": hw,
            "winrate": round(hw / hm * 100, 1),
            "kda": calc_kda(0, 0, 0),            # OpenDota не даёт avg в /heroes
        })

    matches_out = []
    for m in (matches or [])[:20]:
        pslot = m.get("player_slot", 0)
        is_radiant = pslot < 128
        radiant_win = m.get("radiant_win", False)
        win_flag = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)
        dur = m.get("duration", 0)
        matches_out.append({
            "match_id": m.get("match_id"),
            "hero_id": m.get("hero_id"),
            "win": win_flag,
            "kills": m.get("kills", 0),
            "deaths": m.get("deaths", 0),
            "assists": m.get("assists", 0),
            "kda": calc_kda(m.get("kills", 0), m.get("deaths", 0), m.get("assists", 0)),
            "gpm": m.get("gold_per_min", 0),
            "xpm": m.get("xp_per_min", 0),
            "hero_damage": m.get("hero_damage", 0),
            "tower_damage": m.get("tower_damage", 0),
            "healing": m.get("hero_healing", 0),
            "last_hits": m.get("last_hits", 0),
            "denies": m.get("denies", 0),
            "duration_min": dur // 60,
            "duration_sec": dur % 60,
            "end_time": m.get("start_time"),
            "game_mode": m.get("game_mode", 0),
        })

    trend = compute_trend(matches_out)

    return {
        "source": "opendota",
        "account_id": account_id,
        "profile": {
            "name": profile_data.get("personaname", "Unknown"),
            "avatar": profile_data.get("avatarfull", ""),
            "profile_url": profile_data.get("profileurl", ""),
            "rank": rank_tier_to_name(rank_tier),
            "rank_tier": rank_tier,
            "mmr_estimate": mmr,
            "is_anonymous": False,
        },
        "stats": {
            "wins": win,
            "losses": loss,
            "total_matches": total,
            "winrate": winrate,
        },
        "top_heroes": heroes_out,
        "recent_matches": matches_out,
        "trend": trend,
    }


def compute_trend(matches: list) -> dict:
    if not matches:
        return {}
    last5  = matches[:5]
    last20 = matches[:20]

    def avg(lst, key):
        vals = [m.get(key, 0) or 0 for m in lst]
        return round(sum(vals) / len(vals), 2) if vals else 0

    def wr(lst):
        if not lst:
            return 0
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


def rank_tier_to_name(rank_tier) -> str:
    if rank_tier is None:
        return "Не откалиброван"
    tiers = {
        1: "Герольд", 2: "Страж", 3: "Рыцарь", 4: "Витязь",
        5: "Лорд", 6: "Легенда", 7: "Древний", 8: "Божество", 9: "Иммортал",
    }
    tier = int(str(rank_tier)[0]) if rank_tier else 0
    star = int(str(rank_tier)[-1]) if rank_tier and len(str(rank_tier)) > 1 else 0
    name = tiers.get(tier, "Неизвестно")
    return f"{name} {'⭐' * star}" if star else name


# ── SEARCH with fallback ─────────────────────────────────────────────────────
async def search_player_combined(query: str) -> list:
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
                    "avatarfull": p.get("avatarfull", ""),
                }
                for p in od
            ]
    return results


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "message": "Dota 2 Analyzer API v2.0"}


@app.get("/player")
async def find_player(query: str = Query(..., min_length=1)):
    """Поиск игрока по нику или Steam ID (обычный или 64-bit)"""
    query = query.strip()
    cache_key = f"player:{query}"

    cached = get_cache(cache_key)
    if cached:
        return cached

    # Определяем account_id
    if query.isdigit():
        q_int = int(query)
        account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
    else:
        results = await search_player_combined(query)
        if not results:
            raise HTTPException(status_code=404, detail="Игрок не найден")
        account_id = results[0]["account_id"]

    result = None

    # Пробуем Stratz
    if STRATZ_TOKEN:
        stratz_data = await stratz_player(account_id)
        if stratz_data:
            result = build_from_stratz(stratz_data, account_id)

    # Fallback на OpenDota
    if not result:
        logger.info(f"Stratz недоступен, fallback на OpenDota для {account_id}")
        player, wl, matches, heroes = await asyncio.gather(
            od_player(account_id),
            od_wl(account_id),
            od_matches(account_id),
            od_heroes(account_id),
        )
        if not player:
            raise HTTPException(status_code=404, detail="Профиль не найден")
        result = build_from_opendota(player, wl, matches, heroes, account_id)

    set_cache(cache_key, result)
    return result


@app.get("/search")
async def search(q: str = Query(..., min_length=1)):
    """Поиск игроков по нику"""
    results = await search_player_combined(q)
    return results[:5]


@app.get("/matches")
async def get_matches(player_id: int = Query(...)):
    """Последние матчи игрока"""
    cache_key = f"matches:{player_id}"
    cached = get_cache(cache_key)
    if cached:
        return cached

    # Пробуем через /player эндпоинт (уже кэшировано)
    matches = await od_matches(player_id)
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

    heroes = await od_heroes(player_id)
    set_cache(cache_key, heroes)
    return heroes


# ── TELEGRAM WEBHOOK ──────────────────────────────────────────────────────────
async def tg_send(chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload)


def format_player_message(data: dict) -> str:
    p = data["profile"]
    s = data["stats"]
    t = data.get("trend", {})
    streak = t.get("streak", {})

    rank = p.get("rank", "?")
    anon = " 🔒" if p.get("is_anonymous") else ""
    src_icon = "⚡" if data.get("source") == "stratz" else "📊"

    streak_str = ""
    if streak.get("count", 0) >= 2:
        emoji = "🔥" if streak["type"] == "win" else "❄️"
        streak_str = f"\n{emoji} Серия: {streak['count']} {'победа' if streak['type'] == 'win' else 'поражений'} подряд"

    heroes_lines = ""
    for i, h in enumerate(data.get("top_heroes", [])[:5], 1):
        name = h.get("hero_name") or h.get("hero_id", "?")
        heroes_lines += f"  {i}. {name} — {h['matches']} игр, WR {h['winrate']}%, KDA {h['kda']}\n"

    matches_lines = ""
    for m in data.get("recent_matches", [])[:5]:
        hero = m.get("hero") or f"Hero#{m.get('hero_id','?')}"
        result = "✅" if m["win"] else "❌"
        matches_lines += (
            f"  {result} {hero} — "
            f"{m['kills']}/{m['deaths']}/{m['assists']} "
            f"({m['duration_min']}:{m['duration_sec']:02d})\n"
        )

    return (
        f"{src_icon} <b>{p['name']}</b>{anon}\n"
        f"🏅 Ранг: {rank}\n"
        f"📈 Винрейт: {s['winrate']}% ({s['wins']}W / {s['losses']}L)\n"
        f"🎮 Всего матчей: {s['total_matches']}\n"
        f"\n📊 Тренды:\n"
        f"  Последние 5:  WR {t.get('last5_winrate', '?')}%, KDA {t.get('last5_avg_kda', '?')}\n"
        f"  Последние 20: WR {t.get('last20_winrate', '?')}%, GPM {t.get('last20_avg_gpm', '?')}\n"
        f"{streak_str}\n"
        f"\n🦸 Топ герои:\n{heroes_lines}"
        f"\n🕹 Последние матчи:\n{matches_lines}"
        f"\n🔗 <a href='https://stratz.com/players/{data['account_id']}'>Профиль на Stratz</a>"
    )


@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()

    if "message" not in data:
        return {"ok": True}

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").strip()

    if text == "/start":
        keyboard = None
        if WEBAPP_URL:
            keyboard = {
                "inline_keyboard": [[
                    {"text": "🎮 Открыть анализатор", "web_app": {"url": WEBAPP_URL}}
                ]]
            }
        await tg_send(
            chat_id,
            "👋 <b>Dota 2 Analyzer</b>\n\n"
            "Отправь мне ник или Steam ID игрока, и я покажу:\n"
            "• Ранг и винрейт\n"
            "• Топ героев\n"
            "• Последние матчи\n"
            "• Серии побед/поражений\n"
            "• Тренды KDA и GPM\n\n"
            "Или открой Web App 👇",
            reply_markup=keyboard,
        )

    elif text == "/help":
        await tg_send(
            chat_id,
            "📖 <b>Как использовать:</b>\n\n"
            "• Напиши ник: <code>Miracle-</code>\n"
            "• Или Steam32 ID: <code>105248644</code>\n"
            "• Или Steam64 ID: <code>76561198065514372</code>\n\n"
            "Данные берутся из Stratz API (с fallback на OpenDota).",
        )

    elif text.startswith("/"):
        await tg_send(chat_id, "❓ Неизвестная команда. Напиши /help")

    else:
        # Ищем игрока по тексту
        await tg_send(chat_id, f"🔍 Ищу <b>{text}</b>...")
        try:
            # Повторяем логику /player эндпоинта
            query = text
            if query.isdigit():
                q_int = int(query)
                account_id = steam64_to_account_id(q_int) if q_int > 76561197960265728 else q_int
            else:
                results = await search_player_combined(query)
                if not results:
                    await tg_send(chat_id, "❌ Игрок не найден. Проверь ник или ID.")
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
                        od_player(account_id),
                        od_wl(account_id),
                        od_matches(account_id),
                        od_heroes(account_id),
                    )
                    if not player:
                        await tg_send(chat_id, "❌ Профиль не найден или приватный.")
                        return {"ok": True}
                    result = build_from_opendota(player, wl, matches, heroes, account_id)

                set_cache(f"player:{query}", result)

            msg = format_player_message(result)
            keyboard = None
            if WEBAPP_URL:
                keyboard = {
                    "inline_keyboard": [[
                        {
                            "text": "📊 Подробный анализ",
                            "web_app": {"url": f"{WEBAPP_URL}?player_id={account_id}"}
                        }
                    ]]
                }
            await tg_send(chat_id, msg, reply_markup=keyboard)

        except Exception as e:
            logger.error(f"Webhook error: {e}")
            await tg_send(chat_id, "⚠️ Ошибка при получении данных. Попробуй позже.")

    return {"ok": True}
