"""
Synchronizes the official WoW Forever QuestieDB database release into
`scraper/vendor/QuestieDB/` so dataset builds include the latest trace-verified
quest relations (`foreverQuestRelationTraces`), quest levels, and XP tables.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

GITHUB_RELEASES_API = "https://api.github.com/repos/Questie/QuestieDB/releases/latest"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PROJECT_ROOT / "scraper" / "vendor"
QUESTIEDB_VENDOR_DIR = VENDOR_DIR / "QuestieDB"


class QuestieDBSyncer:
    """
    Rules for QuestieDBSyncer:
    1. Only downloads official `QuestieDB-*-Forever.zip` releases from GitHub.
    2. Extracts strictly into `scraper/vendor/QuestieDB/` without keeping archive zips.
    3. Verifies `QuestieDB_Forever.toc` exists before replacing the vendored directory.
    """

    def __init__(self, target_dir: Path = QUESTIEDB_VENDOR_DIR) -> None:
        self.target_dir = target_dir

    def read_local_version(self) -> str | None:
        toc_path = self.target_dir / "QuestieDB_Forever.toc"
        if not toc_path.exists():
            return None
        for line in toc_path.read_text(encoding="utf-8", errors="ignore").splitlines()[:25]:
            if line.startswith("## Version:"):
                return line.split(":", 1)[1].strip()
        return None

    def fetch_latest_release_asset(self) -> tuple[str, str]:
        req = urllib.request.Request(
            GITHUB_RELEASES_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "ForeverQuestPath"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        tag = str(payload.get("tag_name") or "")
        for asset in payload.get("assets") or []:
            name = str(asset.get("name") or "")
            if name.endswith("-Forever.zip"):
                return tag, str(asset["browser_download_url"])
        raise RuntimeError(f"No *-Forever.zip asset found in release {tag!r}")

    def sync(self, force: bool = False) -> str:
        local_ver = self.read_local_version()
        tag, url = self.fetch_latest_release_asset()
        if local_ver and not force and tag.lstrip("v") == local_ver.lstrip("v"):
            print(f"Vendored QuestieDB is already up to date ({local_ver}).")
            return local_ver

        req = urllib.request.Request(url, headers={"User-Agent": "ForeverQuestPath"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw_zip = resp.read()

        staging_dir = self.target_dir.parent / "_QuestieDB_staging"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            zf.extractall(staging_dir)

        extracted_root = staging_dir / "QuestieDB"
        if not (extracted_root / "QuestieDB_Forever.toc").exists():
            extracted_root = staging_dir
        if not (extracted_root / "QuestieDB_Forever.toc").exists():
            shutil.rmtree(staging_dir)
            raise RuntimeError("Downloaded archive missing QuestieDB_Forever.toc")

        if self.target_dir.exists():
            shutil.rmtree(self.target_dir)
        shutil.move(str(extracted_root), str(self.target_dir))
        if staging_dir.exists():
            shutil.rmtree(staging_dir)

        new_ver = self.read_local_version() or tag
        print(f"Synced QuestieDB Forever {new_ver} into {self.target_dir}")
        return new_ver


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync official WoW Forever QuestieDB release.")
    parser.add_argument("--force", action="store_true", help="Re-download even if version matches.")
    args = parser.parse_args()
    QuestieDBSyncer().sync(force=args.force)


if __name__ == "__main__":
    main()
