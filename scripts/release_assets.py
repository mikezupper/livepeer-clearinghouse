#!/usr/bin/env python3
"""Create a deterministic, checksummed release bundle of public contracts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT_FILES = (".env.example", "ARCHITECTURE.md", "compose.yaml")


def build_contract_bundle(
    root: Path, output_directory: Path, *, version: str, source_date_epoch: int
) -> tuple[Path, Path]:
    """Build deterministic archive and manifest and return both paths."""
    files = [root / name for name in ROOT_FILES]
    files.extend(
        sorted(
            path
            for path in (root / "contracts").rglob("*")
            if path.is_file() and path.suffix in {".json", ".yaml", ".yml"}
        )
    )
    unsafe = [
        path for path in files if path.is_symlink() or not path.resolve().is_relative_to(root)
    ]
    if unsafe:
        raise ValueError(f"release inputs must be regular in-repository files: {unsafe[0]}")

    entries = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        entries.append(
            {"path": relative, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        )
    manifest = {
        "format": "livepeer-clearinghouse-contracts/v1",
        "source_date_epoch": source_date_epoch,
        "version": version,
        "files": entries,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output_directory / f"clearinghouse-{version}-contracts.manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    archive_path = output_directory / f"clearinghouse-{version}-contracts.tar.gz"
    with (
        archive_path.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=source_date_epoch) as zipped,
        tarfile.open(fileobj=zipped, mode="w|") as archive,
    ):
        for path in files:
            _add_bytes(
                archive,
                path.relative_to(root).as_posix(),
                path.read_bytes(),
                source_date_epoch,
            )
        _add_bytes(archive, "contracts.manifest.json", manifest_bytes, source_date_epoch)
    return archive_path, manifest_path


def _add_bytes(archive: tarfile.TarFile, name: str, content: bytes, mtime: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = mtime
    archive.addfile(info, io.BytesIO(content))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    archive, manifest = build_contract_bundle(
        root,
        args.output_directory,
        version=args.version,
        source_date_epoch=args.source_date_epoch,
    )
    print(f"release-assets: wrote {archive} and {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
