"""Comparative routing strategies: Naive Greedy, Hub-Clustered Lookahead, and Beam Search + Simulated Annealing."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .dag import QuestDAG
from .dungeon import DungeonOptimizer, DungeonReadiness
from .models import CharacterState, Dataset, ProfileConfig, Quest, RouteStep, SimulationMetrics
from .scoring import MultiObjectiveScorer
from .simulator import RouteSimulator
from .travel import TravelModel
from .xp import MAX_LEVEL, XPModel


# Rules: Represents a macro action choice (sub-zone quest cluster or dungeon run) evaluated during search.
@dataclass
class MacroDecision:
    kind: str  # "cluster" | "dungeon"
    key: str
    quests: List[Quest] = field(default_factory=list)
    dungeon_info: Optional[DungeonReadiness] = None
    score: float = 0.0
    max_level_offset: Optional[int] = None


# Rules: Beam search state pairing a simulated CharacterState with its cumulative objective score
# and ordered macro decision trace.
@dataclass
class BeamNode:
    state: CharacterState
    cumulative_score: float
    decisions: List[Tuple[str, Tuple[int, ...], Optional[str]]] = field(default_factory=list)


# Rules: Base contract for route planners; every strategy must generate a strictly topologically valid
# RouteStep sequence obeying minLevel, prereqIds, and state.max_level_offset (default +2, with empty-pool fallback).
class BaseRoutingStrategy:
    name: str
    dataset: Dataset
    dag: QuestDAG
    xp_model: XPModel
    travel_model: TravelModel
    dungeon_opt: DungeonOptimizer
    scorer: MultiObjectiveScorer
    simulator: RouteSimulator

    def __init__(
        self,
        dataset: Dataset,
        dag: QuestDAG,
        xp_model: XPModel,
        travel_model: TravelModel,
        dungeon_opt: DungeonOptimizer,
        scorer: MultiObjectiveScorer,
        simulator: RouteSimulator,
    ) -> None:
        self.dataset = dataset
        self.dag = dag
        self.xp_model = xp_model
        self.travel_model = travel_model
        self.dungeon_opt = dungeon_opt
        self.scorer = scorer
        self.simulator = simulator

    def _eligible_pool(
        self,
        state: CharacterState,
        profile: ProfileConfig,
        offset_override: Optional[int] = None,
    ) -> List[Quest]:
        avail = self.dag.available_quests(state, include_dungeon=False)
        filtered: List[Quest] = []
        green_range = self.xp_model.quest_green_range(state.level)
        want_rewards = profile.weights["reward"] >= 20
        want_dungeons = profile.weights["dungeon"] >= 15
        want_skinning = profile.weights.get("skinning", 0) >= 10 and (state.skinning or state.leatherworking)
        for q in avail:
            diff = state.level - q.quest_level
            # Keep grey quests only if they unlock a dungeon (when valued), give a bag, major class gear, or LW/Skinning value
            ds = self.dag.downstream(q.quest_id, state)
            cls_score = q.score_for_class(state.char_class)
            ds_cls_score = ds.max_score_for_class(state.char_class)
            high_special = (
                (want_rewards and (q.bag_slots >= 8 or ds.max_bag_slots >= 10 or cls_score >= 45.0 or ds_cls_score >= 55.0))
                or (q.bag_slots >= 8 and diff <= green_range + 4)
                or (want_dungeons and bool(ds.unlocked_dungeon_ids))
                or (want_skinning and (q.is_leatherworking or q.is_skinning or bool(q.lw_rewards)) and diff <= green_range + 6)
            )
            if diff > green_range and not high_special:
                continue
            # Strictly respect configurable max_level_offset (default +2 above player level)
            if not state.within_level_offset(q, offset_override):
                continue
            filtered.append(q)
        return filtered

    def _resolve_pool_and_offset(
        self,
        state: CharacterState,
        profile: ProfileConfig,
    ) -> Tuple[List[Quest], Optional[int]]:
        """Return solo pool at state.max_level_offset, escalating only if 0 quests and 0 dungeons are ready."""
        pool = self._eligible_pool(state, profile)
        if pool:
            return pool, None
        dungeon_w = profile.weights["dungeon"]
        if any(
            d.ready_quests or d.active_dungeon_quests
            for d in self.dungeon_opt.candidate_dungeons(state, dungeon_w)
        ):
            return [], None
        upper_cap = max(4, state.max_level_offset)
        for fallback_off in range(state.max_level_offset + 1, upper_cap + 1):
            fb_pool = self._eligible_pool(state, profile, offset_override=fallback_off)
            if fb_pool:
                return fb_pool, fallback_off
            if any(
                d.ready_quests or d.active_dungeon_quests
                for d in self.dungeon_opt.candidate_dungeons(state, dungeon_w, max_level_offset=fallback_off)
            ):
                return [], fallback_off
        return [], None

    def build_route(
        self,
        initial_state: CharacterState,
        profile: ProfileConfig,
        target_level: int = MAX_LEVEL,
        max_macro_steps: int = 280,
    ) -> Tuple[CharacterState, List[RouteStep], SimulationMetrics]:
        raise NotImplementedError


# Rules: Baseline strategy that greedily picks one quest at a time using frontier NavGraph search with exact
# upper-bound cutoff (zero full-pool scan when NavGraph is loaded) and no sub-zone batching or lookahead.
class NaiveGreedyStrategy(BaseRoutingStrategy):
    name = "naive_greedy"

    def build_route(
        self,
        initial_state: CharacterState,
        profile: ProfileConfig,
        target_level: int = MAX_LEVEL,
        max_macro_steps: int = 900,
    ) -> Tuple[CharacterState, List[RouteStep], SimulationMetrics]:
        state = initial_state.clone()
        steps: List[RouteStep] = []
        iters = 0

        while state.level < target_level and iters < max_macro_steps:
            iters += 1
            pool, eff_off = self._resolve_pool_and_offset(state, profile)
            best_q: Optional[Quest] = None
            best_score = -1e9

            nav = self.travel_model.nav
            src_zone = self.travel_model.resolve_zone_id(state.zone_id)
            if nav is not None and pool and src_zone in nav.zone_cells:
                max_adj_xp = max(
                    self.xp_model.adjusted_quest_xp(q.xp, q.quest_level, state.level)
                    for q in pool
                )
                best_q, best_score = nav.nearest_eligible_quests(
                    src_zone=src_zone,
                    src_x=state.x,
                    src_y=state.y,
                    eligible_pool=pool,
                    score_fn=lambda q, t_sec: (
                        self.xp_model.adjusted_quest_xp(q.xp, q.quest_level, state.level)
                        / max(60.0, t_sec + 180.0)
                    )
                    - (t_sec / 240.0),
                    upper_bound_fn=lambda t_sec: (max_adj_xp / max(60.0, t_sec + 180.0)) - (t_sec / 240.0),
                    coord_fn=self.travel_model.quest_starter_coord,
                    role="s",
                    player_level=state.level,
                    faction=state.faction,
                    allow_hearth=state.hearth_cooldown_remaining <= 0.0,
                    hearth_zone_id=state.hearth_zone_id,
                    discovered_flights=state.discovered_flights,
                )

            if best_q is None and pool:
                for q in pool:
                    sc = self.travel_model.quest_starter_coord(q)
                    t_sec, _ = self.travel_model.estimate_coord_travel(state, sc)
                    # Fallback favors nearest quest with minimal lookahead
                    adj_xp = self.xp_model.adjusted_quest_xp(q.xp, q.quest_level, state.level)
                    val = (adj_xp / max(60.0, t_sec + 180.0)) - (t_sec / 240.0)
                    if val > best_score:
                        best_score = val
                        best_q = q

            # Also consider dungeon solo-if-nearest if no nearby non-dungeon quest or very high ready count
            if best_q is None:
                d_cands = [
                    d
                    for d in self.dungeon_opt.candidate_dungeons(state, max_level_offset=eff_off)
                    if len(d.ready_quests) + len(d.active_dungeon_quests) > 0
                ]
                if d_cands:
                    self.simulator.execute_dungeon_run(d_cands[0], state, steps, max_level_offset=eff_off)
                    continue
                self.simulator.execute_bridge_grind(state, steps)
                continue

            self.simulator.execute_single_quest(best_q, state, steps)

        metrics = self.simulator.summarize_metrics(self.name, profile, initial_state.level, state, steps)
        return state, steps, metrics


# Rules: Clusters eligible quests by terrain-component & sub-zone hub, warms NavGraph single-source
# node distances once per state, evaluates 1-step zone/chain lookahead and dungeon readiness, and runs batched loops.
class HubClusterLookaheadStrategy(BaseRoutingStrategy):
    name = "hub_lookahead"

    def _candidate_macros(self, state: CharacterState, profile: ProfileConfig) -> List[MacroDecision]:
        if self.travel_model.nav is not None:
            self.travel_model.nav.single_source_node_dists(
                self.travel_model.resolve_zone_id(state.zone_id),
                state.x,
                state.y,
                player_level=state.level,
                faction=state.faction,
                allow_hearth=state.hearth_cooldown_remaining <= 0.0,
                hearth_zone_id=state.hearth_zone_id,
                discovered_flights=state.discovered_flights,
            )
        pool, eff_off = self._resolve_pool_and_offset(state, profile)
        # Ensure dungeon unlock prerequisites in current/upcoming windows are strongly surfaced
        pool_ids = {q.quest_id for q in pool}
        d_cands = self.dungeon_opt.candidate_dungeons(state, profile.weights["dungeon"], max_level_offset=eff_off)
        for d_info in d_cands:
            pq = d_info.next_prereq_quest
            if pq and pq.quest_id not in pool_ids and state.within_level_offset(pq, eff_off) and self.dag.can_accept(pq, state):
                pool.append(pq)
                pool_ids.add(pq.quest_id)

        clusters = self.travel_model.cluster_quests_by_subzone(pool)
        decisions: List[MacroDecision] = []

        for cid, c_quests in clusters.items():
            # Cap single batch size to 6 so quest log & loop stay tight
            skin_weight = profile.weights.get("skinning", 0)
            batch = sorted(
                c_quests,
                key=lambda q: (
                    -self.dungeon_opt.dungeon_prep_urgency(q.quest_id, state)[0],
                    -(self.scorer.quest_skinning_value(q, state) if skin_weight >= 15 else 0.0),
                    q.quest_level,
                    q.quest_id,
                ),
            )[:6]
            score, _, _ = self.scorer.score_cluster(batch, state, profile)

            # 1-step lookahead bonus: quests unlocked in same zone right after finishing this cluster
            same_zone_unlocks = 0.0
            b_zone = self.travel_model.resolve_zone_id(self.travel_model.quest_starter_coord(batch[0]).zone_id)
            for q in batch:
                for uq in self.dag.unlocked_by_completion(q.quest_id, state):
                    u_zone = self.travel_model.resolve_zone_id(self.travel_model.quest_starter_coord(uq).zone_id)
                    if u_zone == b_zone and state.level >= uq.min_level and state.within_level_offset(uq, eff_off):
                        same_zone_unlocks += 0.18
            decisions.append(
                MacroDecision(
                    kind="cluster",
                    key=cid,
                    quests=batch,
                    score=score + min(0.65, same_zone_unlocks),
                    max_level_offset=eff_off,
                )
            )

        for d_info in d_cands:
            if self.dungeon_opt.should_run_dungeon_now(d_info, state, profile.weights["dungeon"]):
                d_score, _ = self.scorer.score_dungeon_run(d_info, state, profile)
                decisions.append(
                    MacroDecision(
                        kind="dungeon",
                        key=f"dg_{d_info.dungeon.dungeon_id}",
                        dungeon_info=d_info,
                        score=d_score,
                        max_level_offset=eff_off,
                    )
                )

        decisions.sort(key=lambda d: d.score, reverse=True)
        return decisions

    def build_route(
        self,
        initial_state: CharacterState,
        profile: ProfileConfig,
        target_level: int = MAX_LEVEL,
        max_macro_steps: int = 900,
    ) -> Tuple[CharacterState, List[RouteStep], SimulationMetrics]:
        state = initial_state.clone()
        steps: List[RouteStep] = []
        iters = 0

        while state.level < target_level and iters < max_macro_steps:
            iters += 1
            macros = self._candidate_macros(state, profile)
            if not macros:
                d_cands = [d for d in self.dungeon_opt.candidate_dungeons(state, profile.weights["dungeon"]) if d.ready_quests or d.active_dungeon_quests]
                if d_cands:
                    self.simulator.execute_dungeon_run(d_cands[0], state, steps, allow_remote_pickups=(profile.weights["dungeon"] >= 35))
                    continue
                self.simulator.execute_bridge_grind(state, steps)
                continue

            best = macros[0]
            if best.kind == "dungeon" and best.dungeon_info is not None:
                self.simulator.execute_dungeon_run(
                    best.dungeon_info,
                    state,
                    steps,
                    allow_remote_pickups=(profile.weights["dungeon"] >= 35),
                    max_level_offset=best.max_level_offset,
                )
            else:
                self.simulator.execute_cluster_batch(best.quests, state, steps, max_level_offset=best.max_level_offset)

        metrics = self.simulator.summarize_metrics(self.name, profile, initial_state.level, state, steps)
        return state, steps, metrics


# Rules: Multi-horizon Beam Search over sub-zone clusters and dungeon timing windows followed by
# Simulated Annealing / local swap refinement on macro decision order under strict topological checks.
class BeamAnnealingOptimizer(HubClusterLookaheadStrategy):
    name = "beam_optimizer"
    beam_width: int
    branch_factor: int
    sa_iterations: int
    seed: int

    def __init__(
        self,
        dataset: Dataset,
        dag: QuestDAG,
        xp_model: XPModel,
        travel_model: TravelModel,
        dungeon_opt: DungeonOptimizer,
        scorer: MultiObjectiveScorer,
        simulator: RouteSimulator,
        beam_width: int = 2,
        branch_factor: int = 3,
        sa_iterations: int = 8,
        seed: int = 42,
    ) -> None:
        super().__init__(dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator)
        self.beam_width = beam_width
        self.branch_factor = branch_factor
        self.sa_iterations = sa_iterations
        self.seed = seed

    def _apply_macro_fast(
        self,
        macro: MacroDecision,
        state: CharacterState,
        dummy_steps: List[RouteStep],
        allow_remote: bool = False,
    ) -> Tuple[str, Tuple[int, ...], Optional[str]]:
        if macro.kind == "dungeon" and macro.dungeon_info is not None:
            self.simulator.execute_dungeon_run(
                macro.dungeon_info,
                state,
                dummy_steps,
                allow_remote_pickups=allow_remote,
                max_level_offset=macro.max_level_offset,
            )
            return ("dungeon", tuple(), macro.dungeon_info.dungeon.dungeon_id)
        qids = tuple(q.quest_id for q in macro.quests)
        self.simulator.execute_cluster_batch(macro.quests, state, dummy_steps, max_level_offset=macro.max_level_offset)
        return ("cluster", qids, None)

    def _replay_decisions(
        self,
        initial_state: CharacterState,
        decisions: Sequence[Tuple[str, Tuple[int, ...], Optional[str]]],
        profile: ProfileConfig,
        target_level: int,
    ) -> Tuple[CharacterState, List[RouteStep], float]:
        state = initial_state.clone()
        steps: List[RouteStep] = []
        used_idx: set[int] = set()
        queue = list(decisions)
        allow_remote = profile.weights["dungeon"] >= 35

        # Replay given macro order whenever currently eligible; fill remaining with hub_lookahead
        for idx, (kind, qids, did) in enumerate(queue):
            if state.level >= target_level:
                break
            if kind == "dungeon" and did and did not in state.completed_dungeons:
                dungeon = self.dataset.dungeons.get(did)
                if dungeon and dungeon.min_level <= state.level <= dungeon.max_level + 1:
                    d_info = self.dungeon_opt.evaluate_dungeon(dungeon, state)
                    if d_info.ready_quests or d_info.active_dungeon_quests:
                        self.simulator.execute_dungeon_run(d_info, state, steps, allow_remote_pickups=allow_remote)
                        used_idx.add(idx)
            elif kind == "cluster":
                qs = [
                    self.dataset.quests[qid]
                    for qid in qids
                    if qid in self.dataset.quests
                    and state.within_level_offset(self.dataset.quests[qid])
                    and self.dag.can_accept(self.dataset.quests[qid], state)
                ]
                if qs:
                    self.simulator.execute_cluster_batch(qs, state, steps)
                    used_idx.add(idx)

        # Top off if replay left level < target_level (e.g. swapped order unlocked more or changed XP)
        guard = 0
        while state.level < target_level and guard < 650:
            guard += 1
            macros = self._candidate_macros(state, profile)
            if not macros:
                d_cands = [d for d in self.dungeon_opt.candidate_dungeons(state, profile.weights["dungeon"]) if d.ready_quests or d.active_dungeon_quests]
                if d_cands:
                    self.simulator.execute_dungeon_run(d_cands[0], state, steps, allow_remote_pickups=allow_remote)
                    continue
                self.simulator.execute_bridge_grind(state, steps)
                continue
            top = macros[0]
            if top.kind == "dungeon" and top.dungeon_info:
                self.simulator.execute_dungeon_run(
                    top.dungeon_info,
                    state,
                    steps,
                    allow_remote_pickups=allow_remote,
                    max_level_offset=top.max_level_offset,
                )
            else:
                self.simulator.execute_cluster_batch(top.quests, state, steps, max_level_offset=top.max_level_offset)

        obj = self._global_route_objective(initial_state.level, state, steps, profile)
        return state, steps, obj

    def _global_route_objective(
        self,
        start_level: int,
        state: CharacterState,
        steps: Sequence[RouteStep],
        profile: ProfileConfig,
    ) -> float:
        hours = max(0.05, state.elapsed_seconds / 3600.0)
        xph = state.total_xp_earned / hours
        travel_ratio = state.travel_seconds / max(1.0, state.elapsed_seconds)
        dungeons_count = len(state.completed_dungeons)
        reward_val = (
            (state.total_gold_copper / 10000.0) * 0.15
            + state.bags_earned * 1.4
            + state.max_bag_slots_earned * 0.25
            + state.class_score_total / 90.0
        )
        skin_val = (
            state.lw_quests_completed * 1.8
            + state.skinning_quests_completed * 1.4
            + state.beast_quests_completed * 0.35
            + state.total_skinning_score / 140.0
        )
        level_progress = (state.level - start_level) + (self.xp_model.xp_pct(state) / 100.0)

        w = profile.weights
        return (
            level_progress * 1.6
            + (xph / 12000.0) * (w["xp"] / 35.0) * 3.4
            - travel_ratio * (w["travel"] / 25.0) * 10.0
            + dungeons_count * (w["dungeon"] / 20.0) * 1.8
            + reward_val * (w["reward"] / 20.0) * 1.05
            + skin_val * (w.get("skinning", 0) / 20.0) * profile.skinning_bonus * 0.9
        )

    def build_route(
        self,
        initial_state: CharacterState,
        profile: ProfileConfig,
        target_level: int = MAX_LEVEL,
        max_macro_steps: int = 750,
    ) -> Tuple[CharacterState, List[RouteStep], SimulationMetrics]:
        beam: List[BeamNode] = [BeamNode(state=initial_state.clone(), cumulative_score=0.0)]
        completed_beam: List[BeamNode] = []

        for _ in range(max_macro_steps):
            next_beam: List[BeamNode] = []
            for node in beam:
                if node.state.level >= target_level:
                    completed_beam.append(node)
                    continue

                macros = self._candidate_macros(node.state, profile)
                allow_remote = profile.weights["dungeon"] >= 35
                if not macros:
                    st_copy = node.state.clone()
                    dummy: List[RouteStep] = []
                    d_cands = [d for d in self.dungeon_opt.candidate_dungeons(st_copy, profile.weights["dungeon"]) if d.ready_quests or d.active_dungeon_quests]
                    if d_cands:
                        self.simulator.execute_dungeon_run(d_cands[0], st_copy, dummy, allow_remote_pickups=allow_remote)
                        rec = ("dungeon", tuple(), d_cands[0].dungeon.dungeon_id)
                    else:
                        self.simulator.execute_bridge_grind(st_copy, dummy)
                        rec = ("grind", tuple(), None)
                    next_beam.append(
                        BeamNode(
                            state=st_copy,
                            cumulative_score=node.cumulative_score + 0.1,
                            decisions=node.decisions + [rec],
                        )
                    )
                    continue

                # Diversify branches across distinct zones/dungeons so beam explores real macro alternatives;
                # keep Lv 1-6 starter characters anchored inside their own starter zone while local clusters exist
                chosen_macros: List[MacroDecision] = []
                seen_zones: set[str] = set()
                cur_z = self.travel_model.resolve_zone_id(node.state.zone_id)
                local_starter_macros = [
                    m
                    for m in macros
                    if node.state.level <= 6
                    and m.kind == "cluster"
                    and self.travel_model.resolve_zone_id(self.travel_model.quest_starter_coord(m.quests[0]).zone_id) == cur_z
                    and m.score > 0.0
                ]
                branch_source = local_starter_macros if local_starter_macros else macros
                for m in branch_source:
                    tag = m.key if m.kind == "dungeon" else f"z_{self.travel_model.resolve_zone_id(self.travel_model.quest_starter_coord(m.quests[0]).zone_id)}"
                    if tag not in seen_zones or len(chosen_macros) < 2:
                        chosen_macros.append(m)
                        seen_zones.add(tag)
                    if len(chosen_macros) >= self.branch_factor:
                        break

                for m in chosen_macros:
                    st_copy = node.state.clone()
                    dummy = []
                    prev_elapsed = st_copy.elapsed_seconds
                    prev_xp = st_copy.total_xp_earned
                    rec = self._apply_macro_fast(m, st_copy, dummy, allow_remote=allow_remote)
                    dt = max(1.0, st_copy.elapsed_seconds - prev_elapsed)
                    dxp = st_copy.total_xp_earned - prev_xp
                    step_gain = m.score + (dxp / dt) * 0.05
                    next_beam.append(
                        BeamNode(
                            state=st_copy,
                            cumulative_score=node.cumulative_score + step_gain,
                            decisions=node.decisions + [rec],
                        )
                    )

            if not next_beam:
                break
            # Prune beam continuously by global objective + cumulative lookahead score (avoid integer level cliffs)
            next_beam.sort(
                key=lambda b: (
                    self._global_route_objective(initial_state.level, b.state, [], profile)
                    + b.cumulative_score * 0.12
                ),
                reverse=True,
            )
            beam = next_beam[: self.beam_width]
            if all(b.state.level >= target_level for b in beam):
                completed_beam.extend(beam)
                break

        candidates = completed_beam if completed_beam else beam
        best_node = max(
            candidates,
            key=lambda b: self._global_route_objective(initial_state.level, b.state, [], profile),
        )

        # Phase 2: Simulated Annealing local swap refinement over macro decisions
        best_decisions = [d for d in best_node.decisions if d[0] in ("cluster", "dungeon")]
        best_state, best_steps, best_obj = self._replay_decisions(initial_state, best_decisions, profile, target_level)

        if len(best_decisions) >= 4 and self.sa_iterations > 0:
            rng = random.Random(self.seed)
            cur_decisions = list(best_decisions)
            cur_obj = best_obj
            temp = 1.2
            for step_i in range(self.sa_iterations):
                if len(cur_decisions) < 5:
                    break
                i = rng.randint(1, len(cur_decisions) - 2)
                j = min(len(cur_decisions) - 1, i + rng.randint(1, 3))
                cand = list(cur_decisions)
                cand[i], cand[j] = cand[j], cand[i]
                cand_state, cand_steps, cand_obj = self._replay_decisions(initial_state, cand, profile, target_level)
                # Reject if candidate introduces any prereq/minLevel violations, leaves start zone on step 1, or lowers end level
                pv, mv = self.simulator.validate_steps(cand_steps, initial_state.level)
                start_z_guard = (
                    best_steps
                    and best_steps[0].zone_id == initial_state.zone_id
                    and cand_steps
                    and cand_steps[0].zone_id != initial_state.zone_id
                )
                if pv > 0 or mv > 0 or cand_state.level < best_state.level or start_z_guard:
                    temp *= 0.94
                    continue
                delta = cand_obj - cur_obj
                if delta > 0 or rng.random() < math.exp(delta / max(0.05, temp)):
                    cur_decisions = cand
                    cur_obj = cand_obj
                    if cand_obj > best_obj:
                        best_decisions = cand
                        best_state = cand_state
                        best_steps = cand_steps
                        best_obj = cand_obj
                temp *= 0.94

        metrics = self.simulator.summarize_metrics(self.name, profile, initial_state.level, best_state, best_steps)
        return best_state, best_steps, metrics
