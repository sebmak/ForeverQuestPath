"""Quest DAG prerequisite resolution, eligibility checks, and transitive unlock analysis."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .models import CharacterState, Dataset, Quest, canonical_class_key, canonical_race_key


# Rules: Summarizes all quests and dungeons transitively unlocked by completing a given quest.
# Values are discounted by chain hop depth so immediate unlocks weigh more heavily.
@dataclass
class DownstreamValue:
    unlocked_quest_ids: Set[int] = field(default_factory=set)
    unlocked_dungeon_ids: Set[str] = field(default_factory=set)
    discounted_xp: float = 0.0
    discounted_gold: float = 0.0
    max_bag_slots: int = 0
    max_rogue_score: float = 0.0
    max_class_scores: Dict[str, float] = field(default_factory=dict)
    max_skinning_score: float = 0.0
    discounted_skinning: float = 0.0
    has_lw_followup: bool = False
    chain_depth: int = 0

    def max_score_for_class(self, char_class: str) -> float:
        key = canonical_class_key(char_class)
        if key in self.max_class_scores:
            return float(self.max_class_scores[key])
        if key == "ROGUE":
            return float(self.max_rogue_score)
        return 0.0


# Rules: Enforces strict topological prerequisite ordering, minLevel gating, exclusiveTo lockouts,
# profession skill gating (Leatherworking 165 / Skinning 393), and race/class/faction bitmask eligibility
# across all WoW Forever characters while computing transitive chain, profession, and dungeon unlock value.
class QuestDAG:
    dataset: Dataset
    eligible_quests: List[Quest]
    leveling_quests: List[Quest]
    downstream_cache: Dict[int, DownstreamValue]
    character_downstream_cache: Dict[Tuple[int, str, int, int, bool, bool], DownstreamValue]
    archetype_quest_cache: Dict[Tuple[str, int, int, bool, bool], List[Quest]]
    dungeon_prereq_map: Dict[int, Set[str]]

    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset
        self.leveling_quests = sorted(
            (q for q in dataset.quests.values() if q.is_leveling_quest),
            key=lambda q: (q.min_level, q.quest_level, q.quest_id),
        )
        self.eligible_quests = [q for q in self.leveling_quests if q.eligible_alliance_gnome_rogue]
        self.downstream_cache = {}
        self.character_downstream_cache = {}
        self.archetype_quest_cache = {}
        self.dungeon_prereq_map = {}
        self._index_dungeon_unlocks()
        self._compute_all_downstream()

    def _index_dungeon_unlocks(self) -> None:
        for did, dungeon in self.dataset.dungeons.items():
            d_fmask = 1 if dungeon.faction == "Alliance" else (2 if dungeon.faction == "Horde" else 3)
            for qid in dungeon.quest_ids:
                if qid in self.dataset.quests:
                    self._mark_ancestors_for_dungeon(qid, did, d_fmask)
            for chain in dungeon.unlock_chains:
                for qid in chain.quest_ids:
                    if qid in self.dataset.quests:
                        self._mark_ancestors_for_dungeon(qid, did, d_fmask)

    def _mark_ancestors_for_dungeon(self, root_qid: int, dungeon_id: str, dungeon_fmask: int) -> None:
        queue = deque([root_qid])
        seen: Set[int] = set()
        while queue:
            cur = queue.popleft()
            if cur in seen or cur not in self.dataset.quests:
                continue
            seen.add(cur)
            cq = self.dataset.quests[cur]
            if cq.faction_mask not in (0, 3) and dungeon_fmask != 3 and (cq.faction_mask & dungeon_fmask) == 0:
                continue
            self.dungeon_prereq_map.setdefault(cur, set()).add(dungeon_id)
            for pid in cq.prereq_ids:
                queue.append(pid)

    def _compute_all_downstream(self) -> None:
        for qid in self.dataset.quests:
            self.downstream_cache[qid] = self._compute_downstream(qid, state=None)

    def _compute_downstream(self, start_qid: int, state: Optional[CharacterState] = None) -> DownstreamValue:
        val = DownstreamValue()
        start_q = self.dataset.quests[start_qid]
        if start_q.dungeon_id:
            val.unlocked_dungeon_ids.add(start_q.dungeon_id)
        for did in self.dungeon_prereq_map.get(start_qid, ()):
            val.unlocked_dungeon_ids.add(did)

        queue: deque[tuple[int, int]] = deque([(nid, 1) for nid in start_q.next_quest_ids])
        seen: Set[int] = {start_qid}
        while queue:
            cur_id, depth = queue.popleft()
            if cur_id in seen or cur_id not in self.dataset.quests or depth > 10:
                continue
            seen.add(cur_id)
            nq = self.dataset.quests[cur_id]
            if state is not None:
                if not self.is_eligible_for_character(nq, state):
                    continue
            else:
                if not nq.is_leveling_quest:
                    continue
                if nq.required_skill_id is not None and nq.required_skill_id not in (165, 393, 129, 185, 356, 762):
                    continue
                if start_q.faction_mask in (1, 2) and nq.faction_mask in (1, 2) and start_q.faction_mask != nq.faction_mask:
                    continue
                if nq.class_mask != 0 and (start_q.class_mask == 0 or (start_q.class_mask & nq.class_mask) == 0):
                    continue
                if nq.race_mask != 0 and (start_q.race_mask == 0 or (start_q.race_mask & nq.race_mask) == 0):
                    continue
            val.unlocked_quest_ids.add(cur_id)
            val.chain_depth = max(val.chain_depth, depth)
            discount = 0.80 ** depth
            val.discounted_xp += nq.xp * discount
            val.discounted_gold += nq.gold_copper * discount
            val.max_bag_slots = max(val.max_bag_slots, nq.bag_slots)
            val.max_rogue_score = max(val.max_rogue_score, nq.rogue_score)
            for cls_k, cls_val in nq.class_scores.items():
                if cls_val > val.max_class_scores.get(cls_k, 0.0):
                    val.max_class_scores[cls_k] = cls_val
            if nq.rogue_score > val.max_class_scores.get("ROGUE", 0.0):
                val.max_class_scores["ROGUE"] = nq.rogue_score
            eff_skin = max(
                nq.skinning_score,
                85.0 if (nq.is_leatherworking or nq.lw_rewards) else (75.0 if nq.is_skinning else 0.0),
            )
            val.max_skinning_score = max(val.max_skinning_score, eff_skin)
            val.discounted_skinning += eff_skin * discount
            if nq.is_leatherworking or nq.is_skinning or nq.lw_rewards:
                val.has_lw_followup = True
            if nq.dungeon_id:
                val.unlocked_dungeon_ids.add(nq.dungeon_id)
            for did in self.dungeon_prereq_map.get(cur_id, ()):
                val.unlocked_dungeon_ids.add(did)
            for nxt in nq.next_quest_ids:
                queue.append((nxt, depth + 1))
        return val

    def _character_masks(self, state: CharacterState) -> Tuple[int, int, int]:
        f_bit = 1 if state.faction == "Alliance" else (2 if state.faction == "Horde" else 3)
        r_info = self.dataset.races.get(canonical_race_key(state.race, self.dataset.races))
        c_info = self.dataset.classes.get(canonical_class_key(state.char_class, self.dataset.classes))
        return f_bit, (r_info.bitmask if r_info else 0), (c_info.bitmask if c_info else 0)

    def downstream(self, quest_id: int, state: Optional[CharacterState] = None) -> DownstreamValue:
        if state is None:
            return self.downstream_cache.get(quest_id, DownstreamValue())
        if quest_id not in self.dataset.quests:
            return DownstreamValue()
        _, r_bit, c_bit = self._character_masks(state)
        ckey = (quest_id, state.faction, r_bit, c_bit, state.skinning, state.leatherworking)
        cached = self.character_downstream_cache.get(ckey)
        if cached is not None:
            return cached
        val = self._compute_downstream(quest_id, state=state)
        self.character_downstream_cache[ckey] = val
        return val

    def is_eligible_for_character(self, quest: Quest, state: CharacterState) -> bool:
        if not quest.is_leveling_quest:
            return False
        if quest.required_skill_id is not None:
            if quest.required_skill_id == 165 and not state.leatherworking:
                return False
            if quest.required_skill_id == 393 and not state.skinning:
                return False
            if quest.required_skill_id not in (165, 393, 129, 185, 356, 762):
                return False
        if quest.is_leatherworking and not state.leatherworking:
            return False
        if quest.is_skinning and not state.skinning:
            return False
        f_bit, r_bit, c_bit = self._character_masks(state)
        if quest.faction_mask not in (0, 3) and (quest.faction_mask & f_bit) == 0:
            return False
        if quest.race_mask != 0 and r_bit != 0 and (quest.race_mask & r_bit) == 0:
            return False
        if quest.class_mask != 0 and c_bit != 0 and (quest.class_mask & c_bit) == 0:
            return False
        if (
            state.faction == "Alliance"
            and canonical_race_key(state.race, self.dataset.races) == "Gnome"
            and canonical_class_key(state.char_class, self.dataset.classes) == "ROGUE"
            and not quest.eligible_alliance_gnome_rogue
        ):
            return False
        return True

    def prereqs_satisfied(self, quest: Quest, completed: Set[int]) -> bool:
        return all(pid in completed for pid in quest.prereq_ids)

    def can_accept(self, quest: Quest, state: CharacterState) -> bool:
        qid = quest.quest_id
        if qid in state.completed_quests or qid in state.excluded_quests or qid in state.active_quests:
            return False
        if not self.is_eligible_for_character(quest, state):
            return False
        if state.level < quest.min_level:
            return False
        if not self.prereqs_satisfied(quest, state.completed_quests):
            return False
        # Do not accept mutually exclusive quests if sibling already accepted or completed
        for ex_id in quest.exclusive_to:
            if ex_id in state.completed_quests or ex_id in state.active_quests:
                return False
        return True

    def _archetype_quests(self, state: CharacterState) -> List[Quest]:
        _, r_bit, c_bit = self._character_masks(state)
        key = (state.faction, r_bit, c_bit, state.skinning, state.leatherworking)
        cached = self.archetype_quest_cache.get(key)
        if cached is not None:
            return cached
        filtered = [q for q in self.leveling_quests if self.is_eligible_for_character(q, state)]
        self.archetype_quest_cache[key] = filtered
        return filtered

    def available_quests(
        self,
        state: CharacterState,
        include_dungeon: bool = True,
        min_quest_level_floor: int = 1,
    ) -> List[Quest]:
        result: List[Quest] = []
        completed = state.completed_quests
        excluded = state.excluded_quests
        active = state.active_quests
        lvl = state.level
        for q in self._archetype_quests(state):
            if q.min_level > lvl:
                break
            if not include_dungeon and q.is_dungeon:
                continue
            if q.quest_level < min_quest_level_floor:
                continue
            qid = q.quest_id
            if qid in completed or qid in excluded or qid in active:
                continue
            if not all(pid in completed for pid in q.prereq_ids):
                continue
            if any(ex in completed or ex in active for ex in q.exclusive_to):
                continue
            result.append(q)
        return result

    def unlocked_by_completion(self, quest_id: int, state: CharacterState) -> List[Quest]:
        q = self.dataset.quests.get(quest_id)
        if not q:
            return []
        simulated_completed = state.completed_quests | {quest_id}
        unlocked: List[Quest] = []
        for nid in q.next_quest_ids:
            nq = self.dataset.quests.get(nid)
            if not nq:
                continue
            if nid in simulated_completed or nid in state.excluded_quests:
                continue
            if self.is_eligible_for_character(nq, state) and self.prereqs_satisfied(nq, simulated_completed):
                unlocked.append(nq)
        return unlocked

    def mark_completed(self, quest_id: int, state: CharacterState) -> None:
        state.active_quests.discard(quest_id)
        state.cleared_objectives.discard(quest_id)
        state.completed_quests.add(quest_id)
        q = self.dataset.quests.get(quest_id)
        if q:
            for ex_id in q.exclusive_to:
                state.excluded_quests.add(ex_id)

    def missing_prereqs_in_order(self, target_quest_id: int, state: CharacterState) -> List[int]:
        """Return ordered missing prerequisite quest IDs required before target_quest_id."""
        ordered: List[int] = []
        seen: Set[int] = set()

        def visit(qid: int) -> None:
            if qid in seen or qid in state.completed_quests or qid in state.excluded_quests:
                return
            seen.add(qid)
            q = self.dataset.quests.get(qid)
            if not q:
                return
            for pid in q.prereq_ids:
                visit(pid)
            if qid != target_quest_id:
                ordered.append(qid)

        visit(target_quest_id)
        return ordered
