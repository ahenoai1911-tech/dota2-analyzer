import httpx
import logging

logger = logging.getLogger(__name__)

OPENDOTA_URL = "https://api.opendota.com/api"


async def search_player(query: str) -> list:
    """Поиск игрока по нику"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{OPENDOTA_URL}/search", params={"q": query})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка поиска игрока: {e}")
            return []


async def get_player(player_id: int) -> dict:
    """Получить профиль игрока"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{OPENDOTA_URL}/players/{player_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения профиля: {e}")
            return {}


async def get_player_wl(player_id: int) -> dict:
    """Получить Win/Loss игрока"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{OPENDOTA_URL}/players/{player_id}/wl")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения W/L: {e}")
            return {"win": 0, "lose": 0}


async def get_recent_matches(player_id: int, limit: int = 20) -> list:
    """Получить последние матчи"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{OPENDOTA_URL}/players/{player_id}/recentMatches",
                params={"limit": limit}
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения матчей: {e}")
            return []


async def get_player_heroes(player_id: int) -> list:
    """Получить статистику по героям"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{OPENDOTA_URL}/players/{player_id}/heroes")
            resp.raise_for_status()
            data = resp.json()
            # Сортируем по количеству игр
            return sorted(data, key=lambda x: x.get("games", 0), reverse=True)[:5]
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения героев: {e}")
            return []