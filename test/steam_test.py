import argparse
import json
import re
from pathlib import Path

import requests


LANGUAGE = "english"
COUNTRY = "us"


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


def build_appdetails_with_embeds(data: dict) -> dict:
    about_urls = extract_urls_from_html(data.get("about_the_game"))
    detailed_urls = extract_urls_from_html(data.get("detailed_description"))

    all_embed_urls: list[str] = []
    seen: set[str] = set()
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


def get_steam_appdetails(appid: int) -> dict:
    url = "https://store.steampowered.com/api/appdetails"

    params = {
        "appids": appid,
        "l": LANGUAGE,
        "cc": COUNTRY,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 SteamStoreTest/1.0"
    }

    response = requests.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()

    payload = response.json()
    result = payload.get(str(appid))

    if not result:
        raise RuntimeError("Response tidak berisi App ID tersebut.")

    if not result.get("success"):
        raise RuntimeError("Steam mengembalikan success=false.")

    return result["data"]


def write_json_file(payload: dict, output_path: Path) -> None:
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def parse_appids(raw_values: list[str]) -> list[int]:
    appids: list[int] = []

    for raw_value in raw_values:
        parts = [part.strip() for part in raw_value.split(",")]
        for part in parts:
            if not part:
                continue
            if not part.isdigit():
                raise ValueError(f"App ID tidak valid: {part}")
            appids.append(int(part))

    unique_appids: list[int] = []
    seen: set[int] = set()
    for appid in appids:
        if appid not in seen:
            seen.add(appid)
            unique_appids.append(appid)

    if not unique_appids:
        raise ValueError("Minimal masukkan satu App ID.")

    return unique_appids


def get_input_appids() -> list[int]:
    parser = argparse.ArgumentParser(
        description="Ambil full data Steam appdetails untuk satu atau banyak App ID."
    )
    parser.add_argument(
        "appids",
        nargs="*",
        help="Satu atau banyak App ID. Bisa dipisah spasi atau koma.",
    )
    args = parser.parse_args()

    if args.appids:
        return parse_appids(args.appids)

    raw_input_value = input(
        "Masukkan App ID Steam (bisa lebih dari 1, pisahkan dengan spasi atau koma): "
    ).strip()
    return parse_appids(raw_input_value.split())


def save_and_print_game(game: dict, output_path: Path) -> None:
    write_json_file(game, output_path)

    print("=" * 60)
    print("STEAM APP DETAILS")
    print("=" * 60)

    print("Title       :", game.get("name"))
    print("App ID      :", game.get("steam_appid"))
    print("Developer   :", ", ".join(game.get("developers", [])))
    print("Publisher   :", ", ".join(game.get("publishers", [])))
    print("Release     :", game.get("release_date", {}).get("date"))
    print("Screenshots :", len(game.get("screenshots", [])))
    print("Movies      :", len(game.get("movies", [])))
    print("Fields      :", len(game))

    print("\nSaved JSON:")
    print(output_path.resolve())


def main():
    appids = get_input_appids()

    for index, appid in enumerate(appids, start=1):
        if len(appids) > 1:
            print(f"\nMemproses {index}/{len(appids)} - App ID {appid}")

        data = get_steam_appdetails(appid)
        data = build_appdetails_with_embeds(data)
        output_path = Path(f"steam_app_{appid}.json")
        save_and_print_game(data, output_path)


if __name__ == "__main__":
    main()
