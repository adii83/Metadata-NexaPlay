import argparse
import json
import re
import time

import requests


STEAMGRIDDB_API_KEY = "96d55ab4fd62b3b32565f2724c10ce32"

DEFAULT_STEAM_APPIDS = [
15500
]

OUTPUT_FILE = "steam_original_assets_from_appid.json"

SGDB_API_V2 = "https://www.steamgriddb.com/api/v2"
SGDB_PUBLIC = "https://www.steamgriddb.com/api/public"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"

TARGET_LANGUAGE = "english"
TARGET_COUNTRY = "us"

APPDETAILS_ASSET_FIELDS = {
    "header_image",
    "capsule_image",
    "capsule_imagev5",
    "background",
    "background_raw",
    "screenshots",
    "movies",
    "embedded_media",
}


if not STEAMGRIDDB_API_KEY or STEAMGRIDDB_API_KEY == "ISI_API_KEY_KAMU_DI_SINI":
    raise RuntimeError("Isi dulu STEAMGRIDDB_API_KEY di dalam script.")


def parse_appids(raw_values):
    appids = []

    for raw_value in raw_values:
        parts = [part.strip() for part in raw_value.split(",")]

        for part in parts:
            if not part:
                continue

            if not part.isdigit():
                raise ValueError(f"App ID tidak valid: {part}")

            appids.append(int(part))

    unique_appids = []
    seen = set()

    for appid in appids:
        if appid not in seen:
            seen.add(appid)
            unique_appids.append(appid)

    return unique_appids


def get_input_appids():
    parser = argparse.ArgumentParser(
        description=(
            "Gabungkan Steam appdetails dan Steam original assets "
            "ke dalam satu output JSON."
        )
    )
    parser.add_argument(
        "appids",
        nargs="*",
        help="Satu atau banyak App ID. Bisa dipisah spasi atau koma.",
    )
    args = parser.parse_args()

    if args.appids:
        parsed = parse_appids(args.appids)
        if parsed:
            return parsed

    return list(DEFAULT_STEAM_APPIDS)


def extract_urls_from_html(raw_html):
    if not raw_html:
        return []

    urls = re.findall(r'https://[^"\'>\s]+', raw_html)
    unique_urls = []
    seen = set()

    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls


def build_appdetails_with_embeds(data):
    about_urls = extract_urls_from_html(data.get("about_the_game"))
    detailed_urls = extract_urls_from_html(data.get("detailed_description"))

    all_embed_urls = []
    seen = set()

    for url in about_urls + detailed_urls:
        if url not in seen:
            seen.add(url)
            all_embed_urls.append(url)

    enriched = dict(data)
    enriched["embedded_media"] = {
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
    return enriched


def get_steam_appdetails(steam_appid):
    params = {
        "appids": steam_appid,
        "l": TARGET_LANGUAGE,
        "cc": TARGET_COUNTRY,
    }

    headers = {
        "Accept": "application/json",
        "User-Agent": "SteamOriginalAssetsFetcher/1.0",
    }

    response = requests.get(
        STEAM_APPDETAILS_URL,
        params=params,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    result = payload.get(str(steam_appid))

    if not result:
        raise RuntimeError("Response Steam tidak berisi App ID tersebut.")

    if not result.get("success"):
        raise RuntimeError("Steam appdetails mengembalikan success=false.")

    return build_appdetails_with_embeds(result["data"])


def get_sgdb_game_from_steam_appid(steam_appid):
    """
    Lookup Steam appid ke SteamGridDB game id.
    Ini pakai API v2 resmi dan butuh Bearer token.
    """
    url = f"{SGDB_API_V2}/games/steam/{steam_appid}"

    headers = {
        "Authorization": f"Bearer {STEAMGRIDDB_API_KEY}",
        "Accept": "application/json",
        "User-Agent": "SteamOriginalAssetsFetcher/1.0",
    }

    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code == 404:
        return None

    response.raise_for_status()

    payload = response.json()

    if not payload.get("success"):
        return None

    return payload.get("data")


def fetch_public_game(sgdb_game_id):
    """
    Ambil data public SteamGridDB.
    Endpoint ini tidak pakai Bearer token.
    Di sinilah original Steam assets berada.
    """
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


def build_steam_asset_url(steam_appid, asset_path, mtime):
    if not asset_path:
        return None

    return (
        "https://shared.steamstatic.com/store_item_assets/"
        f"steam/apps/{steam_appid}/{asset_path}?t={mtime}"
    )


def add_asset(assets, key, steam_appid, path, mtime, language=None):
    """
    Hanya simpan asset language english.
    Kalau language bukan english, langsung skip.
    """
    if language != TARGET_LANGUAGE:
        return

    if not path:
        return

    item = {
        "path": path,
        "url": build_steam_asset_url(steam_appid, path, mtime),
        "language": language,
    }

    assets.setdefault(key, []).append(item)


def add_english_assets_from_dict(assets, key, steam_appid, data_dict, mtime):
    """
    Helper untuk ambil hanya key 'english' dari object seperti:
    {
        "english": "hash/file.jpg",
        "koreana": "hash/file_koreana.jpg",
        ...
    }
    """
    if not isinstance(data_dict, dict):
        return

    english_path = data_dict.get(TARGET_LANGUAGE)

    add_asset(
        assets=assets,
        key=key,
        steam_appid=steam_appid,
        path=english_path,
        mtime=mtime,
        language=TARGET_LANGUAGE,
    )


def extract_original_assets(public_payload):
    data = public_payload.get("data", {})
    steam_data = data.get("platforms", {}).get("steam", {})

    steam_appid = steam_data.get("id")
    sgdb_game_id = steam_data.get("gameId")
    metadata = steam_data.get("metadata", {})

    if not steam_appid or not metadata:
        return None

    mtime = metadata.get("store_asset_mtime")
    assets = {}

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
        "steam_appid": str(steam_appid),
        "steamgriddb_game_id": sgdb_game_id,
        "store_asset_mtime": mtime,
        "assets": assets,
    }


def build_url_asset(url, source):
    if not url:
        return []

    return [
        {
            "url": url,
            "source": source,
        }
    ]


def extract_appdetails_assets(appdetails):
    assets = {}

    if appdetails.get("capsule_image"):
        assets["capsule_image"] = build_url_asset(
            appdetails["capsule_image"],
            "steam_appdetails",
        )

    if appdetails.get("capsule_imagev5"):
        assets["capsule_imagev5"] = build_url_asset(
            appdetails["capsule_imagev5"],
            "steam_appdetails",
        )

    if appdetails.get("background"):
        assets["background"] = build_url_asset(
            appdetails["background"],
            "steam_appdetails",
        )

    if appdetails.get("background_raw"):
        assets["background_raw"] = build_url_asset(
            appdetails["background_raw"],
            "steam_appdetails",
        )

    if appdetails.get("screenshots"):
        assets["screenshots"] = appdetails["screenshots"]

    if appdetails.get("movies"):
        assets["movies"] = appdetails["movies"]

    if appdetails.get("embedded_media"):
        assets["embedded_media"] = appdetails["embedded_media"]

    if appdetails.get("header_image"):
        assets["header_from_steam_appdetails"] = build_url_asset(
            appdetails["header_image"],
            "steam_appdetails",
        )

    return assets


def extract_store_data(appdetails):
    return {
        key: value
        for key, value in appdetails.items()
        if key not in APPDETAILS_ASSET_FIELDS and key not in {"name", "steam_appid"}
    }


def merge_asset_maps(original_assets, appdetails_assets):
    merged_assets = {}

    for key, value in original_assets.items():
        merged_assets[key] = value

    for key, value in appdetails_assets.items():
        if key == "header_from_steam_appdetails" and merged_assets.get("header"):
            merged_assets[key] = value
            continue

        if key not in merged_assets:
            merged_assets[key] = value

    return merged_assets


def fetch_merged_data_by_steam_appid(steam_appid):
    appdetails = None
    original_result = None
    appdetails_error = None
    original_error = None

    try:
        appdetails = get_steam_appdetails(steam_appid)
    except Exception as exc:
        appdetails_error = str(exc)

    try:
        sgdb_game = get_sgdb_game_from_steam_appid(steam_appid)

        if not sgdb_game:
            original_error = "Steam appid tidak ditemukan di SteamGridDB"
        else:
            public_payload = fetch_public_game(sgdb_game.get("id"))
            original_result = extract_original_assets(public_payload)

            if not original_result:
                original_error = "Original Steam metadata tidak ditemukan"
                original_result = {
                    "steam_appid": str(steam_appid),
                    "steamgriddb_game_id": sgdb_game.get("id"),
                    "store_asset_mtime": None,
                    "assets": {},
                }
    except Exception as exc:
        original_error = str(exc)

    if not appdetails and not original_result:
        return {
            "success": False,
            "steam_appid": str(steam_appid),
            "name": None,
            "store_data": {},
            "assets": {},
            "assets_count": 0,
            "appdetails_success": False,
            "original_assets_success": False,
            "appdetails_error": appdetails_error,
            "original_assets_error": original_error,
        }

    original_assets = {}
    if original_result:
        original_assets = original_result.get("assets", {})

    appdetails_assets = {}
    store_data = {}

    if appdetails:
        appdetails_assets = extract_appdetails_assets(appdetails)
        store_data = extract_store_data(appdetails)

    merged_assets = merge_asset_maps(original_assets, appdetails_assets)

    name = None
    if original_result and original_result.get("steamgriddb_game_id") is not None:
        sgdb_name = get_sgdb_game_from_steam_appid(steam_appid)
        if sgdb_name:
            name = sgdb_name.get("name")

    if not name and appdetails:
        name = appdetails.get("name")

    return {
        "success": bool(appdetails or original_result),
        "steam_appid": (
            original_result.get("steam_appid")
            if original_result
            else str(appdetails.get("steam_appid", steam_appid))
        ),
        "name": name,
        "steamgriddb_game_id": (
            original_result.get("steamgriddb_game_id") if original_result else None
        ),
        "store_asset_mtime": (
            original_result.get("store_asset_mtime") if original_result else None
        ),
        "appdetails_success": appdetails is not None,
        "original_assets_success": (
            original_result is not None and original_error is None
        ),
        "appdetails_error": appdetails_error,
        "original_assets_error": original_error,
        "assets_count": sum(
            len(items) if isinstance(items, list) else 1
            for items in merged_assets.values()
        ),
        "assets": merged_assets,
        "store_data": store_data,
    }


def main():
    steam_appids = get_input_appids()
    result = {}

    for steam_appid in steam_appids:
        print(f"Fetching merged Steam data for appid {steam_appid}")

        try:
            result[str(steam_appid)] = fetch_merged_data_by_steam_appid(steam_appid)
        except Exception as exc:
            result[str(steam_appid)] = {
                "success": False,
                "steam_appid": str(steam_appid),
                "name": None,
                "store_data": {},
                "assets": {},
                "assets_count": 0,
                "appdetails_success": False,
                "original_assets_success": False,
                "appdetails_error": None,
                "original_assets_error": None,
                "error": str(exc),
            }

        time.sleep(1)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file_handle:
        json.dump(result, file_handle, indent=2, ensure_ascii=False)

    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
