"""Step-by-step deterministic leveling simulator and invariant validator."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .dag import QuestDAG
from .dungeon import DungeonOptimizer, DungeonReadiness
from .models import CharacterState, Coord, Dataset, ProfileConfig, Quest, RouteStep, SimulationMetrics
from .scoring import MultiObjectiveScorer
from .travel import TravelModel
from .xp import MAX_LEVEL, XPModel

MAX_QUEST_LOG = 20
INN_HUB_ZONES = {
    1, 3, 4, 8, 10, 11, 12, 14, 15, 17, 28, 33, 38, 40, 44, 45, 85,
    130, 139, 141, 148, 215, 267, 331, 357, 361, 400, 405, 406, 440,
    618, 1377, 1497, 1519, 1537, 1637, 1638, 1657, 16591, 16593,
}
CAPITAL_HUB_ZONES: Dict[str, set[int]] = {
    "Alliance": {1519, 1537, 1657},
    "Horde": {1637, 1638, 1497},
}


# Rules: Executes macro actions (single quest, sub-zone batch cluster, or dungeon run) on a
# parameterized CharacterState across Lv 1-60 and any race/class/faction, emitting RouteSteps,
# enforcing quest log/prereq/minLevel/max_level_offset (default +2)/eligibility invariants, and summarizing metrics.
class RouteSimulator:
    dataset: Dataset
    dag: QuestDAG
    xp_model: XPModel
    travel_model: TravelModel
    dungeon_opt: DungeonOptimizer
    scorer: MultiObjectiveScorer

    def __init__(
        self,
        dataset: Dataset,
        dag: QuestDAG,
        xp_model: XPModel,
        travel_model: TravelModel,
        dungeon_opt: DungeonOptimizer,
        scorer: MultiObjectiveScorer,
    ) -> None:
        self.dataset = dataset
        self.dag = dag
        self.xp_model = xp_model
        self.travel_model = travel_model
        self.dungeon_opt = dungeon_opt
        self.scorer = scorer

    def _emit_step(
        self,
        steps: List[RouteStep],
        state: CharacterState,
        action: str,
        title: str,
        coord: Coord,
        quest: Optional[Quest] = None,
        quest_ids: Optional[List[int]] = None,
        dungeon_id: Optional[str] = None,
        npc_id: Optional[int] = None,
        npc_name: str = "",
        travel_note: str = "",
        why: str = "",
        est_minutes: float = 1.0,
        travel_seconds: float = 0.0,
        xp_gain: int = 0,
        gold_copper: int = 0,
        bag_slots: int = 0,
        rogue_score: float = 0.0,
        class_score: float = 0.0,
        unlocks_dungeon: Optional[str] = None,
        unlocks_dungeon_id: Optional[str] = None,
    ) -> RouteStep:
        resolved_zone = self.travel_model.resolve_zone_id(coord.zone_id)
        resolved_cls_score = class_score if class_score > 0.0 else (quest.score_for_class(state.char_class) if quest else 0.0)
        step = RouteStep(
            index=len(steps) + 1,
            action=action,
            title=title,
            zone_id=resolved_zone,
            zone_name=self.travel_model.zone_name(resolved_zone),
            x=round(coord.x, 1),
            y=round(coord.y, 1),
            quest_id=quest.quest_id if quest else (quest_ids[0] if quest_ids else None),
            quest_ids=list(quest_ids) if quest_ids else ([quest.quest_id] if quest else []),
            dungeon_id=dungeon_id,
            npc_id=npc_id,
            npc_name=npc_name,
            travel_note=travel_note,
            why=why,
            expected_level=state.level,
            expected_xp_pct=self.xp_model.xp_pct(state),
            est_minutes=round(max(0.2, est_minutes), 1),
            travel_seconds=round(travel_seconds, 1),
            xp_gain=int(xp_gain),
            gold_copper=int(gold_copper),
            bag_slots=int(bag_slots),
            rogue_score=round(float(rogue_score), 1),
            class_score=round(float(resolved_cls_score), 1),
            unlocks_dungeon=unlocks_dungeon,
            unlocks_dungeon_id=unlocks_dungeon_id,
            is_leatherworking=bool(quest.is_leatherworking or quest.lw_rewards) if quest else False,
            is_skinning=bool(quest.is_skinning) if quest else False,
            skinning_score=round(float(quest.skinning_score), 1) if quest else 0.0,
            required_skill_id=quest.required_skill_id if quest else None,
            required_min_skill=quest.required_min_skill if quest else 0,
        )
        steps.append(step)
        return step

    def _maybe_emit_travel(
        self,
        steps: List[RouteStep],
        state: CharacterState,
        target: Coord,
        reason: str,
    ) -> Tuple[float, float]:
        """Execute travel to target; return (total_seconds, unemitted_seconds_for_next_step)."""
        src_zone = self.travel_model.resolve_zone_id(state.zone_id)
        dst_zone = self.travel_model.resolve_zone_id(target.zone_id)
        plan = self.travel_model.execute_travel_to(state, target)
        if src_zone != dst_zone or plan.total_seconds >= 120.0:
            self._emit_step(
                steps=steps,
                state=state,
                action="TRAVEL",
                title=f"Travel to {self.travel_model.zone_name(dst_zone)}",
                coord=target,
                travel_note=plan.note,
                why=reason,
                est_minutes=plan.total_seconds / 60.0,
                travel_seconds=plan.total_seconds,
            )
            return plan.total_seconds, 0.0
        return plan.total_seconds, plan.total_seconds

    def _apply_turnin_rewards(self, quest: Quest, state: CharacterState) -> Tuple[int, Optional[str], Optional[str]]:
        adj_xp = self.xp_model.adjusted_quest_xp(quest.xp, quest.quest_level, state.level)
        self.xp_model.apply_xp(state, adj_xp, source="quest")
        state.total_gold_copper += quest.gold_copper
        if quest.bag_slots > 0:
            state.bags_earned += 1
            state.max_bag_slots_earned = max(state.max_bag_slots_earned, quest.bag_slots)
        if quest.rogue_score >= 35.0:
            state.rogue_upgrades += 1
            state.rogue_score_total += quest.rogue_score
        cls_score = quest.score_for_class(state.char_class)
        if cls_score >= 35.0:
            state.class_upgrades += 1
            state.class_score_total += cls_score
        if quest.is_leatherworking or quest.lw_rewards:
            state.lw_quests_completed += 1
        if quest.is_skinning:
            state.skinning_quests_completed += 1
        if quest.skinning_score >= 30.0 or quest.beast_target_count > 0:
            state.beast_quests_completed += 1
            state.total_skinning_score += quest.skinning_score
        self.dag.mark_completed(quest.quest_id, state)

        _, dg = self.dungeon_opt.dungeon_prep_urgency(quest.quest_id, state)
        return adj_xp, (dg.name if dg else None), (dg.dungeon_id if dg else None)

    def execute_single_quest(
        self,
        quest: Quest,
        state: CharacterState,
        steps: List[RouteStep],
    ) -> None:
        """Execute a single quest sequentially without multi-quest batching (baseline behavior)."""
        if not self.dag.can_accept(quest, state):
            return
        starter_c = self.travel_model.quest_starter_coord(quest)
        _, t1_rem = self._maybe_emit_travel(steps, state, starter_c, f"Head to {quest.name} quest giver")
        state.active_quests.add(quest.quest_id)
        _, dg = self.dungeon_opt.dungeon_prep_urgency(quest.quest_id, state)
        why_action = "DUNGEON_PREP" if (dg and not quest.is_dungeon) else "ACCEPT"
        self._emit_step(
            steps=steps,
            state=state,
            action="ACCEPT",
            title=f"Accept: {quest.name}",
            coord=starter_c,
            quest=quest,
            npc_id=quest.starter_npc_id,
            npc_name=quest.starter_name or "Quest Giver",
            travel_note=f"Go to ({starter_c.x:.1f}, {starter_c.y:.1f})",
            why=self.scorer.explain_quest_step(
                why_action,
                quest,
                self.xp_model.adjusted_quest_xp(quest.xp, quest.quest_level, state.level),
                dg.name if dg else None,
                char_class=state.char_class,
            ),
            est_minutes=(t1_rem + 15.0) / 60.0,
            travel_seconds=t1_rem,
            unlocks_dungeon=dg.name if dg else None,
            unlocks_dungeon_id=dg.dungeon_id if dg else None,
        )

        # Complete objective
        obj_c = self.travel_model.quest_objective_centroid(quest)
        if not self.xp_model.is_talk_or_breadcrumb(quest):
            _, t2_rem = self._maybe_emit_travel(steps, state, obj_c, f"Head to {quest.name} objective area")
            work_sec = self.scorer.estimate_quest_duration_seconds(quest)
            state.elapsed_seconds += work_sec
            state.quest_seconds += work_sec
            state.hearth_cooldown_remaining = max(0.0, state.hearth_cooldown_remaining - work_sec)
            _, mob_xp = self.xp_model.estimate_quest_mob_kills_and_xp(quest, state.level)
            self.xp_model.apply_xp(state, mob_xp, source="mob")
            state.cleared_objectives.add(quest.quest_id)
            self._emit_step(
                steps=steps,
                state=state,
                action="COMPLETE",
                title=f"Complete: {quest.name}",
                coord=obj_c,
                quest=quest,
                travel_note=f"Clear objective area ({obj_c.x:.1f}, {obj_c.y:.1f})",
                why=f"Earn ~{mob_xp:,} mob XP while clearing {quest.name}",
                est_minutes=(t2_rem + work_sec) / 60.0,
                travel_seconds=t2_rem,
                xp_gain=mob_xp,
            )

        # Turn in
        turnin_c = self.travel_model.quest_turnin_coord(quest)
        _, t3_rem = self._maybe_emit_travel(steps, state, turnin_c, f"Turn in {quest.name}")
        adj_xp, dg_name, dg_id = self._apply_turnin_rewards(quest, state)
        self._emit_step(
            steps=steps,
            state=state,
            action="TURN_IN",
            title=f"Turn In: {quest.name}",
            coord=turnin_c,
            quest=quest,
            npc_id=quest.turnin_npc_id,
            npc_name=quest.turnin_name or "Turn-in NPC",
            travel_note=f"Return to ({turnin_c.x:.1f}, {turnin_c.y:.1f})",
            why=self.scorer.explain_quest_step("TURN_IN", quest, adj_xp, dg_name, char_class=state.char_class),
            est_minutes=(t3_rem + 15.0) / 60.0,
            travel_seconds=t3_rem,
            xp_gain=adj_xp,
            gold_copper=quest.gold_copper,
            bag_slots=quest.bag_slots,
            rogue_score=quest.rogue_score,
            class_score=quest.score_for_class(state.char_class),
            unlocks_dungeon=dg_name,
            unlocks_dungeon_id=dg_id,
        )

    def execute_cluster_batch(
        self,
        cluster_quests: Sequence[Quest],
        state: CharacterState,
        steps: List[RouteStep],
        allow_local_followups: bool = True,
        max_level_offset: Optional[int] = None,
    ) -> List[int]:
        """Accept eligible quests in cluster together, clear objectives in TSP order, and turn in batch."""
        completed_ids: List[int] = []
        accepted: List[Quest] = []

        # Respect 20-quest log limit and configurable max_level_offset guard (default +2)
        candidates = [q for q in cluster_quests if state.within_level_offset(q, max_level_offset) and self.dag.can_accept(q, state)]
        if not candidates:
            return completed_ids

        # Sort accepts by proximity to current state then min_level
        candidates.sort(key=lambda q: (self.travel_model.estimate_coord_travel(state, self.travel_model.quest_starter_coord(q))[0], q.min_level))

        batch_zone = self.travel_model.resolve_zone_id(self.travel_model.quest_starter_coord(candidates[0]).zone_id)
        for q in candidates:
            if len(state.active_quests) >= MAX_QUEST_LOG:
                break
            if not state.within_level_offset(q, max_level_offset) or not self.dag.can_accept(q, state):
                continue
            starter_c = self.travel_model.quest_starter_coord(q)
            _, t_rem = self._maybe_emit_travel(steps, state, starter_c, f"Head to {self.travel_model.zone_name(batch_zone)} hub for {len(candidates)}-quest loop")
            if (
                len(candidates) >= 3
                and batch_zone in INN_HUB_ZONES
                and batch_zone != state.hearth_zone_id
            ):
                b_node = self.dataset.zones.get(batch_zone)
                cap_zones = CAPITAL_HUB_ZONES.get(state.faction, set())
                if (
                    b_node
                    and b_node.faction in ("Both", state.faction)
                    and (b_node.max_level >= state.level or batch_zone in cap_zones)
                ):
                    state.hearth_zone_id = batch_zone
            state.active_quests.add(q.quest_id)
            accepted.append(q)
            _, dg = self.dungeon_opt.dungeon_prep_urgency(q.quest_id, state)
            why_action = "DUNGEON_PREP" if (dg and not q.is_dungeon) else "ACCEPT"
            adj_xp = self.xp_model.adjusted_quest_xp(q.xp, q.quest_level, state.level)
            self._emit_step(
                steps=steps,
                state=state,
                action="ACCEPT",
                title=f"Accept: {q.name}",
                coord=starter_c,
                quest=q,
                npc_id=q.starter_npc_id,
                npc_name=q.starter_name or "Quest Giver",
                travel_note=f"Pick up at ({starter_c.x:.1f}, {starter_c.y:.1f})",
                why=self.scorer.explain_quest_step(
                    why_action,
                    q,
                    adj_xp,
                    dg.name if dg else None,
                    len(candidates),
                    char_class=state.char_class,
                ),
                est_minutes=(t_rem + 10.0) / 60.0,
                travel_seconds=t_rem,
                unlocks_dungeon=dg.name if dg else None,
                unlocks_dungeon_id=dg.dungeon_id if dg else None,
            )

        if not accepted:
            return completed_ids

        # Order objective clears with 2-opt TSP
        to_clear = [q for q in accepted if not self.xp_model.is_talk_or_breadcrumb(q)]
        if to_clear:
            obj_pairs = [(q.quest_id, self.travel_model.quest_objective_centroid(q)) for q in to_clear]
            obj_zone = self.travel_model.resolve_zone_id(obj_pairs[0][1].zone_id)
            cur_coord = Coord(state.zone_id, state.x, state.y)
            ordered_clear_ids = self.travel_model.order_coords_tsp(cur_coord, obj_pairs, obj_zone, state.level)
            for qid in ordered_clear_ids:
                q = self.dataset.quests[qid]
                obj_c = self.travel_model.quest_objective_centroid(q)
                _, t_rem = self._maybe_emit_travel(steps, state, obj_c, f"Sweep objective cluster in {self.travel_model.zone_name(obj_c.zone_id)}")
                work_sec = self.scorer.estimate_quest_duration_seconds(q)
                state.elapsed_seconds += work_sec
                state.quest_seconds += work_sec
                state.hearth_cooldown_remaining = max(0.0, state.hearth_cooldown_remaining - work_sec)
                _, mob_xp = self.xp_model.estimate_quest_mob_kills_and_xp(q, state.level)
                self.xp_model.apply_xp(state, mob_xp, source="mob")
                state.cleared_objectives.add(qid)
                self._emit_step(
                    steps=steps,
                    state=state,
                    action="COMPLETE",
                    title=f"Complete: {q.name}",
                    coord=obj_c,
                    quest=q,
                    travel_note=obj_c.label or f"Objective area ({obj_c.x:.1f}, {obj_c.y:.1f})",
                    why=f"Batched clear (~{mob_xp:,} mob XP)" if len(ordered_clear_ids) > 1 else f"Clear area (~{mob_xp:,} mob XP)",
                    est_minutes=(t_rem + work_sec) / 60.0,
                    travel_seconds=t_rem,
                    xp_gain=mob_xp,
                )

        # Turn in accepted quests ordered by nearest turn-in NPC
        pending_turnin = list(accepted)
        while pending_turnin:
            pending_turnin.sort(key=lambda q: self.travel_model.estimate_coord_travel(state, self.travel_model.quest_turnin_coord(q))[0])
            q = pending_turnin.pop(0)
            turnin_c = self.travel_model.quest_turnin_coord(q)
            _, t_rem = self._maybe_emit_travel(steps, state, turnin_c, f"Turn in batch at {self.travel_model.zone_name(turnin_c.zone_id)}")
            adj_xp, dg_name, dg_id = self._apply_turnin_rewards(q, state)
            completed_ids.append(q.quest_id)
            self._emit_step(
                steps=steps,
                state=state,
                action="TURN_IN",
                title=f"Turn In: {q.name}",
                coord=turnin_c,
                quest=q,
                npc_id=q.turnin_npc_id,
                npc_name=q.turnin_name or "Turn-in NPC",
                travel_note=f"Turn in at ({turnin_c.x:.1f}, {turnin_c.y:.1f})",
                why=self.scorer.explain_quest_step("TURN_IN", q, adj_xp, dg_name, len(accepted), char_class=state.char_class),
                est_minutes=(t_rem + 10.0) / 60.0,
                travel_seconds=t_rem,
                xp_gain=adj_xp,
                gold_copper=q.gold_copper,
                bag_slots=q.bag_slots,
                rogue_score=q.rogue_score,
                class_score=q.score_for_class(state.char_class),
                unlocks_dungeon=dg_name,
                unlocks_dungeon_id=dg_id,
            )

            # Smart local follow-up pickup: only grab immediate same-hub talk follow-ups whose turn-in is also close
            if allow_local_followups and len(state.active_quests) < MAX_QUEST_LOG:
                cur_z = self.travel_model.resolve_zone_id(state.zone_id)
                for fq in self.dag.unlocked_by_completion(q.quest_id, state):
                    if fq.is_dungeon or not state.within_level_offset(fq) or not self.dag.can_accept(fq, state):
                        continue
                    f_start = self.travel_model.quest_starter_coord(fq)
                    f_turnin = self.travel_model.quest_turnin_coord(fq)
                    start_z, obj_z, turnin_z, _, _ = self.travel_model.quest_hop_zones(fq)
                    if start_z == cur_z and turnin_z == cur_z and obj_z == cur_z and self.xp_model.is_talk_or_breadcrumb(fq):
                        hop, _ = self.travel_model.estimate_coord_travel(state, f_start)
                        t_hop, _ = self.travel_model.estimate_coord_travel(state, f_turnin)
                        if hop <= 60.0 and t_hop <= 90.0:
                            state.active_quests.add(fq.quest_id)
                            self._emit_step(
                                steps=steps,
                                state=state,
                                action="ACCEPT",
                                title=f"Accept: {fq.name}",
                                coord=f_start,
                                quest=fq,
                                npc_id=fq.starter_npc_id,
                                npc_name=fq.starter_name or "Quest Giver",
                                travel_note=f"Same-hub follow-up ({f_start.x:.1f}, {f_start.y:.1f})",
                                why="Immediate local follow-up unlocked at turn-in NPC",
                                est_minutes=0.3,
                            )
                            pending_turnin.append(fq)
        return completed_ids

    def execute_dungeon_run(
        self,
        info: DungeonReadiness,
        state: CharacterState,
        steps: List[RouteStep],
        is_repeat: bool = False,
        allow_remote_pickups: bool = False,
        max_level_offset: Optional[int] = None,
    ) -> List[int]:
        """Accept nearby/same-region ready dungeon quests, travel to dungeon entrance, clear dungeon, and turn in."""
        dungeon = info.dungeon
        completed_ids: List[int] = []
        run_quests: List[Quest] = list(info.active_dungeon_quests)
        ent = self.travel_model.dungeon_entrance_coord(dungeon)
        ent_zone = self.travel_model.resolve_zone_id(ent.zone_id)
        ent_node = self.dataset.zones.get(ent_zone)
        ent_region = ent_node.region if ent_node else 0

        # Filter ready dungeon quests to avoid cross-continent scavenger hunts for a single turn-in
        local_ready: List[Quest] = []
        for q in info.ready_quests:
            sc = self.travel_model.quest_starter_coord(q)
            tc = self.travel_model.quest_turnin_coord(q)
            sz = self.travel_model.resolve_zone_id(sc.zone_id)
            tz = self.travel_model.resolve_zone_id(tc.zone_id)
            s_node = self.dataset.zones.get(sz)
            t_node = self.dataset.zones.get(tz)
            same_reg = (s_node and s_node.region == ent_region) and (t_node and t_node.region == ent_region)
            if same_reg or allow_remote_pickups:
                local_ready.append(q)

        ready_sorted = sorted(
            local_ready,
            key=lambda q: self.travel_model.estimate_coord_travel(state, self.travel_model.quest_starter_coord(q))[0],
        )
        for q in ready_sorted:
            if len(state.active_quests) >= MAX_QUEST_LOG:
                break
            if not state.within_level_offset(q, max_level_offset) or not self.dag.can_accept(q, state):
                continue
            sc = self.travel_model.quest_starter_coord(q)
            _, t_rem = self._maybe_emit_travel(steps, state, sc, f"Pick up {dungeon.name} quest: {q.name}")
            state.active_quests.add(q.quest_id)
            run_quests.append(q)
            adj_xp = self.xp_model.adjusted_quest_xp(q.xp, q.quest_level, state.level)
            self._emit_step(
                steps=steps,
                state=state,
                action="DUNGEON_PREP",
                title=f"Accept Dungeon Quest: {q.name}",
                coord=sc,
                quest=q,
                dungeon_id=dungeon.dungeon_id,
                npc_id=q.starter_npc_id,
                npc_name=q.starter_name or "Quest Giver",
                travel_note=f"Prepare for {dungeon.name}",
                why=self.scorer.explain_quest_step("DUNGEON_PREP", q, adj_xp, dungeon.name, char_class=state.char_class),
                est_minutes=(t_rem + 15.0) / 60.0,
                travel_seconds=t_rem,
                unlocks_dungeon=dungeon.name,
                unlocks_dungeon_id=dungeon.dungeon_id,
            )

        _, t_ent_rem = self._maybe_emit_travel(steps, state, ent, f"Enter {dungeon.name} (Lv {dungeon.min_level}-{dungeon.max_level})")

        run_sec = 1350.0
        state.elapsed_seconds += run_sec
        state.dungeon_seconds += run_sec
        state.hearth_cooldown_remaining = max(0.0, state.hearth_cooldown_remaining - run_sec)

        trash_xp = self.xp_model.estimate_dungeon_trash_xp(dungeon.recommended_level, state.level, is_repeat=is_repeat)
        quest_kill_xp = sum(self.xp_model.estimate_quest_mob_kills_and_xp(q, state.level)[1] for q in run_quests)
        total_mob_xp = trash_xp + quest_kill_xp
        self.xp_model.apply_xp(state, total_mob_xp, source="mob")
        for q in run_quests:
            state.cleared_objectives.add(q.quest_id)
        state.completed_dungeons.add(dungeon.dungeon_id)

        qids = [q.quest_id for q in run_quests]
        self._emit_step(
            steps=steps,
            state=state,
            action="DUNGEON_RUN",
            title=f"Dungeon Run: {dungeon.name}",
            coord=ent,
            quest_ids=qids,
            dungeon_id=dungeon.dungeon_id,
            travel_note=f"Entrance in {self.travel_model.zone_name(dungeon.entrance_zone_id)} ({ent.x:.1f}, {ent.y:.1f})",
            why=f"Batched {len(run_quests)} dungeon quest(s) in optimal Lv {dungeon.min_level}-{dungeon.max_level} window (+{total_mob_xp:,} kill XP + boss drops)",
            est_minutes=(t_ent_rem + run_sec) / 60.0,
            travel_seconds=t_ent_rem,
            xp_gain=total_mob_xp,
            unlocks_dungeon=dungeon.name,
            unlocks_dungeon_id=dungeon.dungeon_id,
        )

        # Turn in dungeon quests
        pending = list(run_quests)
        while pending:
            pending.sort(key=lambda q: self.travel_model.estimate_coord_travel(state, self.travel_model.quest_turnin_coord(q))[0])
            q = pending.pop(0)
            tc = self.travel_model.quest_turnin_coord(q)
            _, t_turn_rem = self._maybe_emit_travel(steps, state, tc, f"Turn in {dungeon.name} quest: {q.name}")
            adj_xp, dg_name, dg_id = self._apply_turnin_rewards(q, state)
            completed_ids.append(q.quest_id)
            self._emit_step(
                steps=steps,
                state=state,
                action="TURN_IN",
                title=f"Turn In: {q.name}",
                coord=tc,
                quest=q,
                dungeon_id=dungeon.dungeon_id,
                npc_id=q.turnin_npc_id,
                npc_name=q.turnin_name or "Turn-in NPC",
                travel_note=f"Turn in at ({tc.x:.1f}, {tc.y:.1f})",
                why=self.scorer.explain_quest_step("TURN_IN", q, adj_xp, dungeon.name, len(run_quests), char_class=state.char_class),
                est_minutes=(t_turn_rem + 15.0) / 60.0,
                travel_seconds=t_turn_rem,
                xp_gain=adj_xp,
                gold_copper=q.gold_copper,
                bag_slots=q.bag_slots,
                rogue_score=q.rogue_score,
                class_score=q.score_for_class(state.char_class),
                unlocks_dungeon=dg_name or dungeon.name,
                unlocks_dungeon_id=dg_id or dungeon.dungeon_id,
            )
        return completed_ids

    def execute_bridge_grind(self, state: CharacterState, steps: List[RouteStep]) -> None:
        """Fallback only if quest/dungeon pool at current level is temporarily exhausted before 60."""
        needed = max(100, self.xp_model.xp_for_level(state.level) - state.xp)
        per_mob = max(45, self.xp_model.base_mob_xp(state.level, state.level))
        kills = max(3, min(140, (needed // per_mob) + 1))
        xp_gain = kills * per_mob
        sec = kills * 18.0
        state.elapsed_seconds += sec
        state.quest_seconds += sec
        state.hearth_cooldown_remaining = max(0.0, state.hearth_cooldown_remaining - sec)
        self.xp_model.apply_xp(state, xp_gain, source="mob")
        self._emit_step(
            steps=steps,
            state=state,
            action="COMPLETE",
            title=f"Bridge Mob Grind (Lv {state.level} -> Next Hub)",
            coord=Coord(state.zone_id, state.x, state.y),
            travel_note=f"Grind ~{kills} on-level mobs in {self.travel_model.zone_name(state.zone_id)}",
            why=f"Bridge {xp_gain:,} XP to unlock higher minLevel quests",
            est_minutes=sec / 60.0,
            xp_gain=xp_gain,
        )

    def summarize_metrics(
        self,
        strategy_name: str,
        profile: ProfileConfig,
        start_level: int,
        state: CharacterState,
        steps: Sequence[RouteStep],
    ) -> SimulationMetrics:
        prereq_violations, min_level_violations = self.validate_steps(steps, start_level)
        total_hours = max(1e-6, state.elapsed_seconds / 3600.0)
        travel_hours = state.travel_seconds / 3600.0
        travel_pct = (state.travel_seconds / max(1.0, state.elapsed_seconds)) * 100.0
        avg_xph = state.total_xp_earned / total_hours

        dungeons_done: List[str] = []
        dungeon_q_count = 0
        for st in steps:
            if st.action == "DUNGEON_RUN" and st.dungeon_id and st.dungeon_id not in dungeons_done:
                dungeons_done.append(st.dungeon_id)
            if st.action == "TURN_IN" and st.quest_id and st.quest_id in self.dataset.quests:
                if self.dataset.quests[st.quest_id].is_dungeon:
                    dungeon_q_count += 1

        avg_rogue = (state.rogue_score_total / state.rogue_upgrades) if state.rogue_upgrades > 0 else 0.0
        avg_class = (state.class_score_total / state.class_upgrades) if state.class_upgrades > 0 else 0.0
        level_hours = {lvl: round(sec / 3600.0, 2) for lvl, sec in sorted(state.level_timestamps.items())}

        return SimulationMetrics(
            strategy_name=strategy_name,
            profile_id=profile.profile_id,
            start_level=start_level,
            end_level=state.level,
            end_xp_pct=self.xp_model.xp_pct(state),
            total_steps=len(steps),
            quests_completed=len(state.completed_quests),
            dungeon_quests_completed=dungeon_q_count,
            dungeons_scheduled=dungeons_done,
            total_time_hours=round(total_hours, 2),
            travel_time_hours=round(travel_hours, 2),
            travel_share_pct=round(travel_pct, 1),
            avg_xp_per_hour=round(avg_xph, 1),
            total_xp_earned=state.total_xp_earned,
            quest_xp_earned=state.quest_xp_earned,
            mob_xp_earned=state.mob_xp_earned,
            total_gold_copper=state.total_gold_copper,
            bags_earned=state.bags_earned,
            max_bag_slots_earned=state.max_bag_slots_earned,
            rogue_upgrades=state.rogue_upgrades,
            avg_rogue_score=round(avg_rogue, 1),
            prereq_violations=prereq_violations,
            min_level_violations=min_level_violations,
            level_timestamps_hours=level_hours,
            lw_quests_completed=state.lw_quests_completed,
            skinning_quests_completed=state.skinning_quests_completed,
            beast_quests_completed=state.beast_quests_completed,
            total_skinning_score=round(state.total_skinning_score, 1),
            faction=state.faction,
            race=state.race,
            char_class=state.char_class,
            start_zone_id=steps[0].zone_id if steps else state.zone_id,
            class_upgrades=state.class_upgrades,
            avg_class_score=round(avg_class, 1),
        )

    def validate_steps(
        self,
        steps: Sequence[RouteStep],
        start_level: int,
    ) -> Tuple[int, int]:
        """Independently verify topological prerequisite ordering and minLevel gating across steps."""
        prereq_violations = 0
        min_level_violations = 0
        seen_done: set[int] = set()
        for st in steps:
            if st.action in ("ACCEPT", "DUNGEON_PREP") and st.quest_id in self.dataset.quests:
                q = self.dataset.quests[st.quest_id]
                if st.expected_level < q.min_level:
                    min_level_violations += 1
                if not all(pid in seen_done for pid in q.prereq_ids):
                    prereq_violations += 1
            elif st.action == "TURN_IN" and st.quest_id in self.dataset.quests:
                seen_done.add(st.quest_id)
        return prereq_violations, min_level_violations

    def validate_character_steps(
        self,
        steps: Sequence[RouteStep],
        character_template: CharacterState,
    ) -> Tuple[int, int, int, int]:
        """Verify prereq, minLevel, max_level_offset solo ceiling (default +2), and race/class/faction eligibility."""
        prereq_v, min_v = self.validate_steps(steps, character_template.level)
        red_v = 0
        elig_v = 0
        max_offset = character_template.max_level_offset
        for st in steps:
            if st.action in ("ACCEPT", "DUNGEON_PREP") and st.quest_id in self.dataset.quests:
                q = self.dataset.quests[st.quest_id]
                if (
                    st.action == "ACCEPT"
                    and not q.is_dungeon
                    and q.effective_quest_level(st.expected_level) > st.expected_level + max_offset
                ):
                    red_v += 1
                if not self.dag.is_eligible_for_character(q, character_template):
                    elig_v += 1
        return prereq_v, min_v, red_v, elig_v
