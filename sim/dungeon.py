"""Dungeon readiness evaluation, prerequisite chain tracking, and optimal run timing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .dag import QuestDAG
from .models import CharacterState, Dataset, Dungeon, Quest
from .travel import TravelModel
from .xp import XPModel


# Rules: Captures how ready and valuable a dungeon run is at the character's current state.
# ready_quests are accepted or immediately acceptable; locked_quests still have uncompleted prereqs.
@dataclass
class DungeonReadiness:
    dungeon: Dungeon
    in_level_window: bool
    optimal_window: bool
    ready_quests: List[Quest] = field(default_factory=list)
    active_dungeon_quests: List[Quest] = field(default_factory=list)
    locked_quests: List[Quest] = field(default_factory=list)
    completed_quests: List[Quest] = field(default_factory=list)
    next_prereq_quest: Optional[Quest] = None
    total_dungeon_xp: int = 0
    total_gold_copper: int = 0
    max_bag_slots: int = 0
    max_rogue_score: float = 0.0
    max_class_score: float = 0.0
    readiness_ratio: float = 0.0


# Rules: Evaluates WoW Forever dungeons for any character's faction, race, and class, prioritizes unlock chains
# before the optimal level window, defers quests above state.max_level_offset (default +2), and schedules runs cleanly.
class DungeonOptimizer:
    dataset: Dataset
    dag: QuestDAG
    xp_model: XPModel
    travel_model: TravelModel

    def __init__(
        self,
        dataset: Dataset,
        dag: QuestDAG,
        xp_model: XPModel,
        travel_model: TravelModel,
    ) -> None:
        self.dataset = dataset
        self.dag = dag
        self.xp_model = xp_model
        self.travel_model = travel_model

    def eligible_dungeon_quests(self, dungeon: Dungeon, state: CharacterState) -> List[Quest]:
        seen: set[int] = set()
        result: List[Quest] = []
        for qid in dungeon.quest_ids:
            q = self.dataset.quests.get(qid)
            if q and qid not in seen and self.dag.is_eligible_for_character(q, state):
                seen.add(qid)
                result.append(q)
        # Also include any quest tagged with dungeon_id not in dungeon.quest_ids
        for qid, q in self.dataset.quests.items():
            if q.dungeon_id == dungeon.dungeon_id and qid not in seen and self.dag.is_eligible_for_character(q, state):
                seen.add(qid)
                result.append(q)
        return result

    def evaluate_dungeon(
        self,
        dungeon: Dungeon,
        state: CharacterState,
        max_level_offset: Optional[int] = None,
    ) -> DungeonReadiness:
        eligible = self.eligible_dungeon_quests(dungeon, state)
        ready: List[Quest] = []
        active: List[Quest] = []
        locked: List[Quest] = []
        completed: List[Quest] = []

        for q in eligible:
            qid = q.quest_id
            if qid in state.completed_quests:
                completed.append(q)
            elif qid in state.excluded_quests:
                continue
            elif qid in state.active_quests:
                active.append(q)
            elif not state.within_level_offset(q, max_level_offset):
                locked.append(q)
            elif self.dag.can_accept(q, state):
                ready.append(q)
            else:
                locked.append(q)

        # Find earliest actionable prerequisite for any locked dungeon quest or unlockChain
        next_prereq: Optional[Quest] = None
        for chain in dungeon.unlock_chains:
            if state.level + 2 < chain.min_level:
                continue
            for qid in chain.quest_ids:
                q = self.dataset.quests.get(qid)
                if q and state.within_level_offset(q, max_level_offset) and self.dag.can_accept(q, state) and q.quest_id not in [x.quest_id for x in ready]:
                    next_prereq = q
                    break
            if next_prereq:
                break

        if next_prereq is None:
            for lq in sorted(locked, key=lambda x: (x.min_level, x.quest_level)):
                missing = self.dag.missing_prereqs_in_order(lq.quest_id, state)
                for mid in missing:
                    mq = self.dataset.quests.get(mid)
                    if mq and state.within_level_offset(mq, max_level_offset) and self.dag.can_accept(mq, state):
                        next_prereq = mq
                        break
                if next_prereq:
                    break

        actionable = active + ready
        total_xp = sum(
            self.xp_model.adjusted_quest_xp(q.xp, q.quest_level, state.level) + self.xp_model.estimate_quest_mob_kills_and_xp(q, state.level)[1]
            for q in actionable
        )
        total_xp += self.xp_model.estimate_dungeon_trash_xp(dungeon.recommended_level, state.level)
        total_gold = sum(q.gold_copper for q in actionable)
        max_bag = max((q.bag_slots for q in actionable), default=0)
        max_rogue = max((q.rogue_score for q in actionable), default=0.0)
        max_class = max((q.score_for_class(state.char_class) for q in actionable), default=0.0)

        remaining_total = len(actionable) + len(locked)
        readiness_ratio = (len(actionable) / remaining_total) if remaining_total > 0 else 0.0

        in_window = dungeon.min_level <= state.level <= dungeon.max_level + 1
        optimal_window = max(dungeon.min_level, dungeon.recommended_level - 2) <= state.level <= min(dungeon.max_level, dungeon.recommended_level + 3)

        return DungeonReadiness(
            dungeon=dungeon,
            in_level_window=in_window,
            optimal_window=optimal_window,
            ready_quests=ready,
            active_dungeon_quests=active,
            locked_quests=locked,
            completed_quests=completed,
            next_prereq_quest=next_prereq,
            total_dungeon_xp=total_xp,
            total_gold_copper=total_gold,
            max_bag_slots=max_bag,
            max_rogue_score=max_rogue,
            max_class_score=max_class,
            readiness_ratio=readiness_ratio,
        )

    def candidate_dungeons(
        self,
        state: CharacterState,
        dungeon_weight: int = 20,
        max_level_offset: Optional[int] = None,
    ) -> List[DungeonReadiness]:
        results: List[DungeonReadiness] = []
        cur_node = self.dataset.zones.get(self.travel_model.resolve_zone_id(state.zone_id))
        cur_region = cur_node.region if cur_node else None

        for dungeon in self.dataset.dungeons.values():
            if dungeon.faction not in ("Both", state.faction):
                continue
            if dungeon.dungeon_id in state.completed_dungeons:
                continue
            # Do not schedule low-level outdated dungeons from another continent unless dungeon_focus asks for it
            if state.level > dungeon.recommended_level + 3 and dungeon_weight < 35:
                continue
            if state.level < dungeon.min_level or state.level > dungeon.max_level + 1:
                continue
            ent_node = self.dataset.zones.get(self.travel_model.resolve_zone_id(dungeon.entrance_zone_id))
            same_region = cur_region is not None and ent_node is not None and ent_node.region == cur_region
            if not same_region and dungeon_weight < 35 and state.level < dungeon.recommended_level:
                continue
            info = self.evaluate_dungeon(dungeon, state, max_level_offset=max_level_offset)
            if info.ready_quests or info.active_dungeon_quests or info.next_prereq_quest:
                results.append(info)
        return sorted(results, key=lambda r: (r.dungeon.recommended_level, r.dungeon.min_level))

    def should_run_dungeon_now(
        self,
        info: DungeonReadiness,
        state: CharacterState,
        dungeon_weight: int,
    ) -> bool:
        if info.dungeon.dungeon_id in state.completed_dungeons:
            return False
        if state.level < info.dungeon.min_level or state.level > info.dungeon.max_level + 1:
            return False
        # Filter actionable quests to those whose starter/turn-in are reasonably accessible
        ent_c = self.travel_model.dungeon_entrance_coord(info.dungeon)
        cur_node = self.dataset.zones.get(self.travel_model.resolve_zone_id(state.zone_id))
        ent_node = self.dataset.zones.get(self.travel_model.resolve_zone_id(ent_c.zone_id))
        same_region = cur_node is not None and ent_node is not None and cur_node.region == ent_node.region

        actionable_count = len(info.ready_quests) + len(info.active_dungeon_quests)
        if actionable_count == 0:
            return False
        closing_window = state.level >= info.dungeon.recommended_level + 1 or state.level >= info.dungeon.max_level - 1
        if info.next_prereq_quest and not closing_window and info.readiness_ratio < 0.65:
            return False
        if dungeon_weight >= 35:
            return actionable_count >= 2 or (actionable_count >= 1 and (same_region or closing_window))
        if dungeon_weight >= 15:
            if same_region:
                return actionable_count >= 2 and (info.optimal_window or closing_window)
            return actionable_count >= 4 and info.readiness_ratio >= 0.6 and info.optimal_window
        # fast_xp: run high-density local dungeons when stacked quests + optimal window yield top XP/hr
        return same_region and actionable_count >= 3 and info.optimal_window

    def dungeon_prep_urgency(self, quest_id: int, state: CharacterState) -> Tuple[float, Optional[Dungeon]]:
        """Return urgency boost [0..1.5] if quest unlocks an upcoming/current dungeon window."""
        best_urgency = 0.0
        best_dungeon: Optional[Dungeon] = None
        for did in self.dag.downstream(quest_id, state).unlocked_dungeon_ids:
            dungeon = self.dataset.dungeons.get(did)
            if not dungeon or dungeon.dungeon_id in state.completed_dungeons or dungeon.faction not in ("Both", state.faction):
                continue
            # Highest urgency right before and inside the dungeon level range
            if dungeon.min_level - 3 <= state.level <= dungeon.max_level:
                closeness = 1.0 - min(1.0, abs(state.level - dungeon.recommended_level) / 6.0)
                urgency = 0.75 + 0.75 * closeness
                if urgency > best_urgency:
                    best_urgency = urgency
                    best_dungeon = dungeon
        return best_urgency, best_dungeon
