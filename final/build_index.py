import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def count_entries(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return 0
    if isinstance(payload, dict):
        return len(payload)
    return 0


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build dist/index.json for chunk discovery")
    parser.add_argument("--dist-dir", default="dist")
    parser.add_argument("--chunks-dir", default="dist/chunks")
    parser.add_argument("--file-prefix", default="steam_metadata_NP")
    parser.add_argument("--output", default="dist/index.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dist_dir = Path(args.dist_dir)
    chunks_dir = Path(args.chunks_dir)
    output_path = Path(args.output)

    chunk_pattern = f"{args.file_prefix}_part*.json"
    chunk_paths = sorted(chunks_dir.glob(chunk_pattern))

    files: list[dict[str, Any]] = []
    total_entries = 0
    total_bytes = 0

    for path in chunk_paths:
        entry_count = count_entries(path)
        size_bytes = path.stat().st_size
        total_entries += entry_count
        total_bytes += size_bytes
        files.append(
            {
                "file": path.name,
                "path": f"chunks/{path.name}",
                "size_bytes": size_bytes,
                "sha256": file_sha256(path),
                "entries": entry_count,
                "last_modified_utc": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )

    runtime_catalog_path = dist_dir / "runtime_catalog.json"
    runtime_catalog_meta: dict[str, Any] | None = None
    if runtime_catalog_path.exists():
        runtime_catalog_meta = {
            "file": runtime_catalog_path.name,
            "size_bytes": runtime_catalog_path.stat().st_size,
            "sha256": file_sha256(runtime_catalog_path),
            "last_modified_utc": datetime.fromtimestamp(
                runtime_catalog_path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }

    payload = {
        "generated_at": utc_now_iso(),
        "dataset": args.file_prefix,
        "version": utc_now_iso(),
        "chunks": {
            "folder": "chunks",
            "count": len(files),
            "total_entries": total_entries,
            "total_size_bytes": total_bytes,
            "files": files,
        },
        "runtime_catalog": runtime_catalog_meta,
    }
    write_json(output_path, payload)
    print(
        f"[index] done | chunks={len(files)} | entries={total_entries} | "
        f"bytes={total_bytes} | file={output_path}"
    )


if __name__ == "__main__":
    main()
