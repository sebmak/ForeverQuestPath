#!/usr/bin/env python3
"""Generate addon/Data/ProfessionData.lua for all 12 WoW Forever professions."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRAPER_DIR = PROJECT_ROOT / "scraper"
if str(SCRAPER_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPER_DIR))

from build_dataset import BAD_NAME_PATTERNS, ForeverDatasetBuilder
from sim.lua_exporter import to_lua


# Rules for ProfessionDataBuilder:
# 1. All constants, profession mappings, exclusion sets, and compiled regexes live at
#    the top of the class.
# 2. Reads verified data/quests.json, data/nav_graph.json, data/travel_graph.json,
#    data/dungeons.json, and upstream QuestieDB/ForeverLevelingBiS sources via
#    ForeverDatasetBuilder without modifying GeneratedData.lua or NavData.lua.
# 3. Emits addon/Data/ProfessionData.lua containing missing non-repeatable quests across
#    all 12 WoW Forever professions (plus missing prereqs & Nav questAnchors), recipe/
#    tool rewards (profRewards), per-profession synergy scores (profScores), and
#    sorted data.indexes.professionQuestIds[skillId] lists in <=150-entry IIFEs so
#    LuaJIT constant limits are never exceeded and luajit -bl compiles cleanly.
class ProfessionDataBuilder:
    """Builds addon/Data/ProfessionData.lua with 12-profession quest & synergy overlays."""

    ALL_PROF_IDS: Tuple[int, ...] = (171, 164, 333, 202, 182, 165, 186, 393, 197, 185, 129, 356)
    ALL_PROF_SET: Set[int] = set(ALL_PROF_IDS)
    RECIPE_SUBCLASS_TO_SKILL: Mapping[int, int] = {
        1: 165,  # Leatherworking
        2: 197,  # Tailoring
        3: 202,  # Engineering
        4: 164,  # Blacksmithing
        5: 185,  # Cooking
        6: 171,  # Alchemy
        7: 129,  # First Aid
        8: 333,  # Enchanting
        9: 356,  # Fishing
    }
    EXCLUDED_SALVE_REPEATABLES: Set[int] = {4109, 4110, 4111, 4112, 5888, 5889, 5890, 5891}
    GENERATED_QUEST_RE = re.compile(r"quests\[(\d+)\]=")
    MINING_ORE_RE = re.compile(
        r"\b(ore|vein|mineral|copper|tin|silver|iron|mithril|thorium|truesilver|incendicite|indurium|bloodstone|stone|coarse stone|heavy stone|solid stone|dense stone|elemental earth|core of earth|golem|excavat|quarry|mine)\b",
        re.IGNORECASE,
    )
    ENG_MECH_RE = re.compile(
        r"\b(gizmo|whirring|bronze tube|gyromast|shredder|harvest|mechanical|dynamite|blasting|explosive|goblin|gnomish)\b",
        re.IGNORECASE,
    )
    HERB_ALCH_RE = re.compile(
        r"\b(herb|root|petal|flower|blossom|moss|spore|kelp|earthroot|mageroyal|briarthorn|bruiseweed|wild steelbloom|kingsblood|liferoot|fadeleaf|goldthorn|khadgar|sungrass|blindweed|gromsblood|dreamfoil|ichor|venom|poison sac|volatile|elixir|potion|vial)\b",
        re.IGNORECASE,
    )
    CLOTH_RE = re.compile(
        r"\b(linen|wool|silk|mageweave|runecloth|felcloth|bandana|burlap|cloth|shadowsilk|spider's silk|thick spider)\b",
        re.IGNORECASE,
    )
    HUMANOID_CAMP_RE = re.compile(
        r"\b(bandit|defias|syndicate|kurzen|pirate|cultist|scarlet|venture co|trogg|kobold|gnoll|ogre|troll|orc|quilboar)\b",
        re.IGNORECASE,
    )
    COOKING_MEAT_RE = re.compile(
        r"\b(meat|ribs|liver|flank|goretusk|boar|wolf|bear|raptor|turtle|clam|zesty|egg|crawler|crab|strider)\b",
        re.IGNORECASE,
    )
    FISHING_WATER_RE = re.compile(
        r"\b(fish|angler|pagle|murloc fin|clam|pearl|electropeller|gaffer)\b",
        re.IGNORECASE,
    )
    KILL_QTY_RE = re.compile(r"\b(\d+)\b")
    RECIPE_PREFIXES: Tuple[str, ...] = ("Recipe:", "Plans:", "Pattern:", "Schematic:", "Formula:", "Manual:")
    SIDE_NAMES: Mapping[int, str] = {0: "Both", 1: "Alliance", 2: "Horde", 3: "Both"}

    project_root: Path
    data_dir: Path
    generated_lua_path: Path
    out_lua_path: Path

    def __init__(self, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root
        self.data_dir = project_root / "data"
        self.generated_lua_path = project_root / "addon" / "Data" / "GeneratedData.lua"
        self.out_lua_path = project_root / "addon" / "Data" / "ProfessionData.lua"

    @classmethod
    def _classify_reward_skill(cls, name: str, item_cls: Any, item_sub: Any) -> int | None:
        lower = name.lower()
        if item_cls == 9:
            mapped = cls.RECIPE_SUBCLASS_TO_SKILL.get(item_sub)
            if mapped is not None:
                return mapped
        if name.startswith("Plans:"):
            return 164
        if name.startswith("Schematic:"):
            return 202
        if name.startswith("Formula:"):
            return 333
        if name.startswith("Manual:"):
            return 129
        if name.startswith("Pattern:"):
            if any(w in lower for w in ("leather", "scale", "hide", "deviate", "moonglow", "kodo", "suede", "armor kit", "turtle")):
                return 165
            return 197
        if name.startswith("Recipe:"):
            if any(w in lower for w in ("elixir", "potion", "oil", "flask", "transmute", "philosopher", "protection", "restorative")):
                return 171
            return 185
        if any(w in lower for w in ("fishing pole", "nat pagle's", "bright baubles", "shiny bauble", "aquadynamic")):
            return 356
        if "skinning knife" in lower or "finkle's skinner" in lower:
            return 393
        return None

    @staticmethod
    def _collect_objective_text(qid: int, q: Mapping[str, Any], b: ForeverDatasetBuilder) -> str:
        parts: List[str] = [str(q.get("name") or "")]
        for oc in q.get("objectiveCoords") or ():
            lbl = oc.get("label")
            if lbl:
                parts.append(str(lbl))
        raw = b.qdb.quests.get(qid) or {}
        for txt in raw.get(8) or ():
            if txt:
                parts.append(str(txt))
        obj = raw.get(10)
        if isinstance(obj, dict):
            buckets = obj.values()
        elif isinstance(obj, list):
            buckets = [obj]
        else:
            buckets = ()
        for idx_key, entries in (obj.items() if isinstance(obj, dict) else enumerate(buckets)):
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, list) or not entry:
                    continue
                eid = entry[0]
                if len(entry) >= 2 and entry[1]:
                    parts.append(str(entry[1]))
                if isinstance(eid, int):
                    row = b.qdb.items.get(eid) or b.qdb.objects.get(eid) or b.qdb.npcs.get(eid)
                    if isinstance(row, dict) and row.get(1):
                        parts.append(str(row[1]))
        return " | ".join(parts)

    @classmethod
    def _extract_kill_count(cls, text: str) -> int:
        best = 0
        for m in cls.KILL_QTY_RE.finditer(text):
            v = int(m.group(1))
            if 5 <= v <= 60 and v > best:
                best = v
        return best

    def _compute_prof_rewards_and_scores(
        self,
        qid: int,
        q: Mapping[str, Any],
        b: ForeverDatasetBuilder,
    ) -> Tuple[List[Dict[str, Any]], Dict[int, int]]:
        flb_q = b.flb_quests.get(qid) or {}
        rw = flb_q.get("rewards") or {}
        cand_items: List[int] = []
        seen_items: Set[int] = set()
        for src_list in (rw.get("items") or (), rw.get("choices") or (), sorted(b.quest_reward_items.get(qid, ()))):
            for iid in src_list:
                if isinstance(iid, int) and iid not in seen_items:
                    seen_items.add(iid)
                    cand_items.append(iid)

        prof_rewards: List[Dict[str, Any]] = []
        for iid in cand_items:
            flb_it = b.flb_items.get(iid) or {}
            qdb_it = b.qdb.items.get(iid) or {}
            name = str(flb_it.get("name") or qdb_it.get(1) or "")
            if not name:
                continue
            item_cls = flb_it.get("class") if flb_it.get("class") is not None else qdb_it.get(12)
            item_sub = flb_it.get("subclass") if flb_it.get("subclass") is not None else qdb_it.get(13)
            sid = self._classify_reward_skill(name, item_cls, item_sub)
            if sid in self.ALL_PROF_SET:
                prof_rewards.append({"itemId": int(iid), "name": name, "skillId": int(sid)})

        scores: Dict[int, int] = {}

        def _bump(skill_id: int, val: float) -> None:
            iv = int(round(max(0.0, min(100.0, val))))
            if iv > scores.get(skill_id, 0):
                scores[skill_id] = iv

        rs = q.get("requiredSkill")
        if isinstance(rs, dict) and rs.get("skillId") in self.ALL_PROF_SET:
            _bump(int(rs["skillId"]), 88)
        if q.get("isLeatherworking"):
            _bump(165, 88)
        if q.get("isSkinning"):
            _bump(393, 88)

        for pr in prof_rewards:
            _bump(int(pr["skillId"]), 90)

        skin_val = float(q.get("skinningScore") or 0.0)
        if skin_val >= 25.0:
            _bump(393, skin_val)
        if skin_val >= 35.0:
            _bump(165, skin_val * 0.65)

        obj_text = self._collect_objective_text(qid, q, b)
        ore_hits = len(set(m.group(1).lower() for m in self.MINING_ORE_RE.finditer(obj_text)))
        if ore_hits > 0:
            mining_s = min(82, 68 + (ore_hits - 1) * 5)
            _bump(186, mining_s)
            _bump(164, 48 + min(14, (ore_hits - 1) * 5))
            _bump(202, 48 + min(14, (ore_hits - 1) * 5))

        mech_hits = len(set(m.group(1).lower() for m in self.ENG_MECH_RE.finditer(obj_text)))
        if mech_hits > 0:
            _bump(202, min(78, 62 + (mech_hits - 1) * 5))

        herb_hits = len(set(m.group(1).lower() for m in self.HERB_ALCH_RE.finditer(obj_text)))
        if herb_hits > 0:
            _bump(182, min(82, 65 + (herb_hits - 1) * 5))
            _bump(171, min(72, 52 + (herb_hits - 1) * 5))

        if self.CLOTH_RE.search(obj_text):
            _bump(197, 72)
            _bump(129, 62)
        elif self.HUMANOID_CAMP_RE.search(obj_text) and self._extract_kill_count(obj_text) >= 8:
            _bump(197, 48)
            _bump(129, 45)

        max_equip_q = max(
            (
                int(sr.get("quality") or 0)
                for sr in (q.get("specialRewards") or ())
                if sr.get("kind") in ("weapon", "gear")
            ),
            default=0,
        )
        if max_equip_q >= 3:
            _bump(333, 68)
        elif max_equip_q == 2:
            _bump(333, 52)

        meat_hits = len(set(m.group(1).lower() for m in self.COOKING_MEAT_RE.finditer(obj_text)))
        if meat_hits > 0:
            _bump(185, min(75, 55 + (meat_hits - 1) * 5))

        fish_hits = len(set(m.group(1).lower() for m in self.FISHING_WATER_RE.finditer(obj_text)))
        if fish_hits > 0:
            _bump(356, min(80, 52 + (fish_hits - 1) * 7))

        return prof_rewards, {sid: sc for sid, sc in sorted(scores.items()) if sc > 0}

    @staticmethod
    def _coord_obj(raw: Mapping[str, Any] | None, npc_id: Any, npc_name: Any, kind: Any, default_name: str, fallback_zone: int) -> Dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        zid = raw.get("zoneId") or fallback_zone
        x = raw.get("x")
        y = raw.get("y")
        if zid is None or x is None or y is None:
            return None
        return {
            "id": npc_id,
            "name": npc_name or default_name,
            "kind": kind or "npc",
            "zoneId": int(zid),
            "x": round(float(x), 1),
            "y": round(float(y), 1),
        }

    def _serialize_extra_quest(self, qid: int, q: Mapping[str, Any]) -> Dict[str, Any]:
        zid = int(q.get("zoneId") or 0)
        starter = self._coord_obj(q.get("starterCoords"), q.get("starterNpcId"), q.get("starterName"), q.get("starterKind"), "Quest Giver", zid)
        turnin = self._coord_obj(q.get("turninCoords"), q.get("turninNpcId"), q.get("turninName"), q.get("turninKind"), "Turn-in NPC", zid)
        if starter is None and turnin is not None:
            starter = dict(turnin)
        if turnin is None and starter is not None:
            turnin = dict(starter)
        objs = [
            {
                "zoneId": int(oc.get("zoneId") or zid),
                "x": round(float(oc["x"]), 1),
                "y": round(float(oc["y"]), 1),
                "label": oc.get("label") or q["name"],
            }
            for oc in (q.get("objectiveCoords") or ())
            if oc.get("x") is not None and oc.get("y") is not None
        ]
        fmask = int(q.get("factionMask") or 3)
        rs = q.get("requiredSkill")
        cscores = {
            k: round(float(v), 1)
            for k, v in sorted((q.get("classScores") or {}).items())
            if round(float(v), 1) > 0.0
        }
        return {
            "id": qid,
            "name": q["name"],
            "zoneId": zid,
            "zoneName": q.get("zoneName") or "",
            "minLevel": int(q.get("minLevel") or 1),
            "questLevel": int(q.get("questLevel") or q.get("minLevel") or 1),
            "xp": int(q.get("xp") or 0),
            "goldCopper": int(q.get("goldCopper") or 0) or None,
            "bagSlots": int(q.get("bagSlots") or 0) or None,
            "rogueScore": round(float(q.get("rogueScore") or 0.0), 1) or None,
            "classScores": cscores or None,
            "specialRewards": q.get("specialRewards") or None,
            "prereqIds": list(q.get("prereqIds") or ()) or None,
            "nextQuestIds": list(q.get("nextQuestIds") or ()) or None,
            "exclusiveTo": list(q.get("exclusiveTo") or ()) or None,
            "isBreadcrumb": True if q.get("isBreadcrumb") else None,
            "isDungeon": True if q.get("isDungeon") else None,
            "dungeonId": q.get("dungeonId"),
            "isGroup": True if q.get("isGroup") else None,
            "faction": self.SIDE_NAMES.get(fmask, "Both"),
            "side": fmask,
            "factionMask": fmask,
            "raceMask": int(q.get("raceMask") or 0),
            "classMask": int(q.get("classMask") or 0) or None,
            "requiredSkill": ({"skillId": int(rs["skillId"]), "minSkill": int(rs.get("minSkill") or 1)} if isinstance(rs, dict) and rs.get("skillId") else None),
            "isLeatherworking": True if q.get("isLeatherworking") else None,
            "isSkinning": True if q.get("isSkinning") else None,
            "lwRewards": q.get("lwRewards") or None,
            "skinningScore": round(float(q.get("skinningScore") or 0.0), 1) or None,
            "beastTargetCount": int(q.get("beastTargetCount") or 0) or None,
            "beastTypes": list(q.get("beastTypes") or ()) or None,
            "starter": starter,
            "turnin": turnin,
            "objectives": objs or None,
            "features": {"skinningValue": round(float(q.get("skinningScore") or 0.0), 1)},
        }

    def build(self) -> Path:
        quests_doc = json.loads((self.data_dir / "quests.json").read_text(encoding="utf-8"))
        quests_json: Dict[int, Dict[str, Any]] = {int(k): v for k, v in quests_doc["quests"].items()}
        nav_doc = json.loads((self.data_dir / "nav_graph.json").read_text(encoding="utf-8"))
        quest_anchors: Mapping[str, Any] = nav_doc.get("questAnchors") or {}
        travel_doc = json.loads((self.data_dir / "travel_graph.json").read_text(encoding="utf-8"))
        dungeons_doc = json.loads((self.data_dir / "dungeons.json").read_text(encoding="utf-8"))
        known_zones: Set[int] = {int(k) for k in travel_doc.get("nodes", {})}
        for d in (dungeons_doc.get("dungeons") or {}).values():
            if d.get("zoneId"):
                known_zones.add(int(d["zoneId"]))

        gen_text = self.generated_lua_path.read_text(encoding="utf-8")
        generated_qids: Set[int] = {int(m.group(1)) for m in self.GENERATED_QUEST_RE.finditer(gen_text)}

        b = ForeverDatasetBuilder()
        b._load_addon_sources()

        # Precompute rewards + scores across candidate/all quests
        prof_rewards_by_qid: Dict[int, List[Dict[str, Any]]] = {}
        prof_scores_by_qid: Dict[int, Dict[int, int]] = {}
        for qid, q in quests_json.items():
            prs, psc = self._compute_prof_rewards_and_scores(qid, q, b)
            if prs:
                prof_rewards_by_qid[qid] = prs
            if psc:
                prof_scores_by_qid[qid] = psc

        # Find missing non-repeatable profession quests (requiredSkill or recipe reward)
        extra_qids: Set[int] = set()
        for qid, q in quests_json.items():
            if qid in generated_qids or qid in self.EXCLUDED_SALVE_REPEATABLES:
                continue
            rs = q.get("requiredSkill") or {}
            sid = rs.get("skillId") if isinstance(rs, dict) else None
            has_direct_prof = (sid in self.ALL_PROF_SET) or bool(prof_rewards_by_qid.get(qid))
            if not has_direct_prof:
                continue
            raw_qdb = b.qdb.quests.get(qid) or {}
            sflags = int(raw_qdb.get(24) or 0)
            qflags = int(raw_qdb.get(23) or 0)
            if (sflags & 1) != 0 or (qflags & (4096 | 32768 | 65536)) != 0:
                continue
            qlv = int(q.get("questLevel") or 0)
            if qlv < 1 or qlv > 60:
                continue
            if BAD_NAME_PATTERNS.search(str(q.get("name") or "")):
                continue
            zid = int(q.get("zoneId") or 0)
            sc = q.get("starterCoords")
            tc = q.get("turninCoords")
            has_valid_coord = (isinstance(sc, dict) and int(sc.get("zoneId") or zid) in known_zones) or (
                isinstance(tc, dict) and int(tc.get("zoneId") or zid) in known_zones
            )
            if not has_valid_coord or zid not in known_zones:
                continue
            extra_qids.add(qid)

        # Pull missing prereqs recursively
        queue = list(extra_qids)
        while queue:
            cur = queue.pop()
            cq = quests_json.get(cur)
            if not cq:
                continue
            for pid in cq.get("prereqIds") or ():
                pid_int = int(pid)
                if pid_int not in generated_qids and pid_int not in extra_qids and pid_int in quests_json:
                    pq = quests_json[pid_int]
                    if pq.get("starterCoords") or pq.get("turninCoords"):
                        extra_qids.add(pid_int)
                        queue.append(pid_int)

        all_exported_qids = generated_qids | extra_qids

        # Build professionQuestIds index across all exported quests (top 350 per skill)
        prof_index: Dict[int, List[int]] = {}
        for sid in self.ALL_PROF_IDS:
            cands: List[Tuple[int, int, int, int]] = []
            for qid in sorted(all_exported_qids):
                q = quests_json.get(qid)
                if not q:
                    continue
                psc = prof_scores_by_qid.get(qid, {})
                score = psc.get(sid, 0)
                if score <= 0:
                    continue
                rs = q.get("requiredSkill") or {}
                has_req = 1 if (isinstance(rs, dict) and rs.get("skillId") == sid) else 0
                has_rew = 1 if any(pr.get("skillId") == sid for pr in prof_rewards_by_qid.get(qid, ())) else 0
                direct = 1 if (has_req or has_rew or (sid == 165 and q.get("isLeatherworking")) or (sid == 393 and q.get("isSkinning"))) else 0
                cands.append((direct, score, -int(q.get("minLevel") or 1), -qid))
            cands.sort(reverse=True)
            prof_index[sid] = [-c[3] for c in cands[:350]]

        # Emit Lua overlay
        lines: List[str] = [
            "-- AUTO-GENERATED by ForeverQuestPath/scripts/build_profession_data.py. Do not edit manually.",
            "local _, ns = ...",
            "",
            "local data = ns.GeneratedData",
            "if not (data and data.quests) then return end",
            "local quests = data.quests",
            "local zones = data.zones or {}",
            "local nav = ns.NavData",
            "local qa = nav and nav.questAnchors or nil",
            "",
        ]

        # 1. Extra profession quests
        extra_sorted = sorted(extra_qids)
        lines.append("-- 1. Extra non-repeatable profession & prereq quests across all 12 professions")
        chunk_size = 120
        for start in range(0, len(extra_sorted), chunk_size):
            lines.append(";(function(quests, zones)")
            for qid in extra_sorted[start : start + chunk_size]:
                qobj = self._serialize_extra_quest(qid, quests_json[qid])
                lines.append(f"quests[{qid}]={to_lua(qobj, compact=True)}")
            lines.append("end)(quests, zones)")
        lines.append("")

        # Register extra quests in byZone and byFaction indexes
        lines.append("if data.indexes and data.indexes.byZone and data.indexes.byFaction then")
        lines.append("  local bz, bf = data.indexes.byZone, data.indexes.byFaction")
        lines.append("  local seenZ, seenF = {}, {}")
        lines.append("  local function AddToBucket(map, key, qid, seen)")
        lines.append("    local b = map[key]")
        lines.append("    if not b then b = {}; map[key] = b end")
        lines.append("    local sk = seen[key]")
        lines.append("    if not sk then")
        lines.append("      sk = {}")
        lines.append("      for i = 1, #b do sk[b[i]] = true end")
        lines.append("      seen[key] = sk")
        lines.append("    end")
        lines.append("    if not sk[qid] then sk[qid] = true; b[#b + 1] = qid end")
        lines.append("  end")
        extra_idx_expr = ", ".join(str(qid) for qid in extra_sorted)
        lines.append(f"  local extraIds = {{{extra_idx_expr}}}")
        lines.append("  for i = 1, #extraIds do")
        lines.append("    local qid = extraIds[i]")
        lines.append("    local q = quests[qid]")
        lines.append("    if q then")
        lines.append("      local scz = q.starter and q.starter.zoneId")
        lines.append("      if scz then AddToBucket(bz, scz, qid, seenZ) end")
        lines.append("      if q.zoneId and q.zoneId ~= scz then AddToBucket(bz, q.zoneId, qid, seenZ) end")
        lines.append("      local fm = q.factionMask or q.side or 3")
        lines.append("      if fm == 0 or fm == 1 or fm == 3 then AddToBucket(bf, 1, qid, seenF) end")
        lines.append("      if fm == 0 or fm == 2 or fm == 3 then AddToBucket(bf, 2, qid, seenF) end")
        lines.append("      if fm == 0 or fm == 3 then AddToBucket(bf, 3, qid, seenF) end")
        lines.append("    end")
        lines.append("  end")
        lines.append("end")
        lines.append("")

        # 2. Extra Nav questAnchors
        extra_anchors = [
            (qid, quest_anchors[str(qid)])
            for qid in extra_sorted
            if str(qid) in quest_anchors
        ]
        if extra_anchors:
            lines.append("if qa then")
            for start in range(0, len(extra_anchors), 150):
                lines.append("  ;(function(qa)")
                for qid, anc in extra_anchors[start : start + 150]:
                    s_flat = [v for pair in (anc.get("s") or ()) for v in (int(pair[0]), round(float(pair[1]), 1))]
                    o_flat = [v for pair in (anc.get("o") or ()) for v in (int(pair[0]), round(float(pair[1]), 1))]
                    t_pairs = anc.get("t")
                    if t_pairs is not None:
                        t_flat = [v for pair in t_pairs for v in (int(pair[0]), round(float(pair[1]), 1))]
                        lines.append(
                            f"  qa[{qid}]={{s={{{','.join(map(str, s_flat))}}},o={{{','.join(map(str, o_flat))}}},t={{{','.join(map(str, t_flat))}}}}}"
                        )
                    else:
                        lines.append(
                            f"  qa[{qid}]={{s={{{','.join(map(str, s_flat))}}},o={{{','.join(map(str, o_flat))}}}}}"
                        )
                lines.append("  end)(qa)")
            lines.append("end")
            lines.append("")

        # 3. profRewards on exported quests
        pr_items = [(qid, prof_rewards_by_qid[qid]) for qid in sorted(all_exported_qids) if qid in prof_rewards_by_qid]
        lines.append("-- 2. Recipe, pattern, plans, schematic, formula, manual & tool rewards")
        for start in range(0, len(pr_items), 150):
            lines.append(";(function(quests)")
            for qid, plist in pr_items[start : start + 150]:
                lines.append(f"if quests[{qid}] then quests[{qid}].profRewards={to_lua(plist, compact=True)} end")
            lines.append("end)(quests)")
        lines.append("")

        # 4. profScores on exported quests
        ps_items = [(qid, prof_scores_by_qid[qid]) for qid in sorted(all_exported_qids) if qid in prof_scores_by_qid]
        lines.append("-- 3. Per-profession quest synergy scores across all 12 professions")
        for start in range(0, len(ps_items), 200):
            lines.append(";(function(quests)")
            for qid, smap in ps_items[start : start + 200]:
                kv = ",".join(f"[{sid}]={val}" for sid, val in smap.items())
                lines.append(f"if quests[{qid}] then quests[{qid}].profScores={{{kv}}} end")
            lines.append("end)(quests)")
        lines.append("")

        # 5. indexes.professionQuestIds
        lines.append("-- 4. Per-profession sorted quest index")
        lines.append("data.indexes = data.indexes or {}")
        lines.append("data.indexes.professionQuestIds = {")
        for sid in self.ALL_PROF_IDS:
            qlist = ",".join(str(q) for q in prof_index.get(sid, ()))
            lines.append(f"  [{sid}] = {{{qlist}}},")
        lines.append("}")
        lines.append("")

        self.out_lua_path.write_text("\n".join(lines), encoding="utf-8")
        return self.out_lua_path


def main() -> None:
    builder = ProfessionDataBuilder()
    out = builder.build()
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
