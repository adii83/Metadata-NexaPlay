import argparse
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

OVERRIDE_DATA_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/override_data.json"
)
STEAM_DATA_GZ_URL = (
    "https://raw.githubusercontent.com/adii83/steam-metadata-archive/main/steam_data.json.gz"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_json(url: str, timeout: int = 120) -> Any:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "SteamMetadataNP/2.0"})
    response.raise_for_status()
    return response.json()


def fetch_json_gz(url: str, timeout: int = 180) -> Any:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "SteamMetadataNP/2.0"},
        stream=True,
    )
    response.raise_for_status()
    with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz:
        return json.load(gz)


def parse_appid(raw_value: Any) -> int | None:
    text = str(raw_value).strip()
    if not text.isdigit():
        return None
    return int(text)


def pick_fields(payload: dict[str, Any], appid: int) -> dict[str, Any]:
    return {
        "appid": appid,
        "price_display": payload.get("price_display"),
        "price_normalized": payload.get("price_normalized"),
        "protection": payload.get("protection"),
    }


def build_runtime_catalog(steam_data_obj: dict[str, Any], override_obj: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    catalog: dict[str, Any] = {}
    base_count = 0
    override_count = 0

    for key, value in steam_data_obj.items():
        if not isinstance(value, dict):
            continue
        appid = parse_appid(value.get("appid", key))
        if appid is None:
            continue
        catalog[str(appid)] = pick_fields(value, appid)
        base_count += 1

    for key, value in override_obj.items():
        if not isinstance(value, dict):
            continue
        appid = parse_appid(value.get("appid", key))
        if appid is None:
            continue
        # Override selalu menang bila App ID sama.
        catalog[str(appid)] = pick_fields(value, appid)
        override_count += 1

    stats = {
        "base_loaded": base_count,
        "override_loaded": override_count,
        "final_total": len(catalog),
    }
    return catalog, stats


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build dist/runtime_catalog.json from steam_data.json.gz + override_data.json"
    )
    parser.add_argument("--steam-data-gz-url", default=STEAM_DATA_GZ_URL)
    parser.add_argument("--override-data-url", default=OVERRIDE_DATA_URL)
    parser.add_argument("--output", default="dist/runtime_catalog.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    steam_data_obj = fetch_json_gz(args.steam_data_gz_url)
    override_obj = fetch_json(args.override_data_url)

    if not isinstance(steam_data_obj, dict):
        raise RuntimeError("steam_data.json.gz harus berupa object.")
    if not isinstance(override_obj, dict):
        raise RuntimeError("override_data.json harus berupa object.")

    catalog, stats = build_runtime_catalog(steam_data_obj, override_obj)
    output_path = Path(args.output)
    payload = {
        "generated_at": utc_now_iso(),
        "sources": {
            "steam_data_gz_url": args.steam_data_gz_url,
            "override_data_url": args.override_data_url,
        },
        "fields": ["appid", "price_display", "price_normalized", "protection"],
        "stats": stats,
        "data": catalog,
    }
    write_json(output_path, payload)
    print(
        f"[runtime_catalog] done | total={stats['final_total']} | "
        f"base={stats['base_loaded']} | override={stats['override_loaded']} | file={output_path}"
    )


if __name__ == "__main__":
    main()
