"""
Fetches and caches WoW Forever NPC creature types (`Beast`, `Dragonkin`, etc.) and
creature families (`Crocolisk`, `Raptor`, `Cat`, etc.) from Wowhead Forever tooltips
(`dataEnv=16`, falling back to Classic Era `dataEnv=4`) for all QuestieDB quest targets.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRAPER_DIR = PROJECT_ROOT / "scraper"
if str(SCRAPER_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPER_DIR))

from cbor_reader import QuestieDBReader

ADDONS_ROOT = Path("/Applications/World of Warcraft/_forever_/Interface/AddOns")
VENDOR_QUESTIEDB_DIR = PROJECT_ROOT / "scraper" / "vendor" / "QuestieDB"
DEFAULT_CACHE_PATH = SCRAPER_DIR / "cache" / "npc_creature_types.json"

CREATURE_TYPE_IDS: dict[str, int] = {
    "Beast": 1,
    "Dragonkin": 2,
    "Demon": 3,
    "Elemental": 4,
    "Giant": 5,
    "Undead": 6,
    "Humanoid": 7,
    "Critter": 8,
    "Mechanical": 9,
    "Not specified": 10,
    "Totem": 11,
    "Non-combat Pet": 12,
    "Gas Cloud": 13,
}

ID_TO_CREATURE_TYPE: dict[int, str] = {tid: name for name, tid in CREATURE_TYPE_IDS.items()}

TYPE_LINE_RE = re.compile(
    r"(?:^|>)(?:[^<]*?\s)?\b("
    + "|".join(re.escape(k) for k in sorted(CREATURE_TYPE_IDS, key=len, reverse=True))
    + r")\b\s*(?:\((?:Normal|Elite|Rare|Rare Elite|Boss)\)|</)",
    re.IGNORECASE,
)
TD_LINE_RE = re.compile(r"<td>(.*?)</td>", re.DOTALL)


# Rules for NpcCreatureTypeFetcher: strictly fetches public Wowhead tooltip metadata for
# QuestieDB target NPCs and persists deterministic, reusable JSON cache entries under scraper/cache/.
class NpcCreatureTypeFetcher:
    """Collects QuestieDB quest target NPC IDs and caches their Wowhead Forever creature types."""

    def __init__(self, cache_path: Path = DEFAULT_CACHE_PATH) -> None:
        self.cache_path = cache_path
        self.qdb_dir = (
            VENDOR_QUESTIEDB_DIR
            if (VENDOR_QUESTIEDB_DIR / "QuestieDB_Forever.toc").exists()
            else ADDONS_ROOT / "QuestieDB"
        )

    def collect_target_npc_ids(self) -> list[int]:
        """Collects all unique NPC IDs referenced by QuestieDB quest kills and item drops."""
        qdb = QuestieDBReader(self.qdb_dir).load()
        target_npcs: set[int] = set()
        for row in qdb.quests.values():
            raw_obj = row.get(10)
            groups: dict[int, list[Any]] = {}
            if isinstance(raw_obj, list):
                for idx, grp in enumerate(raw_obj, start=1):
                    if isinstance(grp, list) and grp:
                        groups[idx] = grp
            elif isinstance(raw_obj, dict):
                for k, grp in raw_obj.items():
                    if isinstance(grp, list) and grp:
                        groups[int(k)] = grp

            for gidx, entries in groups.items():
                for entry in entries:
                    if not isinstance(entry, (list, tuple)) or not entry:
                        continue
                    eid = entry[0]
                    if gidx == 1 and isinstance(eid, int):
                        target_npcs.add(eid)
                    elif gidx == 5:
                        if isinstance(eid, list):
                            target_npcs.update(int(x) for x in eid if isinstance(x, int))
                        elif isinstance(eid, int):
                            target_npcs.add(eid)
                    elif gidx == 3 and isinstance(eid, int):
                        it = qdb.items.get(eid)
                        if isinstance(it, dict):
                            for nid in (it.get(2) or [])[:5]:
                                if isinstance(nid, int):
                                    target_npcs.add(nid)
        return sorted(target_npcs)

    @staticmethod
    def _parse_tooltip(nid: int, payload: dict[str, Any], data_env: int) -> dict[str, Any]:
        tooltip = html.unescape(str(payload.get("tooltip") or ""))
        name = str(payload.get("name") or "")
        ctype = "Unknown"
        ctype_id = 0

        comp_cat = payload.get("completion_category")
        if isinstance(comp_cat, int) and comp_cat in ID_TO_CREATURE_TYPE:
            ctype_id = comp_cat
            ctype = ID_TO_CREATURE_TYPE[comp_cat]
        else:
            m = TYPE_LINE_RE.search(tooltip)
            if m:
                raw_type = m.group(1)
                for canonical, tid in CREATURE_TYPE_IDS.items():
                    if canonical.lower() == raw_type.lower():
                        ctype = canonical
                        ctype_id = tid
                        break

        family = None
        td_lines = [re.sub(r"<[^>]+>", "", line).strip() for line in TD_LINE_RE.findall(tooltip)]
        for idx, line in enumerate(td_lines):
            if not line or line == name:
                continue
            if TYPE_LINE_RE.search(f"<td>{line}</td>"):
                # Creature family appears directly after the creature type/rank line (e.g. "Crocolisk")
                if ctype in ("Beast", "Dragonkin") and idx + 1 < len(td_lines):
                    nxt = td_lines[idx + 1]
                    if nxt and nxt != name and not nxt.startswith("Level "):
                        family = nxt
                break

        return {
            "npcId": nid,
            "name": name,
            "type": ctype,
            "typeId": ctype_id,
            "family": family,
            "dataEnv": data_env,
        }

    def fetch_one(self, nid: int) -> dict[str, Any]:
        """Fetches creature type metadata for one NPC ID from Wowhead tooltips."""
        for env in (16, 4):
            url = f"https://nether.wowhead.com/tooltip/npc/{nid}?dataEnv={env}&locale=0"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                if isinstance(payload, dict) and payload.get("tooltip"):
                    return self._parse_tooltip(nid, payload, env)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                return {"npcId": nid, "type": "Unknown", "typeId": 0, "error": f"HTTP {exc.code}"}
            except Exception as exc:  # noqa: BLE001
                return {"npcId": nid, "type": "Unknown", "typeId": 0, "error": str(exc)}
        return {"npcId": nid, "type": "NotFound", "typeId": 0}

    def sync(self, refresh: bool = False, workers: int = 24) -> dict[str, dict[str, Any]]:
        """Updates and saves scraper/cache/npc_creature_types.json for all QuestieDB target NPCs."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, dict[str, Any]] = {}
        if self.cache_path.exists() and not refresh:
            try:
                existing = json.loads(self.cache_path.read_text(encoding="utf-8")).get("npcs", {})
            except Exception:  # noqa: BLE001
                existing = {}

        target_ids = self.collect_target_npc_ids()
        todo = [
            nid
            for nid in target_ids
            if str(nid) not in existing or existing[str(nid)].get("error")
        ]
        if todo:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(self.fetch_one, nid): nid for nid in todo}
                done = 0
                for fut in as_completed(futs):
                    nid = futs[fut]
                    existing[str(nid)] = fut.result()
                    done += 1
                    if done % 500 == 0:
                        print(f"Fetched {done}/{len(todo)} NPC tooltips...")

        ordered = {str(nid): existing[str(nid)] for nid in sorted(int(k) for k in existing)}
        payload = {"meta": {"count": len(ordered), "source": "nether.wowhead.com dataEnv=16/4"}, "npcs": ordered}
        self.cache_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache Wowhead Forever NPC creature types.")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch all target NPCs instead of incremental update.")
    parser.add_argument("--workers", type=int, default=24, help="Concurrent worker count.")
    args = parser.parse_args()
    npcs = NpcCreatureTypeFetcher().sync(refresh=args.refresh, workers=args.workers)
    counts: dict[str, int] = {}
    for row in npcs.values():
        t = str(row.get("type") or "Unknown")
        counts[t] = counts.get(t, 0) + 1
    print(f"Cached {len(npcs)} NPCs -> type breakdown: {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
