import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

SGDB_API_V2 = "https://www.steamgriddb.com/api/v2"
DEFAULT_STEAM_APPID = 418370
DEFAULT_OUTPUT = "steamgriddb_first_post_assets.json"


def load_key_from_env_file() -> str:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return ""

    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key.strip() == "STEAMGRIDDB_API_KEY":
            return value.strip().strip('"').strip("'")

    return ""


def get_api_key() -> str:
    api_key = os.getenv("STEAMGRIDDB_API_KEY", "").strip()
    if not api_key:
        api_key = load_key_from_env_file()
    if not api_key:
        raise RuntimeError(
            "STEAMGRIDDB_API_KEY belum di-set di environment/.env. "
            "Contoh .env: STEAMGRIDDB_API_KEY=API_KEY_KAMU"
        )
    return api_key


def build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "SteamGridDBFirstPostFetcher/1.0",
    }


def get_sgdb_game_from_steam_appid(steam_appid: int, headers: dict[str, str]) -> dict[str, Any] | None:
    url = f"{SGDB_API_V2}/games/steam/{steam_appid}"
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        return None
    return payload.get("data")


def get_first_asset_url(game_id: int, asset_type: str, headers: dict[str, str]) -> str | None:
    url = f"{SGDB_API_V2}/{asset_type}/game/{game_id}"
    params = {"page": 0, "limit": 1}
    response = requests.get(url, headers=headers, params=params, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or []
    if not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    return first.get("url")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ambil URL pertama grid/heroes/icons SGDB berdasarkan Steam AppID."
    )
    parser.add_argument("steam_appid", nargs="?", type=int, default=DEFAULT_STEAM_APPID)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    headers = build_headers(get_api_key())

    sgdb_game = get_sgdb_game_from_steam_appid(args.steam_appid, headers)
    if not sgdb_game:
        raise RuntimeError(f"Steam AppID {args.steam_appid} tidak ditemukan di SteamGridDB.")

    sgdb_game_id = sgdb_game.get("id")

    result = {
        "steam_appid": args.steam_appid,
        "sgdb_game_id": sgdb_game_id,
        "grid_url": get_first_asset_url(sgdb_game_id, "grids", headers),
        "heroes_url": get_first_asset_url(sgdb_game_id, "heroes", headers),
        "icons_url": get_first_asset_url(sgdb_game_id, "icons", headers),
    }

    with open(args.output, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
