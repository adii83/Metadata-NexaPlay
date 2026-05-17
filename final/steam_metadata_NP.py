import argparse
import gzip
import io
import json
import os
import re
import subprocess
import sys
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

try:
    import ijson  # type: ignore
except ImportError:
    ijson = None


STEAMGRIDDB_API_KEY = os.getenv("STEAMGRIDDB_API_KEY")

SGDB_API_V2 = "https://www.steamgriddb.com/api/v2"
SGDB_PUBLIC = "https://www.steamgriddb.com/api/public"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

APPID_POPULER_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/appid_populer.json"
)
FIX_GAMES_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/fix_games.json"
)
NEW_FIX_GAMES_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/new_fix_games.json"
)
OVERRIDE_DATA_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/override_data.json"
)
STEAM_DATA_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/steam_data.json"
)
STEAM_DATA_GZ_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/steam_data.json.gz"
)

LANGUAGE = "english"
COUNTRY = "us"
DEFAULT_OUTPUT_DIR = "dist"
DEFAULT_FILE_PREFIX = "steam_metadata_NP"
DEFAULT_MAX_FILE_SIZE_MB = 25
DEFAULT_SLEEP_SECONDS = 1.0
DEFAULT_BATCH_SIZE = 2000
DEFAULT_SNAPSHOT_NAME = "steam_metadata_NP_sources.json"
DEFAULT_CHUNKS_DIR_NAME = "chunks"
DEFAULT_CONTINUOUS_DELAY_SECONDS = 0.0
DEFAULT_AUTO_PUSH_EVERY = 500
DEFAULT_PRODUCTION_BATCH_SIZE = 1
DEFAULT_REPLACE_RETRIES = 8
DEFAULT_REPLACE_RETRY_DELAY_SECONDS = 0.25

STORE_ASSET_KEYS = [
    "header",
    "library_capsule",
    "library_capsule_2x",
    "library_hero",
    "library_hero_2x",
    "library_logo",
    "library_logo_2x",
    "clienticon",
    "icon",
    "capsule_image",
    "capsule_imagev5",
    "background",
    "background_raw",
    "screenshots",
    "movies",
    "embedded_media",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_json(url: str, timeout: int = 60) -> Any:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "SteamMetadataNP/2.0"},
    )
    response.raise_for_status()
    return response.json()


def fetch_and_decompress_gz(url: str, timeout: int = 60) -> Any:
    """Fetch gzip-compressed JSON and decompress on-the-fly."""
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "SteamMetadataNP/2.0"},
        stream=True,
    )
    response.raise_for_status()
    with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz:
        return json.load(gz)


def extract_urls_from_html(raw_html: str | None) -> list[str]:
    if not raw_html:
        return []

    urls = re.findall(r'https://[^"\'>\s]+', raw_html)
    unique_urls: list[str] = []
    seen: set[str] = set()

    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls


def build_embedded_media(data: dict) -> dict:
    about_urls = extract_urls_from_html(data.get("about_the_game"))
    detailed_urls = extract_urls_from_html(data.get("detailed_description"))

    all_embed_urls: list[str] = []
    seen: set[str] = set()

    for url in about_urls + detailed_urls:
        if url not in seen:
            seen.add(url)
            all_embed_urls.append(url)

    return {
        "about_the_game_urls": about_urls,
        "detailed_description_urls": detailed_urls,
        "all_urls": all_embed_urls,
        "videos_mp4": [url for url in all_embed_urls if ".mp4" in url.lower()],
        "videos_webm": [url for url in all_embed_urls if ".webm" in url.lower()],
        "images": [
            url for url in all_embed_urls
            if re.search(r"\.(jpg|jpeg|png|gif|webp|avif)(\?|$)", url, re.I)
        ],
    }


def parse_appid_value(raw_value: Any) -> int | None:
    text = str(raw_value).strip()
    if not text.isdigit():
        return None
    return int(text)


def load_popular_appids(url: str) -> list[int]:
    payload = fetch_json(url)
    if not isinstance(payload, list):
        raise RuntimeError("appid_populer.json harus berupa array App ID.")

    appids: list[int] = []
    for item in payload:
        appid = parse_appid_value(item)
        if appid is not None:
            appids.append(appid)

    return appids


def load_fix_games_appids(url: str) -> list[int]:
    """Load App IDs from fix_games.json which has structure: {"games": [{"appid": ..., ...}]}"""
    payload = fetch_json(url)
    if not isinstance(payload, dict) or "games" not in payload:
        raise RuntimeError("fix_games.json harus berupa object dengan key 'games'.")
    
    games = payload.get("games", [])
    if not isinstance(games, list):
        raise RuntimeError("fix_games.json['games'] harus berupa array.")
    
    appids: list[int] = []
    for item in games:
        if isinstance(item, dict):
            appid = parse_appid_value(item.get("appid"))
            if appid is not None:
                appids.append(appid)
    
    return appids


def load_new_fix_games_appids(url: str) -> list[int]:
    """Load App IDs from new_fix_games.json which is a direct array of integers."""
    payload = fetch_json(url)
    if not isinstance(payload, list):
        raise RuntimeError("new_fix_games.json harus berupa array App ID.")
    
    appids: list[int] = []
    for item in payload:
        appid = parse_appid_value(item)
        if appid is not None:
            appids.append(appid)
    
    return appids


def load_steam_data_gz_appids(url: str) -> list[int]:
    """Load App IDs from steam_data.json.gz (compressed version with full dataset)."""
    payload = fetch_and_decompress_gz(url, timeout=180)
    if not isinstance(payload, dict):
        raise RuntimeError("steam_data.json.gz harus berupa object keyed by App ID.")
    
    appids: list[int] = []
    for key in payload.keys():
        appid = parse_appid_value(key)
        if appid is not None:
            appids.append(appid)
    
    return appids


def load_object_appids(url: str) -> list[int]:
    if ijson is not None:
        response = requests.get(
            url,
            stream=True,
            timeout=180,
            headers={"User-Agent": "SteamMetadataNP/2.0"},
        )
        response.raise_for_status()
        response.raw.decode_content = True

        appids: list[int] = []
        for prefix, event, value in ijson.parse(response.raw):
            if prefix == "" and event == "map_key":
                appid = parse_appid_value(value)
                if appid is not None:
                    appids.append(appid)
        return appids

    payload = fetch_json(url, timeout=180)
    if not isinstance(payload, dict):
        raise RuntimeError("Source object App ID harus berupa object keyed by App ID.")

    appids: list[int] = []
    for key in payload.keys():
        appid = parse_appid_value(key)
        if appid is not None:
            appids.append(appid)
    return appids


def load_existing_archive(output_dir: Path, file_prefix: str) -> dict[int, dict]:
    archive: dict[int, dict] = {}

    if not output_dir.exists():
        return archive

    chunks_dir = output_dir / DEFAULT_CHUNKS_DIR_NAME
    # Keep backward compatibility: read both old root chunks and new chunks folder.
    search_dirs = [output_dir, chunks_dir]
    pattern = f"{file_prefix}_part*.json"

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for chunk_path in sorted(search_dir.glob(pattern)):
            payload = json.loads(chunk_path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                continue

            for key, value in payload.items():
                appid = parse_appid_value(key)
                if appid is not None and isinstance(value, dict):
                    archive[appid] = value

    return archive


def get_progress_path(output_dir: Path, file_prefix: str) -> Path:
    return output_dir / f"{file_prefix}_progress.json"


def load_progress(output_dir: Path, file_prefix: str) -> dict:
    progress_path = get_progress_path(output_dir, file_prefix)
    if not progress_path.exists():
        return {
            "backlog_cursor": 0,
            "known_source_appids": [],
            "failed_once_appids": [],
            "failed_twice_appids": [],
        }

    payload = json.loads(progress_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return {
            "backlog_cursor": 0,
            "known_source_appids": [],
            "failed_once_appids": [],
            "failed_twice_appids": [],
        }

    payload.setdefault("backlog_cursor", 0)
    payload.setdefault("known_source_appids", [])
    payload.setdefault("failed_once_appids", [])
    payload.setdefault("failed_twice_appids", [])
    return payload


def write_progress(output_dir: Path, file_prefix: str, progress: dict) -> None:
    write_json_file(progress, get_progress_path(output_dir, file_prefix))


def get_snapshot_path(output_dir: Path) -> Path:
    return output_dir / DEFAULT_SNAPSHOT_NAME


def save_source_snapshot(
    output_dir: Path,
    appids: list[int],
    source_map: dict[int, str],
    source_counts: dict[str, int],
) -> Path:
    payload = {
        "generated_at": utc_now_iso(),
        "appids": appids,
        "source_map": {str(appid): source for appid, source in source_map.items()},
        "source_counts": source_counts,
        "sources": list(source_counts.keys()),
    }
    snapshot_path = get_snapshot_path(output_dir)
    write_json_file(payload, snapshot_path)
    return snapshot_path


def load_source_snapshot(
    output_dir: Path,
) -> tuple[list[int], dict[int, str], dict[str, int]] | None:
    snapshot_path = get_snapshot_path(output_dir)
    if not snapshot_path.exists():
        return None

    payload = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return None

    raw_appids = payload.get("appids")
    raw_source_map = payload.get("source_map")
    raw_source_counts = payload.get("source_counts")

    if not isinstance(raw_appids, list) or not isinstance(raw_source_map, dict) or not isinstance(raw_source_counts, dict):
        return None

    appids: list[int] = []
    for item in raw_appids:
        appid = parse_appid_value(item)
        if appid is not None:
            appids.append(appid)

    source_map: dict[int, str] = {}
    for key, value in raw_source_map.items():
        appid = parse_appid_value(key)
        if appid is not None and isinstance(value, str):
            source_map[appid] = value

    source_counts: dict[str, int] = {}
    for key, value in raw_source_counts.items():
        if isinstance(key, str) and isinstance(value, int):
            source_counts[key] = value

    return appids, source_map, source_counts


def build_prioritized_appids(
    popular_url: str,
    fix_games_url: str,
    new_fix_games_url: str,
    override_url: str,
    steam_data_url: str,
    steam_data_gz_url: str,
) -> tuple[list[int], dict[int, str], dict[str, int]]:
    prioritized_lists = [
        ("appid_populer", load_popular_appids(popular_url)),
        ("fix_games", load_fix_games_appids(fix_games_url)),
        ("new_fix_games", load_new_fix_games_appids(new_fix_games_url)),
        ("override_data", load_object_appids(override_url)),
        ("steam_data", load_object_appids(steam_data_url)),
        ("steam_data_gz", load_steam_data_gz_appids(steam_data_gz_url)),
    ]

    appids: list[int] = []
    source_map: dict[int, str] = {}
    seen: set[int] = set()

    for source_name, source_appids in prioritized_lists:
        for appid in source_appids:
            if appid in seen:
                continue
            seen.add(appid)
            appids.append(appid)
            source_map[appid] = source_name

    source_counts = {source_name: len(source_appids) for source_name, source_appids in prioritized_lists}
    return appids, source_map, source_counts


def build_retry_appids(
    existing_archive: dict[int, dict],
    excluded_appids: set[int],
) -> list[int]:
    retry_appids: list[int] = []
    for appid, payload in existing_archive.items():
        if not payload.get("success", False) and appid not in excluded_appids:
            retry_appids.append(appid)
    return retry_appids


def build_backlog_batch(
    appids: list[int],
    cursor: int,
    batch_remaining: int,
    blocked_appids: set[int],
) -> tuple[list[int], int]:
    if not appids or batch_remaining <= 0:
        return [], cursor

    total = len(appids)
    selected: list[int] = []
    index = cursor % total
    scanned = 0

    while scanned < total and len(selected) < batch_remaining:
        appid = appids[index]
        if appid not in blocked_appids:
            selected.append(appid)
            blocked_appids.add(appid)

        index = (index + 1) % total
        scanned += 1

    return selected, index


def select_batch_appids(
    appids: list[int],
    existing_archive: dict[int, dict],
    progress: dict,
    batch_size: int,
    force_refresh: bool,
) -> tuple[list[int], dict]:
    if not appids:
        return [], {
            "new_count": 0,
            "retry_failed_count": 0,
            "backlog_count": 0,
            "backlog_cursor_before": 0,
            "backlog_cursor_after": 0,
            "skipped_success_existing": 0,
        }

    previous_known_source = {
        appid
        for appid in (
            parse_appid_value(value) for value in progress.get("known_source_appids", [])
        )
        if appid is not None
    }
    failed_once_appids = {
        appid
        for appid in (
            parse_appid_value(value) for value in progress.get("failed_once_appids", [])
        )
        if appid is not None
    }
    failed_twice_appids = {
        appid
        for appid in (
            parse_appid_value(value) for value in progress.get("failed_twice_appids", [])
        )
        if appid is not None
    }
    processed_success = {
        appid for appid, payload in existing_archive.items() if payload.get("success", False)
    }
    # Hindari loop App ID gagal yang sama berulang-ulang.
    # App ID yang sudah masuk failed_once/failed_twice tidak diretry lagi di putaran berikutnya.
    retry_failed_appids = build_retry_appids(
        existing_archive,
        failed_once_appids | failed_twice_appids,
    )
    cursor_before = int(progress.get("backlog_cursor", 0) or 0)

    if force_refresh:
        refresh_batch, cursor_after = build_backlog_batch(
            appids=appids,
            cursor=cursor_before,
            batch_remaining=batch_size,
            blocked_appids=set(),
        )
        return refresh_batch, {
            "new_count": 0,
            "retry_failed_count": 0,
            "backlog_count": len(refresh_batch),
            "backlog_cursor_before": cursor_before,
            "backlog_cursor_after": cursor_after,
            "skipped_success_existing": 0,
        }

    new_appids = [
        appid
        for appid in appids
        if appid not in previous_known_source
        and appid not in processed_success
        and appid not in failed_once_appids
        and appid not in failed_twice_appids
    ]

    blocked_appids = set(processed_success)
    blocked_appids.update(new_appids)
    blocked_appids.update(failed_once_appids)
    blocked_appids.update(failed_twice_appids)

    # Prioritas utama: App ID baru dari sumber terbaru harus didahulukan.
    selected: list[int] = []
    selected.extend(new_appids[:batch_size])

    if len(selected) < batch_size:
        remaining = batch_size - len(selected)
        selected.extend(retry_failed_appids[:remaining])

    backlog_remaining = batch_size - len(selected)
    backlog_batch: list[int] = []
    cursor_after = cursor_before

    if backlog_remaining > 0:
        blocked_for_backlog = set(blocked_appids)
        blocked_for_backlog.update(selected)
        backlog_batch, cursor_after = build_backlog_batch(
            appids=appids,
            cursor=cursor_before,
            batch_remaining=backlog_remaining,
            blocked_appids=blocked_for_backlog,
        )
        selected.extend(backlog_batch)

    stats = {
        "new_count": len([appid for appid in selected if appid in set(new_appids)]),
        "retry_failed_count": len([appid for appid in selected if appid in set(retry_failed_appids)]),
        "backlog_count": len(backlog_batch),
        "backlog_cursor_before": cursor_before,
        "backlog_cursor_after": cursor_after,
        "skipped_success_existing": len(processed_success),
    }
    return selected, stats


def get_steam_appdetails(appid: int) -> dict:
    params = {
        "appids": appid,
        "l": LANGUAGE,
        "cc": COUNTRY,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 SteamMetadataNP/2.0"
    }

    response = requests.get(
        STEAM_APPDETAILS_URL,
        params=params,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    result = payload.get(str(appid))

    if not result:
        raise RuntimeError("Response tidak berisi App ID tersebut.")

    if not result.get("success"):
        raise RuntimeError("Steam mengembalikan success=false.")

    return result["data"]


def get_sgdb_game_from_steam_appid(steam_appid: int) -> dict | None:
    if not STEAMGRIDDB_API_KEY:
        raise RuntimeError(
            "STEAMGRIDDB_API_KEY belum di-set. Simpan di environment variable atau GitHub Actions Secret."
        )

    url = f"{SGDB_API_V2}/games/steam/{steam_appid}"

    headers = {
        "Authorization": f"Bearer {STEAMGRIDDB_API_KEY}",
        "Accept": "application/json",
        "User-Agent": "SteamMetadataNP/2.0",
    }

    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code == 404:
        return None

    response.raise_for_status()

    payload = response.json()
    if not payload.get("success"):
        return None

    return payload.get("data")


def fetch_public_game(sgdb_game_id: int) -> dict:
    url = f"{SGDB_PUBLIC}/game/{sgdb_game_id}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.steamgriddb.com/game/{sgdb_game_id}/grids",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def build_steam_asset_url(steam_appid: str, asset_path: str, mtime: int | None) -> str | None:
    if not asset_path:
        return None

    return (
        "https://shared.steamstatic.com/store_item_assets/"
        f"steam/apps/{steam_appid}/{asset_path}?t={mtime}"
    )


def add_asset(
    assets: dict,
    key: str,
    steam_appid: str,
    path: str | None,
    mtime: int | None,
    language: str | None = None,
) -> None:
    if language != LANGUAGE:
        return

    if not path:
        return

    item = {
        "path": path,
        "url": build_steam_asset_url(steam_appid, path, mtime),
        "language": language,
    }

    assets.setdefault(key, []).append(item)


def add_english_assets_from_dict(
    assets: dict,
    key: str,
    steam_appid: str,
    data_dict: dict,
    mtime: int | None,
) -> None:
    if not isinstance(data_dict, dict):
        return

    english_path = data_dict.get(LANGUAGE)
    add_asset(
        assets=assets,
        key=key,
        steam_appid=steam_appid,
        path=english_path,
        mtime=mtime,
        language=LANGUAGE,
    )


def extract_original_assets(public_payload: dict) -> dict | None:
    data = public_payload.get("data", {})
    steam_data = data.get("platforms", {}).get("steam", {})

    steam_appid = steam_data.get("id")
    sgdb_game_id = steam_data.get("gameId")
    metadata = steam_data.get("metadata", {})

    if not steam_appid or not metadata:
        return None

    mtime = metadata.get("store_asset_mtime")
    assets: dict = {}

    add_english_assets_from_dict(
        assets=assets,
        key="header",
        steam_appid=steam_appid,
        data_dict=metadata.get("header_image_full", {}),
        mtime=mtime,
    )

    library_capsule_full = metadata.get("library_capsule_full", {})
    add_english_assets_from_dict(
        assets=assets,
        key="library_capsule",
        steam_appid=steam_appid,
        data_dict=library_capsule_full.get("image", {}),
        mtime=mtime,
    )
    add_english_assets_from_dict(
        assets=assets,
        key="library_capsule_2x",
        steam_appid=steam_appid,
        data_dict=library_capsule_full.get("image2x", {}),
        mtime=mtime,
    )

    library_hero_full = metadata.get("library_hero_full", {})
    add_english_assets_from_dict(
        assets=assets,
        key="library_hero",
        steam_appid=steam_appid,
        data_dict=library_hero_full.get("image", {}),
        mtime=mtime,
    )
    add_english_assets_from_dict(
        assets=assets,
        key="library_hero_2x",
        steam_appid=steam_appid,
        data_dict=library_hero_full.get("image2x", {}),
        mtime=mtime,
    )

    library_logo_full = metadata.get("library_logo_full", {})
    add_english_assets_from_dict(
        assets=assets,
        key="library_logo",
        steam_appid=steam_appid,
        data_dict=library_logo_full.get("image", {}),
        mtime=mtime,
    )
    add_english_assets_from_dict(
        assets=assets,
        key="library_logo_2x",
        steam_appid=steam_appid,
        data_dict=library_logo_full.get("image2x", {}),
        mtime=mtime,
    )

    clienticon = metadata.get("clienticon")
    icon = metadata.get("icon")

    if clienticon:
        assets["clienticon"] = [
            {
                "hash": clienticon,
                "url": (
                    "https://cdn.cloudflare.steamstatic.com/"
                    f"steamcommunity/public/images/apps/{steam_appid}/{clienticon}.ico"
                ),
            }
        ]

    if icon:
        assets["icon"] = [
            {
                "hash": icon,
                "url": (
                    "https://cdn.cloudflare.steamstatic.com/"
                    f"steamcommunity/public/images/apps/{steam_appid}/{icon}.jpg"
                ),
            }
        ]

    return {
        "success": True,
        "name": data.get("name") or steam_data.get("name"),
        "steam_appid": str(steam_appid),
        "steamgriddb_game_id": sgdb_game_id,
        "store_asset_mtime": mtime,
        "assets_count": sum(len(items) for items in assets.values()),
        "assets": assets,
    }


def fetch_original_assets_by_steam_appid(steam_appid: int) -> dict:
    try:
        sgdb_game = get_sgdb_game_from_steam_appid(steam_appid)
    except Exception as exc:
        return {
            "success": False,
            "steam_appid": str(steam_appid),
            "error_stage": "steamgriddb_lookup",
            "error": str(exc),
            "assets_count": 0,
            "assets": {},
        }

    if not sgdb_game:
        return {
            "success": False,
            "steam_appid": str(steam_appid),
            "error_stage": "steamgriddb_lookup",
            "error": "Steam appid tidak ditemukan di SteamGridDB",
            "assets_count": 0,
            "assets": {},
        }

    sgdb_game_id = sgdb_game.get("id")

    try:
        public_payload = fetch_public_game(sgdb_game_id)
        extracted = extract_original_assets(public_payload)
    except Exception as exc:
        return {
            "success": False,
            "steam_appid": str(steam_appid),
            "steamgriddb_game_id": sgdb_game_id,
            "name": sgdb_game.get("name"),
            "error_stage": "steamgriddb_public",
            "error": str(exc),
            "assets_count": 0,
            "assets": {},
        }

    if not extracted:
        return {
            "success": False,
            "steam_appid": str(steam_appid),
            "steamgriddb_game_id": sgdb_game_id,
            "name": sgdb_game.get("name"),
            "error_stage": "steamgriddb_extract",
            "error": "Original Steam metadata tidak ditemukan",
            "assets_count": 0,
            "assets": {},
        }

    extracted["name"] = extracted.get("name") or sgdb_game.get("name")
    return extracted


def wrap_single_asset(url: str | None) -> list[dict]:
    if not url:
        return []
    return [{"url": url}]


def build_store_assets(store_data: dict) -> dict:
    return {
        "capsule_image": wrap_single_asset(store_data.pop("capsule_image", None)),
        "capsule_imagev5": wrap_single_asset(store_data.pop("capsule_imagev5", None)),
        "background": wrap_single_asset(store_data.pop("background", None)),
        "background_raw": wrap_single_asset(store_data.pop("background_raw", None)),
        "screenshots": store_data.pop("screenshots", []),
        "movies": store_data.pop("movies", []),
        "embedded_media": store_data.pop("embedded_media", {}),
    }


def reorder_assets(assets: dict) -> dict:
    ordered: dict = {}

    for key in STORE_ASSET_KEYS:
        if key in assets:
            ordered[key] = assets[key]

    for key, value in assets.items():
        if key not in ordered:
            ordered[key] = value

    return ordered


def reorder_store_data(store_data: dict) -> dict:
    preferred_order = [
        "type",
        "required_age",
        "is_free",
        "controller_support",
        "dlc",
        "demos",
        "about_the_game",
        "detailed_description",
        "short_description",
        "supported_languages",
        "website",
        "pc_requirements",
        "mac_requirements",
        "linux_requirements",
        "developers",
        "publishers",
        "support_info",
        "legal_notice",
        "drm_notice",
        "price_overview",
        "packages",
        "package_groups",
        "platforms",
        "categories",
        "genres",
        "recommendations",
        "achievements",
        "release_date",
        "content_descriptors",
        "ratings",
    ]

    ordered: dict = {}

    for key in preferred_order:
        if key in store_data:
            ordered[key] = store_data[key]

    for key, value in store_data.items():
        if key not in ordered:
            ordered[key] = value

    return ordered


def count_assets(assets: dict) -> int:
    total = 0
    for value in assets.values():
        if isinstance(value, list):
            total += len(value)
        elif value:
            total += 1
    return total


def build_failure_record(appid: int, stage: str, error: str, source_priority: str) -> dict:
    return {
        "appid": appid,
        "stage": stage,
        "error": error,
        "source_priority": source_priority,
        "timestamp": utc_now_iso(),
    }


def merge_metadata(appid: int, source_priority: str) -> tuple[dict, list[dict]]:
    failures: list[dict] = []
    store_data: dict = {}
    original_assets: dict = {
        "success": False,
        "steam_appid": str(appid),
        "assets": {},
    }

    try:
        store_data = get_steam_appdetails(appid)
        store_data["embedded_media"] = build_embedded_media(store_data)
    except Exception as exc:
        failures.append(
            build_failure_record(appid, "steam_appdetails", str(exc), source_priority)
        )

    original_assets = fetch_original_assets_by_steam_appid(appid)
    if not original_assets.get("success"):
        failures.append(
            build_failure_record(
                appid,
                original_assets.get("error_stage", "steam_original_assets"),
                original_assets.get("error", "Unknown error"),
                source_priority,
            )
        )

    merged_assets = dict(original_assets.get("assets", {}))

    if store_data:
        store_assets = build_store_assets(store_data)
        for key, value in store_assets.items():
            merged_assets[key] = value

    store_name = store_data.get("name")
    store_data.pop("name", None)
    store_data.pop("steam_appid", None)
    store_data.pop("header_image", None)
    store_data = reorder_store_data(store_data)
    merged_assets = reorder_assets(merged_assets)

    return {
        "success": bool(store_data) or original_assets.get("success", False),
        "source_priority": source_priority,
        "fetch_status": {
            "steam_appdetails": bool(store_data),
            "steam_original_assets": original_assets.get("success", False),
        },
        "name": original_assets.get("name") or store_name,
        "steam_appid": original_assets.get("steam_appid", str(appid)),
        "steamgriddb_game_id": original_assets.get("steamgriddb_game_id"),
        "store_asset_mtime": original_assets.get("store_asset_mtime"),
        "assets_count": count_assets(merged_assets),
        "assets": merged_assets,
        "store_data": store_data,
    }, failures


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_chunk_path(output_dir: Path, file_prefix: str, chunk_index: int) -> Path:
    chunks_dir = output_dir / DEFAULT_CHUNKS_DIR_NAME
    ensure_directory(chunks_dir)
    return chunks_dir / f"{file_prefix}_part{chunk_index:03d}.json"


def write_json_file(payload: Any, output_path: Path) -> None:
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_json_file_atomic(payload: Any, output_path: Path) -> None:
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    last_error: Exception | None = None
    for _ in range(DEFAULT_REPLACE_RETRIES):
        try:
            temp_path.replace(output_path)
            return
        except PermissionError as exc:
            # Windows can hold transient locks (indexer/AV/git scanner). Retry shortly.
            last_error = exc
            time.sleep(DEFAULT_REPLACE_RETRY_DELAY_SECONDS)

    with suppress(FileNotFoundError):
        temp_path.unlink()
    if last_error is not None:
        raise last_error


def remove_existing_chunk_files(output_dir: Path, file_prefix: str) -> None:
    # Remove chunks from both old and new locations to avoid stale duplicates.
    for chunk_path in output_dir.glob(f"{file_prefix}_part*.json"):
        chunk_path.unlink()
    chunks_dir = output_dir / DEFAULT_CHUNKS_DIR_NAME
    if chunks_dir.exists():
        for chunk_path in chunks_dir.glob(f"{file_prefix}_part*.json"):
            chunk_path.unlink()


def write_chunk_files(
    entries: list[tuple[int, dict]],
    output_dir: Path,
    file_prefix: str,
    max_file_size_mb: int,
) -> list[dict]:
    max_bytes = max_file_size_mb * 1024 * 1024
    chunk_files: list[dict] = []
    current_chunk: dict[str, dict] = {}
    current_size = 2
    chunk_index = 1
    new_paths: list[Path] = []
    old_paths: set[Path] = set()

    for root in [output_dir, output_dir / DEFAULT_CHUNKS_DIR_NAME]:
        if root.exists():
            for p in root.glob(f"{file_prefix}_part*.json"):
                old_paths.add(p.resolve())

    for appid, payload in entries:
        entry_json = json.dumps({str(appid): payload}, ensure_ascii=False)
        entry_size = len(entry_json.encode("utf-8"))

        if current_chunk and current_size + entry_size > max_bytes:
            chunk_path = build_chunk_path(output_dir, file_prefix, chunk_index)
            write_json_file_atomic(current_chunk, chunk_path)
            new_paths.append(chunk_path.resolve())
            chunk_files.append(
                {
                    "file": chunk_path.name,
                    "entries": len(current_chunk),
                    "size_bytes": chunk_path.stat().st_size,
                }
            )
            chunk_index += 1
            current_chunk = {}
            current_size = 2

        current_chunk[str(appid)] = payload
        current_size += entry_size

    if current_chunk:
        chunk_path = build_chunk_path(output_dir, file_prefix, chunk_index)
        write_json_file_atomic(current_chunk, chunk_path)
        new_paths.append(chunk_path.resolve())
        chunk_files.append(
            {
                "file": chunk_path.name,
                "entries": len(current_chunk),
                "size_bytes": chunk_path.stat().st_size,
            }
        )

    # Cleanup stale chunk files only after new files are safely written.
    for old_path in old_paths:
        if old_path not in set(new_paths):
            try:
                old_path.unlink()
            except FileNotFoundError:
                pass

    return chunk_files


def get_push_state_path(output_dir: Path, file_prefix: str) -> Path:
    return output_dir / f"{file_prefix}_push_state.json"


def load_push_state(output_dir: Path, file_prefix: str) -> dict:
    push_state_path = get_push_state_path(output_dir, file_prefix)
    if not push_state_path.exists():
        return {"pending_processed_since_push": 0, "last_push_at": None}
    payload = json.loads(push_state_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return {"pending_processed_since_push": 0, "last_push_at": None}
    payload.setdefault("pending_processed_since_push", 0)
    payload.setdefault("last_push_at", None)
    return payload


def write_push_state(output_dir: Path, file_prefix: str, state: dict) -> None:
    write_json_file_atomic(state, get_push_state_path(output_dir, file_prefix))


def run_git_command(args: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def try_auto_push(
    repo_dir: Path,
    output_dir: Path,
    file_prefix: str,
    pending_processed_count: int,
) -> tuple[bool, int]:
    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        print("[push] skip | folder bukan repo git")
        return False, pending_processed_count

    commit_user_name = os.getenv("COMMIT_USER_NAME")
    commit_user_email = os.getenv("COMMIT_USER_EMAIL")
    if commit_user_name:
        run_git_command(["git", "config", "user.name", commit_user_name], repo_dir)
    if commit_user_email:
        run_git_command(["git", "config", "user.email", commit_user_email], repo_dir)

    rc, status_out = run_git_command(["git", "status", "--porcelain", "--", "dist"], repo_dir)
    if rc != 0 or not status_out:
        print("[push] skip | tidak ada perubahan")
        return False, pending_processed_count

    rc_add, out_add = run_git_command(["git", "add", "dist"], repo_dir)
    if rc_add != 0:
        print(f"[push] gagal add | {out_add}")
        return False, pending_processed_count

    commit_msg = f"Update Steam metadata archive after {pending_processed_count} appids"
    rc_commit, out_commit = run_git_command(["git", "commit", "-m", commit_msg], repo_dir)
    if rc_commit != 0:
        print(f"[push] gagal commit | {out_commit}")
        return False, pending_processed_count

    rc_push, out_push = run_git_command(["git", "push"], repo_dir)
    if rc_push != 0:
        print(f"[push] gagal push | {out_push}")
        return False, pending_processed_count

    write_push_state(
        output_dir,
        file_prefix,
        {"pending_processed_since_push": 0, "last_push_at": utc_now_iso()},
    )
    print(f"[push] OK | pushed={pending_processed_count} | reset pending=0")
    return True, 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bangun arsip metadata Steam gabungan dari Steam appdetails dan Steam original assets."
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Folder output. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--file-prefix",
        default=DEFAULT_FILE_PREFIX,
        help=f"Prefix nama file output. Default: {DEFAULT_FILE_PREFIX}",
    )
    parser.add_argument(
        "--max-file-size-mb",
        type=int,
        default=DEFAULT_MAX_FILE_SIZE_MB,
        help=f"Maksimum ukuran file per chunk dalam MB. Default: {DEFAULT_MAX_FILE_SIZE_MB}",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Delay antar request App ID. Default: {DEFAULT_SLEEP_SECONDS}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Batasi jumlah App ID untuk test lokal. 0 berarti semua.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Jumlah App ID yang diproses per run. Default: {DEFAULT_BATCH_SIZE}",
    )
    parser.add_argument(
        "--refresh-snapshot",
        action="store_true",
        help="Refresh snapshot sumber App ID dan simpan ke file lokal.",
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="Hanya buat snapshot sumber, lalu keluar (tanpa memproses App ID).",
    )
    parser.add_argument(
        "--appid-populer-url",
        default=APPID_POPULER_URL,
        help="URL sumber appid_populer.json",
    )
    parser.add_argument(
        "--fix-games-url",
        default=FIX_GAMES_URL,
        help="URL sumber fix_games.json",
    )
    parser.add_argument(
        "--new-fix-games-url",
        default=NEW_FIX_GAMES_URL,
        help="URL sumber new_fix_games.json",
    )
    parser.add_argument(
        "--override-data-url",
        default=OVERRIDE_DATA_URL,
        help="URL sumber override_data.json",
    )
    parser.add_argument(
        "--steam-data-url",
        default=STEAM_DATA_URL,
        help="URL sumber steam_data.json",
    )
    parser.add_argument(
        "--steam-data-gz-url",
        default=STEAM_DATA_GZ_URL,
        help="URL sumber steam_data.json.gz",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Proses ulang semua App ID walau sudah ada di output sebelumnya.",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Jalankan mode kontinu langsung di Python (tanpa loop PowerShell).",
    )
    parser.add_argument(
        "--continuous-delay-seconds",
        type=float,
        default=DEFAULT_CONTINUOUS_DELAY_SECONDS,
        help=f"Jeda antar putaran mode kontinu. Default: {DEFAULT_CONTINUOUS_DELAY_SECONDS}",
    )
    parser.add_argument(
        "--auto-push-every",
        type=int,
        default=DEFAULT_AUTO_PUSH_EVERY,
        help=f"Auto push setiap N App ID terproses (0=off). Default: {DEFAULT_AUTO_PUSH_EVERY}",
    )
    args = parser.parse_args()

    # No-arg mode = production mode
    # python final/steam_metadata_NP.py
    if len(sys.argv) == 1:
        args.continuous = True
        args.batch_size = DEFAULT_PRODUCTION_BATCH_SIZE
        args.continuous_delay_seconds = DEFAULT_CONTINUOUS_DELAY_SECONDS
        args.auto_push_every = DEFAULT_AUTO_PUSH_EVERY

    return args


def run_once(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    ensure_directory(output_dir)

    processed_count_this_run = 0
    snapshot_loaded = False
    snapshot_payload = None if args.refresh_snapshot else load_source_snapshot(output_dir)
    if snapshot_payload is not None:
        appids, source_map, source_counts = snapshot_payload
        snapshot_loaded = True
    else:
        appids, source_map, source_counts = build_prioritized_appids(
            args.appid_populer_url,
            args.fix_games_url,
            args.new_fix_games_url,
            args.override_data_url,
            args.steam_data_url,
            args.steam_data_gz_url,
        )
        save_source_snapshot(output_dir, appids, source_map, source_counts)

    if args.snapshot_only:
        status = "refresh" if args.refresh_snapshot else "load"
        print(f"[snapshot] {status} | total={len(appids)} | file={get_snapshot_path(output_dir).name}")
        return 0

    if snapshot_loaded:
        print(f"[snapshot] loaded | total={len(appids)} | file={get_snapshot_path(output_dir).name}")
    existing_archive = load_existing_archive(output_dir, args.file_prefix)
    progress = load_progress(output_dir, args.file_prefix)

    batch_appids, batch_stats = select_batch_appids(
        appids=appids,
        existing_archive=existing_archive,
        progress=progress,
        batch_size=args.batch_size,
        force_refresh=args.force_refresh,
    )

    if args.limit > 0:
        batch_appids = batch_appids[:args.limit]

    next_appid_preview = batch_appids[0] if batch_appids else None
    archive_count_before = len(existing_archive)
    total_source_appids = sum(source_counts.values())

    print(f"[run] total sumber={total_source_appids} | arsip={archive_count_before} | ambil={len(batch_appids)}")

    if not batch_appids:
        print("[run] tidak ada App ID yang perlu diproses")
        return 0

    print(f"[run] mulai dari App ID {next_appid_preview}")

    merged_entries: list[tuple[int, dict]] = []
    failures: list[dict] = []

    for index, appid in enumerate(batch_appids, start=1):
        source_priority = source_map.get(appid, "unknown")
        print(f"[{index}/{len(batch_appids)}] proses App ID {appid}")

        try:
            merged_payload, entry_failures = merge_metadata(appid, source_priority)
            merged_entries.append((appid, merged_payload))
            failures.extend(entry_failures)
            result_label = "OK" if merged_payload.get("success", False) else "GAGAL"
        except Exception as exc:
            failures.append(
                build_failure_record(appid, "merge_metadata", str(exc), source_priority)
            )
            merged_entries.append(
                (
                    appid,
                    {
                        "success": False,
                        "source_priority": source_priority,
                        "fetch_status": {
                            "steam_appdetails": False,
                            "steam_original_assets": False,
                        },
                        "steam_appid": str(appid),
                        "assets_count": 0,
                        "assets": {},
                        "store_data": {},
                    },
                )
            )
            result_label = "GAGAL"

        print(f"    {result_label}")

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    for appid, payload in merged_entries:
        existing_archive[appid] = payload

    failed_appids_this_run = [
        appid for appid, payload in merged_entries if not payload.get("success", False)
    ]
    successful_appids_this_run = {
        appid for appid, payload in merged_entries if payload.get("success", False)
    }
    failed_once_set = {
        appid
        for appid in (
            parse_appid_value(value) for value in progress.get("failed_once_appids", [])
        )
        if appid is not None
    }
    failed_twice_set = {
        appid
        for appid in (
            parse_appid_value(value) for value in progress.get("failed_twice_appids", [])
        )
        if appid is not None
    }

    for appid in failed_appids_this_run:
        if appid in failed_once_set:
            failed_twice_set.add(appid)
        else:
            failed_once_set.add(appid)

    failed_once_set.difference_update(successful_appids_this_run)
    failed_twice_set.difference_update(successful_appids_this_run)

    all_entries = sorted(existing_archive.items(), key=lambda item: item[0])
    chunk_files = write_chunk_files(
        entries=all_entries,
        output_dir=output_dir,
        file_prefix=args.file_prefix,
        max_file_size_mb=args.max_file_size_mb,
    )

    failures_path = output_dir / f"{args.file_prefix}_failures.json"
    write_json_file(failures, failures_path)

    progress["backlog_cursor"] = batch_stats["backlog_cursor_after"]
    progress["known_source_appids"] = appids
    progress["failed_once_appids"] = sorted(failed_once_set)
    progress["failed_twice_appids"] = sorted(failed_twice_set)
    progress["last_run_at"] = utc_now_iso()
    progress["last_batch"] = {
        "processed_count": len(batch_appids),
        "new_count": batch_stats["new_count"],
        "retry_failed_count": batch_stats["retry_failed_count"],
        "backlog_count": batch_stats["backlog_count"],
        "force_refresh": args.force_refresh,
    }
    write_progress(output_dir, args.file_prefix, progress)

    manifest = {
        "generated_at": utc_now_iso(),
        "total_appids_processed_this_run": len(batch_appids),
        "total_entries_in_archive": len(existing_archive),
        "source_counts": source_counts,
        "prioritized_sources": [
            "appid_populer",
            "fix_games",
            "new_fix_games",
            "override_data",
            "steam_data",
            "steam_data_gz",
        ],
        "batch": {
            "batch_size": args.batch_size,
            "selected_count": len(batch_appids),
            "new_count": batch_stats["new_count"],
            "retry_failed_count": batch_stats["retry_failed_count"],
            "backlog_count": batch_stats["backlog_count"],
            "backlog_cursor_before": batch_stats["backlog_cursor_before"],
            "backlog_cursor_after": batch_stats["backlog_cursor_after"],
        },
        "skipped_success_existing": batch_stats["skipped_success_existing"],
        "force_refresh": args.force_refresh,
        "chunking": {
            "max_file_size_mb": args.max_file_size_mb,
            "folder": DEFAULT_CHUNKS_DIR_NAME,
            "files": chunk_files,
        },
        "failures": {
            "count": len(failures),
            "file": failures_path.name,
        },
        "progress_file": get_progress_path(output_dir, args.file_prefix).name,
    }

    manifest_path = output_dir / f"{args.file_prefix}_manifest.json"
    write_json_file(manifest, manifest_path)

    archive_count_after = len(all_entries)
    processed_count_this_run = len(batch_appids)
    print(
        f"[run] selesai | diproses={len(batch_appids)} | arsip={archive_count_after} | error={len(failures)} | file={len(chunk_files)}"
    )
    return processed_count_this_run


def main() -> None:
    args = parse_args()

    if not args.continuous:
        run_once(args)
        return

    print("Mode kontinu aktif. Tekan Ctrl+C untuk berhenti.")
    print(
        f"[runner] batch={args.batch_size} | jeda={args.continuous_delay_seconds}s | auto-push={args.auto_push_every}"
    )
    output_dir = Path(args.output_dir)
    push_state = load_push_state(output_dir, args.file_prefix)
    pending = int(push_state.get("pending_processed_since_push", 0) or 0)
    always_refresh_snapshot = bool(args.refresh_snapshot)
    refresh_snapshot_next_run = False

    while True:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{started_at}] proses dimulai")
        try:
            args.refresh_snapshot = always_refresh_snapshot or refresh_snapshot_next_run
            processed = run_once(args)
            if refresh_snapshot_next_run and not always_refresh_snapshot:
                refresh_snapshot_next_run = False
            pending += processed
            write_push_state(
                output_dir,
                args.file_prefix,
                {
                    "pending_processed_since_push": pending,
                    "last_push_at": push_state.get("last_push_at"),
                },
            )
            print(f"[runner] selesai | appid diproses={processed} | belum dipush={pending}")
            if args.auto_push_every > 0 and pending >= args.auto_push_every:
                pushed, pending = try_auto_push(
                    Path.cwd(),
                    output_dir,
                    args.file_prefix,
                    pending,
                )
                if pushed and not always_refresh_snapshot:
                    refresh_snapshot_next_run = True
                    print("[snapshot] dijadwalkan refresh otomatis setelah push sukses")
        except KeyboardInterrupt:
            print("\n[runner] dihentikan manual.")
            break
        except Exception as exc:
            print(f"[runner] gagal | error={exc}")

        if args.continuous_delay_seconds > 0:
            print(f"[runner] tunggu {args.continuous_delay_seconds}s...")
            time.sleep(args.continuous_delay_seconds)


if __name__ == "__main__":
    main()
