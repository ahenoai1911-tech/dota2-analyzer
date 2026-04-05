def calc_kda(kills: int, deaths: int, assists: int) -> float:
    """Считает KDA"""
    if deaths == 0:
        return round((kills + assists), 2)
    return round((kills + assists) / deaths, 2)


def calc_winrate(wins: int, losses: int) -> float:
    """Считает WinRate в процентах"""
    total = wins + losses
    if total == 0:
        return 0.0
    return round((wins / total) * 100, 1)


def analyze_matches(matches: list) -> dict:
    """
    Анализирует последние матчи.
    Возвращает средние показатели и список KDA по матчам.
    """
    if not matches:
        return {
            "avg_kills": 0,
            "avg_deaths": 0,
            "avg_assists": 0,
            "avg_kda": 0,
            "avg_gpm": 0,
            "avg_xpm": 0,
            "kda_history": [],
            "wins": 0,
            "losses": 0,
        }

    total_kills = total_deaths = total_assists = 0
    total_gpm = total_xpm = 0
    wins = losses = 0
    kda_history = []

    for m in matches:
        k = m.get("kills", 0)
        d = m.get("deaths", 0)
        a = m.get("assists", 0)
        gpm = m.get("gold_per_min", 0)
        xpm = m.get("xp_per_min", 0)

        total_kills += k
        total_deaths += d
        total_assists += a
        total_gpm += gpm
        total_xpm += xpm

        # Win/Loss из матча
        player_slot = m.get("player_slot", 0)
        radiant_win = m.get("radiant_win", False)
        is_radiant = player_slot < 128
        won = (is_radiant and radiant_win) or (not is_radiant and not radiant_win)

        if won:
            wins += 1
        else:
            losses += 1

        kda_history.append({
            "match_id": m.get("match_id"),
            "kda": calc_kda(k, d, a),
            "won": won,
            "kills": k,
            "deaths": d,
            "assists": a,
            "hero_id": m.get("hero_id", 0),
        })

    count = len(matches)

    return {
        "avg_kills": round(total_kills / count, 1),
        "avg_deaths": round(total_deaths / count, 1),
        "avg_assists": round(total_assists / count, 1),
        "avg_kda": calc_kda(
            total_kills // count,
            total_deaths // count,
            total_assists // count
        ),
        "avg_gpm": round(total_gpm / count),
        "avg_xpm": round(total_xpm / count),
        "kda_history": kda_history,
        "wins": wins,
        "losses": losses,
    }


def get_advice(winrate: float, avg_kda: float, avg_deaths: float) -> list:
    """
    Генерирует советы на основе статистики.
    Без GPT — просто логика.
    """
    advice = []

    # Советы по WinRate
    if winrate < 45:
        advice.append({
            "type": "danger",
            "icon": "💀",
            "text": f"WinRate {winrate}% — критически низкий. Сделай перерыв и пересмотри свой пул героев."
        })
    elif winrate < 50:
        advice.append({
            "type": "warning",
            "icon": "⚠️",
            "text": f"WinRate {winrate}% — немного ниже нормы. Сфокусируйся на 2-3 героях."
        })
    elif winrate >= 55:
        advice.append({
            "type": "success",
            "icon": "🏆",
            "text": f"WinRate {winrate}% — отличный результат! Продолжай в том же духе."
        })
    else:
        advice.append({
            "type": "info",
            "icon": "✅",
            "text": f"WinRate {winrate}% — хороший показатель, выше среднего."
        })

    # Советы по KDA
    if avg_kda < 1.5:
        advice.append({
            "type": "danger",
            "icon": "☠️",
            "text": f"KDA {avg_kda} — очень низкий. Избегай агрессивных действий без поддержки команды."
        })
    elif avg_kda < 2.5:
        advice.append({
            "type": "warning",
            "icon": "⚔️",
            "text": f"KDA {avg_kda} — есть куда расти. Старайся реже умирать в поздней игре."
        })
    elif avg_kda >= 4.0:
        advice.append({
            "type": "success",
            "icon": "🌟",
            "text": f"KDA {avg_kda} — превосходно! Ты эффективно используешь своего героя."
        })
    else:
        advice.append({
            "type": "info",
            "icon": "👍",
            "text": f"KDA {avg_kda} — хороший показатель."
        })

    # Советы по смертям
    if avg_deaths > 8:
        advice.append({
            "type": "danger",
            "icon": "💊",
            "text": f"В среднем {avg_deaths} смертей за игру — слишком много. Покупай больше защитных предметов."
        })
    elif avg_deaths > 5:
        advice.append({
            "type": "warning",
            "icon": "🛡️",
            "text": f"В среднем {avg_deaths} смертей — старайся осторожнее действовать на линии."
        })

    return advice


def full_analysis(player: dict, wl: dict, matches: list) -> dict:
    """
    Полный анализ игрока.
    Собирает всё в один объект.
    """
    wins = wl.get("win", 0)
    losses = wl.get("lose", 0)
    winrate = calc_winrate(wins, losses)

    match_stats = analyze_matches(matches)
    avg_kda = match_stats["avg_kda"]
    avg_deaths = match_stats["avg_deaths"]

    advice = get_advice(winrate, avg_kda, avg_deaths)

    profile = player.get("profile", {})

    return {
        "profile": {
            "account_id": profile.get("account_id"),
            "name": profile.get("personaname", "Unknown"),
            "avatar": profile.get("avatarfull", ""),
            "rank": player.get("rank_tier"),
        },
        "stats": {
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "total_games": wins + losses,
            "avg_kills": match_stats["avg_kills"],
            "avg_deaths": match_stats["avg_deaths"],
            "avg_assists": match_stats["avg_assists"],
            "avg_kda": avg_kda,
            "avg_gpm": match_stats["avg_gpm"],
            "avg_xpm": match_stats["avg_xpm"],
            "recent_wins": match_stats["wins"],
            "recent_losses": match_stats["losses"],
        },
        "kda_history": match_stats["kda_history"],
        "advice": advice,
    }