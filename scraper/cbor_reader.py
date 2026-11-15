"""
Pure-Python reader for QuestieDB_Forever.toc (CBOR + zlib + chunked Base64 metadata)
and supporting Forever Lua databases (subzones, UiMapIDs, dungeons, QuestXP, runtime fixes).
"""

from __future__ import annotations

import base64
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lua_parser import parse_lua_indexed_assignments, parse_lua_table_at


def decode_cbor(data: bytes) -> Any:
    """Decodes a CBOR binary payload into native Python primitives."""
    pos = 0
    n_bytes = len(data)

    def read_uint(ai: int) -> int:
        nonlocal pos
        if ai < 24:
            return ai
        if ai == 24:
            v = data[pos]
            pos += 1
            return v
        if ai == 25:
            v = struct.unpack_from(">H", data, pos)[0]
            pos += 2
            return v
        if ai == 26:
            v = struct.unpack_from(">I", data, pos)[0]
            pos += 4
            return v
        if ai == 27:
            v = struct.unpack_from(">Q", data, pos)[0]
            pos += 8
            return v
        raise ValueError(f"Unsupported CBOR integer additional info: {ai}")

    def parse() -> Any:
        nonlocal pos
        if pos >= n_bytes:
            raise EOFError("Unexpected end of CBOR data")
        b = data[pos]
        pos += 1
        mt = b >> 5
        ai = b & 0x1F

        if mt == 0:
            return read_uint(ai)
        if mt == 1:
            return -1 - read_uint(ai)
        if mt == 2:
            ln = read_uint(ai)
            raw = data[pos : pos + ln]
            pos += ln
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw
        if mt == 3:
            ln = read_uint(ai)
            txt = data[pos : pos + ln].decode("utf-8", errors="replace")
            pos += ln
            return txt
        if mt == 4:
            if ai == 31:
                arr: list[Any] = []
                while data[pos] != 0xFF:
                    arr.append(parse())
                pos += 1
                return arr
            count = read_uint(ai)
            return [parse() for _ in range(count)]
        if mt == 5:
            if ai == 31:
                mapping: dict[Any, Any] = {}
                while data[pos] != 0xFF:
                    k = parse()
                    v = parse()
                    mapping[k] = v
                pos += 1
                return mapping
            count = read_uint(ai)
            return {parse(): parse() for _ in range(count)}
        if mt == 6:
            _ = read_uint(ai)
            return parse()
        if mt == 7:
            if ai == 20:
                return False
            if ai == 21:
                return True
            if ai in (22, 23):
                return None
            if ai == 25:
                v = struct.unpack_from(">e", data, pos)[0]
                pos += 2
                return v
            if ai == 26:
                v = struct.unpack_from(">f", data, pos)[0]
                pos += 4
                return v
            if ai == 27:
                v = struct.unpack_from(">d", data, pos)[0]
                pos += 8
                return v
            raise ValueError(f"Unsupported CBOR simple value ai={ai}")
        raise ValueError(f"Invalid CBOR major type {mt}")

    return parse()


@dataclass
class QuestieDBData:
    """Container holding decoded QuestieDB entities and Forever support tables."""

    quests: dict[int, dict[Any, Any]]
    npcs: dict[int, dict[Any, Any]]
    objects: dict[int, dict[Any, Any]]
    items: dict[int, dict[Any, Any]]
    xp_db: dict[int, tuple[int, int]]
    subzone_to_parent: dict[int, int]
    area_to_uimap: dict[int, int]
    dungeon_entrances: dict[int, tuple[str, int, list[tuple[int, float, float]]]]
    sort_names: dict[int, str]
    alliance_fixes: dict[int, dict[Any, Any]]


# Rules for QuestieDBReader: strictly read-only against AddOns/QuestieDB; decodes
# QuestieDB_Forever.toc using exact baked.lua rules (zlib on -IDS only, plain CBOR on entity rows).
class QuestieDBReader:
    """Decodes QuestieDB_Forever.toc and Forever Lua support tables into Python structures."""

    NPC_CONSTANTS = {2: 0, 3: 1}
    CHUNK_RE = re.compile(r"^~(\d+)~$")

    def __init__(self, questiedb_dir: Path) -> None:
        self.questiedb_dir = questiedb_dir
        self.meta: dict[str, str] = {}

    def _load_toc_meta(self) -> None:
        toc_path = self.questiedb_dir / "QuestieDB_Forever.toc"
        with toc_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("## X-"):
                    k, v = line[3:].strip().split(": ", 1)
                    self.meta[k] = v

    def _get_stored(self, key: str) -> str | None:
        val = self.meta.get(key)
        if val is None:
            return None
        m = self.CHUNK_RE.match(val)
        if m:
            parts = int(m.group(1))
            return "".join(self.meta[f"{key}-{i}"] for i in range(1, parts + 1))
        return val

    def _read_ids(self, entity_prefix: str) -> list[int]:
        stored = self._get_stored(f"X-{entity_prefix}-IDS")
        if not stored:
            return []
        return decode_cbor(zlib.decompress(base64.b64decode(stored)))

    def _read_entity(self, entity_prefix: str, eid: int, constants: dict[int, Any] | None = None) -> dict[Any, Any] | None:
        stored = self._get_stored(f"X-{entity_prefix}-{eid}-S")
        if stored is None:
            return None
        row = decode_cbor(base64.b64decode(stored))
        if isinstance(row, list):
            d: dict[Any, Any] = {idx + 1: val for idx, val in enumerate(row) if val is not None}
        else:
            d = dict(row)
        if constants:
            for k, v in constants.items():
                d.setdefault(k, v)
        presence = d.pop("p", 0) or 0
        field_idx = 1
        while presence:
            if presence & 1:
                f_stored = self._get_stored(f"X-{entity_prefix}-{eid}-{field_idx}")
                if f_stored is not None:
                    d[field_idx] = decode_cbor(base64.b64decode(f_stored))
            presence >>= 1
            field_idx += 1
        return d

    def _load_xp_db(self) -> dict[int, tuple[int, int]]:
        xp: dict[int, tuple[int, int]] = {}
        classic_path = self.questiedb_dir / "support/Forever/QuestXP/xpDB-classic.lua"
        classic_tbl = parse_lua_table_at(classic_path.read_text(encoding="utf-8"), r"QuestXP\.db\s*=")
        for qid, pair in classic_tbl.items():
            if isinstance(pair, list) and len(pair) >= 2:
                xp[int(qid)] = (int(pair[0]), int(pair[1]))
        traces_path = self.questiedb_dir / "support/Forever/QuestXP/xpDB-traces.lua"
        traces_tbl = parse_lua_indexed_assignments(traces_path.read_text(encoding="utf-8"), "QuestXP.db")
        for qid, pair in traces_tbl.items():
            if isinstance(pair, list) and len(pair) >= 2:
                xp[int(qid)] = (int(pair[0]), int(pair[1]))
        return xp

    def _load_zone_maps(self) -> tuple[dict[int, int], dict[int, int], dict[int, tuple[str, int, list[tuple[int, float, float]]]]]:
        sub_txt = (self.questiedb_dir / "support/Forever/Zones/subZoneToParentZone.lua").read_text(encoding="utf-8")
        subzone_to_parent = {int(k): int(v) for k, v in re.findall(r"\[(\d+)\]\s*=\s*(\d+)", sub_txt)}

        area_to_ui: dict[int, int] = {}
        ui_txt = (self.questiedb_dir / "support/Forever/Zones/uiMapIdToAreaId.lua").read_text(encoding="utf-8")
        # Direct canonical block comes second (54 direct AreaIDs); load canonical first so aliases don't overwrite
        direct_idx = ui_txt.find("ZoneDB.private.uiMapIdToAreaId =")
        for chunk in (ui_txt[direct_idx:], ui_txt[:direct_idx]):
            for ui_str, area_str in re.findall(r"\[(\d+)\]\s*=\s*(\d+)", chunk):
                area_to_ui.setdefault(int(area_str), int(ui_str))

        a2u_txt = (self.questiedb_dir / "support/Forever/Zones/areaIdToUiMapId.lua").read_text(encoding="utf-8")
        for area_str, ui_str in re.findall(r"\[(\d+)\]\s*=\s*(\d+)", a2u_txt):
            area_to_ui.setdefault(int(area_str), int(ui_str))

        dungeons_txt = (self.questiedb_dir / "support/Forever/Zones/dungeons.lua").read_text(encoding="utf-8")
        # Only parse the base table before conditional Expansions.Current blocks so Classic/Forever values remain intact
        base_end = dungeons_txt.find("\nif Expansions.Current")
        base_txt = dungeons_txt[:base_end] if base_end != -1 else dungeons_txt
        raw_dungeons = parse_lua_table_at(base_txt, r"local\s+dungeons\s*=")
        entrances: dict[int, tuple[str, int, list[tuple[int, float, float]]]] = {}
        for did, arr in raw_dungeons.items():
            if not isinstance(arr, list) or len(arr) < 3:
                continue
            name = str(arr[0])
            parent_zone = int(arr[2]) if len(arr) >= 3 and isinstance(arr[2], (int, float)) else 0
            pts: list[tuple[int, float, float]] = []
            if len(arr) >= 4 and isinstance(arr[3], list):
                for entry in arr[3]:
                    if isinstance(entry, list) and len(entry) >= 3:
                        pts.append((int(entry[0]), float(entry[1]), float(entry[2])))
            entrances[int(did)] = (name, parent_zone, pts)

        return subzone_to_parent, area_to_ui, entrances

    def _load_enums_and_fixes(self) -> tuple[dict[int, str], dict[int, dict[Any, Any]]]:
        q_enum_txt = (self.questiedb_dir / "src/corrections/enum/quests.lua").read_text(encoding="utf-8")
        sort_tbl = parse_lua_table_at(q_enum_txt, r"constants\.sortKeys\s*=")
        sort_names = {int(v): str(k).replace("_", " ").title() for k, v in sort_tbl.items() if isinstance(v, (int, float))}

        # Load zoneIDs enum for resolving foreverQuestFixes symbols
        z_enum_txt = (self.questiedb_dir / "src/corrections/enum/zones.lua").read_text(encoding="utf-8")
        zone_consts = {k: int(v) for k, v in re.findall(r"(\w+)\s*=\s*(-?\d+)", z_enum_txt)}
        quest_keys_txt = (self.questiedb_dir / "src/meta/questMeta.lua").read_text(encoding="utf-8")
        qkey_consts = {k: int(v) for k, v in re.findall(r"(\w+)\s*=\s*(\d+)", quest_keys_txt)}

        fixes_txt = (self.questiedb_dir / "src/corrections/Forever/foreverQuestFixes.lua").read_text(encoding="utf-8")
        alliance_fixes: dict[int, dict[Any, Any]] = {}
        if "local questFixesAlliance =" in fixes_txt:
            raw_fix = parse_lua_table_at(fixes_txt, r"local\s+questFixesAlliance\s*=")
            for qid, fields in raw_fix.items():
                if not isinstance(fields, dict):
                    continue
                resolved: dict[Any, Any] = {}
                for k, v in fields.items():
                    rk = qkey_consts.get(k.split(".", 1)[-1], k) if isinstance(k, str) else k
                    rv = zone_consts.get(v.split(".", 1)[-1], v) if isinstance(v, str) and v.startswith("zoneIDs.") else v
                    resolved[rk] = rv
                alliance_fixes[int(qid)] = resolved
        return sort_names, alliance_fixes

    def load(self) -> QuestieDBData:
        """Loads all QuestieDB entities and Forever lookup tables."""
        self._load_toc_meta()
        quests = {qid: row for qid in self._read_ids("Quest") if (row := self._read_entity("Quest", qid)) is not None}
        npcs = {nid: row for nid in self._read_ids("Npc") if (row := self._read_entity("Npc", nid, self.NPC_CONSTANTS)) is not None}
        objects = {oid: row for oid in self._read_ids("Object") if (row := self._read_entity("Object", oid)) is not None}
        items = {iid: row for iid in self._read_ids("Item") if (row := self._read_entity("Item", iid)) is not None}
        xp_db = self._load_xp_db()
        subzone_to_parent, area_to_uimap, dungeon_entrances = self._load_zone_maps()
        sort_names, alliance_fixes = self._load_enums_and_fixes()

        return QuestieDBData(
            quests=quests,
            npcs=npcs,
            objects=objects,
            items=items,
            xp_db=xp_db,
            subzone_to_parent=subzone_to_parent,
            area_to_uimap=area_to_uimap,
            dungeon_entrances=dungeon_entrances,
            sort_names=sort_names,
            alliance_fixes=alliance_fixes,
        )
