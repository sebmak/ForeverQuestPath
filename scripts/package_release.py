#!/usr/bin/env python3
"""Package and optionally publish ForeverQuestPath WoW addon release archives."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


# Rules for AddonReleasePackager:
# 1. Version, title, notes, interface version, and manifest entries must be read
#    directly from addon/ForeverQuestPath.toc and validated with
#    scripts/install_addon.sh --check-only (including ForeverQuestPath_Forever.toc
#    and Media/Icon.png) before packaging.
# 2. The release ZIP must be named ForeverQuestPath-v<version>-forever.zip, wrap
#    all addon files under ForeverQuestPath/ using POSIX forward slashes, and
#    accompany dist/release.json (WowUp / BigWigs packager metadata) + .sha256.
# 3. Every archive must pass zipfile integrity checks, manifest & WowUp asset
#    membership checks, and clean temporary extraction checks.
# 4. Optional GitHub release publishing must read HTTPS credentials in memory via
#    `git credential fill` without printing secrets, sync best-effort repo
#    metadata/topics, remove stale unsuffixed archives, and upload zip,
#    release.json, and sha256 assets.
class AddonReleasePackager:
    """Builds, verifies, and optionally publishes ForeverQuestPath release archives."""

    ADDON_FOLDER_NAME = "ForeverQuestPath"
    DEFAULT_REPO = "sebmak/ForeverQuestPath"
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    ADDON_DIR = PROJECT_ROOT / "addon"
    TOC_PATH = ADDON_DIR / "ForeverQuestPath.toc"
    FOREVER_TOC_NAME = "ForeverQuestPath_Forever.toc"
    ICON_REL_PATH = "Media/Icon.png"
    INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "install_addon.sh"
    DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist"
    RELEASE_FLAVOR = "forever"
    RELEASE_JSON_NAME = "release.json"
    GITHUB_TOPICS = ("wow-addon", "world-of-warcraft", "wow-forever", "wowup")
    GITHUB_API_BASE = "https://api.github.com"
    GITHUB_UPLOADS_BASE = "https://uploads.github.com"
    HTTP_TIMEOUT_SECONDS = 60
    FORBIDDEN_ARCHIVE_TOKENS = (".DS_Store", "__MACOSX")

    def __init__(self, output_dir: Path | None = None, repo: str = DEFAULT_REPO) -> None:
        self.project_root = self.PROJECT_ROOT
        self.addon_dir = self.ADDON_DIR
        self.toc_path = self.TOC_PATH
        self.install_script = self.INSTALL_SCRIPT
        self.output_dir = (output_dir or self.DEFAULT_OUTPUT_DIR).resolve()
        self.repo = repo

    def parse_toc_metadata(self) -> dict:
        """Return parsed TOC headers (version, title, notes, interface) and file entries."""
        if not self.toc_path.is_file():
            raise FileNotFoundError(f"Missing .toc file: {self.toc_path}")

        version: str | None = None
        title: str | None = None
        notes: str | None = None
        interface_ver: int | None = None
        toc_files: list[str] = []
        for raw_line in self.toc_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("## "):
                key_val = line[3:].split(":", 1)
                if len(key_val) == 2:
                    key, val = key_val[0].strip(), key_val[1].strip()
                    if key == "Version":
                        version = val
                    elif key == "Title":
                        title = val
                    elif key == "Notes" and not notes:
                        notes = val
                    elif key == "Interface":
                        try:
                            interface_ver = int(val.split(",", 1)[0].strip())
                        except ValueError as exc:
                            raise RuntimeError(f"Invalid ## Interface header in {self.toc_path}: {val}") from exc
                continue
            if line.startswith("#"):
                continue
            toc_files.append(line.replace("\\", "/"))

        if not version:
            raise RuntimeError(f"Missing ## Version header in {self.toc_path}")
        if interface_ver is None:
            raise RuntimeError(f"Missing ## Interface header in {self.toc_path}")
        if not toc_files:
            raise RuntimeError(f"No addon files listed in {self.toc_path}")
        return {
            "version": version,
            "title": title or self.ADDON_FOLDER_NAME,
            "notes": notes or "",
            "interface": interface_ver,
            "toc_files": toc_files,
        }

    def parse_toc(self) -> tuple[str, list[str]]:
        """Return (addon_version, manifest_relative_paths) from ForeverQuestPath.toc."""
        meta = self.parse_toc_metadata()
        return meta["version"], meta["toc_files"]

    def validate_addon_sources(self) -> None:
        """Run scripts/install_addon.sh --check-only to verify .toc and Lua syntax."""
        subprocess.run(
            ["bash", str(self.install_script), "--check-only"],
            cwd=str(self.project_root),
            check=True,
        )

    def _iter_addon_files(self) -> list[tuple[Path, str]]:
        """Collect clean addon files paired with ForeverQuestPath/<rel> archive paths."""
        items: list[tuple[Path, str]] = []
        for file_path in sorted(p for p in self.addon_dir.rglob("*") if p.is_file()):
            rel = file_path.relative_to(self.addon_dir)
            if any(part.startswith(".") or part == "__MACOSX" for part in rel.parts):
                continue
            arcname = f"{self.ADDON_FOLDER_NAME}/{rel.as_posix()}"
            items.append((file_path, arcname))
        return items

    def _verify_archive(self, zip_path: Path, toc_files: list[str]) -> None:
        """Validate zip CRC integrity, top-level ForeverQuestPath/ layout, and clean unpack."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad_entry = zf.testzip()
            if bad_entry is not None:
                raise RuntimeError(f"Corrupt ZIP entry detected: {bad_entry}")

            names = zf.namelist()
            name_set = set(names)
            for req_rel in ("ForeverQuestPath.toc", self.FOREVER_TOC_NAME, self.ICON_REL_PATH):
                expected_entry = f"{self.ADDON_FOLDER_NAME}/{req_rel}"
                if expected_entry not in name_set:
                    raise RuntimeError(f"Archive missing required entry: {expected_entry}")

            for rel in toc_files:
                expected_entry = f"{self.ADDON_FOLDER_NAME}/{rel}"
                if expected_entry not in name_set:
                    raise RuntimeError(f"Archive missing .toc entry: {expected_entry}")

            for entry in names:
                if any(tok in entry for tok in self.FORBIDDEN_ARCHIVE_TOKENS) or "/._" in entry or entry.startswith("._"):
                    raise RuntimeError(f"Forbidden metadata entry inside archive: {entry}")

            with tempfile.TemporaryDirectory() as tmp_dir:
                zf.extractall(tmp_dir)
                extracted_root = Path(tmp_dir) / self.ADDON_FOLDER_NAME
                for req_rel in ("ForeverQuestPath.toc", self.FOREVER_TOC_NAME, self.ICON_REL_PATH):
                    if not (extracted_root / req_rel).is_file():
                        raise RuntimeError(f"Extracted archive missing {req_rel}")

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """Compute lowercase SHA-256 hex digest for file_path."""
        digest = hashlib.sha256()
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def build_archive(self) -> tuple[str, Path, Path, Path, str]:
        """Validate addon files and build verified -forever.zip, .sha256, and release.json."""
        meta = self.parse_toc_metadata()
        version: str = meta["version"]
        title: str = meta["title"]
        interface_ver: int = meta["interface"]
        toc_files: list[str] = meta["toc_files"]
        self.validate_addon_sources()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        for stale in list(self.output_dir.glob(f"{self.ADDON_FOLDER_NAME}-*.zip")) + list(
            self.output_dir.glob(f"{self.ADDON_FOLDER_NAME}-*.zip.sha256")
        ):
            stale.unlink()

        zip_name = f"{self.ADDON_FOLDER_NAME}-v{version}-{self.RELEASE_FLAVOR}.zip"
        zip_path = self.output_dir / zip_name
        sha_path = self.output_dir / f"{zip_name}.sha256"
        release_json_path = self.output_dir / self.RELEASE_JSON_NAME

        addon_files = self._iter_addon_files()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for src_path, arcname in addon_files:
                zf.write(src_path, arcname=arcname)

        self._verify_archive(zip_path, toc_files)
        sha256_hex = self._compute_sha256(zip_path)
        sha_path.write_text(f"{sha256_hex}  {zip_name}\n", encoding="utf-8")

        release_payload = {
            "releases": [
                {
                    "name": title,
                    "version": f"v{version}",
                    "filename": zip_name,
                    "nolib": False,
                    "metadata": [
                        {
                            "flavor": self.RELEASE_FLAVOR,
                            "interface": interface_ver,
                        }
                    ],
                }
            ]
        }
        release_json_path.write_text(json.dumps(release_payload, indent=2) + "\n", encoding="utf-8")

        print(f"Built {zip_path} ({zip_path.stat().st_size} bytes, {len(addon_files)} files)")
        print(f"Manifest ({release_json_path.name}): flavor={self.RELEASE_FLAVOR}, interface={interface_ver}")
        print(f"SHA-256 ({sha_path.name}): {sha256_hex}")
        return version, zip_path, sha_path, release_json_path, sha256_hex

    def _load_github_auth_header(self) -> str:
        """Read HTTPS GitHub credentials in memory via `git credential fill`."""
        cred_input = f"protocol=https\nhost=github.com\npath={self.repo}.git\n\n"
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input=cred_input,
            text=True,
            capture_output=True,
            check=True,
        )
        creds: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                creds[key] = val
        username = creds.get("username", "")
        password = creds.get("password", "")
        if not username or not password:
            raise RuntimeError("Missing GitHub credentials from `git credential fill`")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _github_request(
        self,
        method: str,
        url: str,
        auth_header: str,
        *,
        json_body: dict | None = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
    ) -> dict | None:
        """Send an authenticated GitHub REST request and return decoded JSON if present."""
        data: bytes | None = raw_body
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")

        headers = {
            "Authorization": auth_header,
            "Accept": "application/vnd.github+json",
            "User-Agent": "ForeverQuestPath-ReleasePackager",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if data is not None:
            headers["Content-Type"] = content_type

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.HTTP_TIMEOUT_SECONDS) as resp:
            payload = resp.read()
            if not payload:
                return None
            return json.loads(payload.decode("utf-8"))

    def sync_github_repo_metadata(self, auth_header: str, description: str, homepage: str) -> None:
        """Best-effort sync of repository description, homepage, and WowUp discovery topics."""
        repo_url = f"{self.GITHUB_API_BASE}/repos/{self.repo}"
        try:
            self._github_request(
                "PATCH",
                repo_url,
                auth_header,
                json_body={"description": description, "homepage": homepage},
            )
            self._github_request(
                "PUT",
                f"{repo_url}/topics",
                auth_header,
                json_body={"names": list(self.GITHUB_TOPICS)},
            )
            print(f"Synced GitHub metadata & topics for {self.repo}")
        except urllib.error.HTTPError as exc:
            err_msg = exc.read().decode("utf-8", errors="replace")
            print(f"WARN: Skipped GitHub repository metadata sync (HTTP {exc.code}): {err_msg}")

    @classmethod
    def _format_release_notes(
        cls,
        version: str,
        zip_name: str,
        sha256_hex: str,
        interface_version: int = 16001,
    ) -> str:
        """Build user-facing GitHub Release markdown notes."""
        repo_url = f"https://github.com/{cls.DEFAULT_REPO}"
        return (
            f"## ForeverQuestPath v{version} (WoW Forever `{interface_version}`)\n\n"
            "### Install via WowUp\n"
            "1. Open **WowUp**, select your **WoW Forever** installation, and open **Get Addons -> Install from URL**.\n"
            f"2. Paste `{repo_url}` and click **Import**.\n\n"
            "### Manual Installation\n"
            f"1. Download **`{zip_name}`** below.\n"
            "2. Extract it into your WoW Forever `Interface/AddOns/` directory:\n"
            "   - **macOS:** `/Applications/World of Warcraft/_forever_/Interface/AddOns/`\n"
            "   - **Windows:** `<WoW>\\_forever_\\Interface\\AddOns\\`\n"
            "3. Verify `<WoW>/_forever_/Interface/AddOns/ForeverQuestPath/ForeverQuestPath.toc` exists.\n"
            "4. Launch WoW Forever (or `/reload`) and run `/fqp` or click the minimap icon.\n\n"
            "### Checksum\n"
            f"- `SHA-256 ({zip_name})`: `{sha256_hex}`\n"
        )

    def publish_github_release(
        self,
        version: str,
        zip_path: Path,
        sha_path: Path,
        release_json_path: Path,
        sha256_hex: str,
        *,
        notes: str = "",
        interface_version: int = 16001,
    ) -> dict:
        """Create or update GitHub release `v<version>`, sync metadata, and upload assets."""
        auth_header = self._load_github_auth_header()
        description = notes or "Optimal 1-60 quest routing addon for WoW Forever (Interface 16001)."
        self.sync_github_repo_metadata(auth_header, description, f"https://github.com/{self.repo}")

        tag = f"v{version}"
        title = f"{self.ADDON_FOLDER_NAME} {tag}"
        body = self._format_release_notes(version, zip_path.name, sha256_hex, interface_version)
        releases_url = f"{self.GITHUB_API_BASE}/repos/{self.repo}/releases"

        release: dict | None = None
        try:
            release = self._github_request(
                "POST",
                releases_url,
                auth_header,
                json_body={
                    "tag_name": tag,
                    "name": title,
                    "body": body,
                    "draft": False,
                    "prerelease": False,
                },
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 422:
                err_msg = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"GitHub release creation failed (HTTP {exc.code}): {err_msg}") from exc
            existing = self._github_request("GET", f"{releases_url}/tags/{tag}", auth_header)
            if not existing or "id" not in existing:
                raise RuntimeError(f"Failed to retrieve existing release for tag {tag}") from exc
            release = self._github_request(
                "PATCH",
                f"{releases_url}/{existing['id']}",
                auth_header,
                json_body={"name": title, "body": body, "draft": False, "prerelease": False},
            )

        if not release or "id" not in release:
            raise RuntimeError("GitHub release response missing release id")

        release_id = release["id"]
        existing_assets = {
            asset["name"]: asset["id"]
            for asset in (release.get("assets") or [])
            if "name" in asset and "id" in asset
        }

        upload_specs = [
            (zip_path, "application/zip"),
            (release_json_path, "application/json"),
            (sha_path, "text/plain"),
        ]
        stale_names = {
            f"{self.ADDON_FOLDER_NAME}-v{version}.zip",
            f"{self.ADDON_FOLDER_NAME}-v{version}.zip.sha256",
        }
        for stale_name in stale_names:
            if stale_name in existing_assets:
                self._github_request(
                    "DELETE",
                    f"{releases_url}/assets/{existing_assets[stale_name]}",
                    auth_header,
                )

        uploaded_assets: list[dict] = []
        for asset_path, mime_type in upload_specs:
            if asset_path.name in existing_assets:
                asset_id = existing_assets[asset_path.name]
                self._github_request(
                    "DELETE",
                    f"{releases_url}/assets/{asset_id}",
                    auth_header,
                )
            encoded_name = urllib.parse.quote(asset_path.name)
            upload_url = f"{self.GITHUB_UPLOADS_BASE}/repos/{self.repo}/releases/{release_id}/assets?name={encoded_name}"
            asset_resp = self._github_request(
                "POST",
                upload_url,
                auth_header,
                raw_body=asset_path.read_bytes(),
                content_type=mime_type,
            )
            if asset_resp:
                uploaded_assets.append(asset_resp)

        print(f"Release published: {release.get('html_url')}")
        for asset in uploaded_assets:
            print(
                f"Uploaded asset: {asset.get('name')} ({asset.get('size')} bytes) -> {asset.get('browser_download_url')}"
            )
        return release


def main() -> None:
    parser = argparse.ArgumentParser(description="Package and optionally publish ForeverQuestPath addon release ZIP.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=AddonReleasePackager.DEFAULT_OUTPUT_DIR,
        help="Directory for release archive and checksum outputs (default: dist/)",
    )
    parser.add_argument(
        "--publish-github",
        action="store_true",
        help="Create/update GitHub release for tag v<version> and upload binary assets.",
    )
    parser.add_argument(
        "--repo",
        default=AddonReleasePackager.DEFAULT_REPO,
        help="GitHub repository slug in owner/name form (default: sebmak/ForeverQuestPath)",
    )
    args = parser.parse_args()

    packager = AddonReleasePackager(output_dir=args.output_dir, repo=args.repo)
    meta = packager.parse_toc_metadata()
    version, zip_path, sha_path, release_json_path, sha256_hex = packager.build_archive()
    if args.publish_github:
        packager.publish_github_release(
            version,
            zip_path,
            sha_path,
            release_json_path,
            sha256_hex,
            notes=meta["notes"],
            interface_version=meta["interface"],
        )


if __name__ == "__main__":
    main()
