"""Multi-objective scoring profiles, per-quest feature normalization, and step explanations."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .dag import QuestDAG
from .dungeon import DungeonOptimizer, DungeonReadiness
from .models import CharacterState, Dataset, ProfileConfig, Quest
from .travel import TravelModel
from .xp import XPModel

PROFILES: Dict[str, ProfileConfig] = {
    "balanced": ProfileConfig(
        profile_id="balanced",
        label="Balanced",
        description="Balanced XP/hour, tight sub-zone travel batching, timely dungeon unlocks, and Skinning/Leatherworking synergy.",
        weights={"xp": 42, "travel": 23, "dungeon": 18, "reward": 7, "skinning": 10},
        dungeon_bonus=1.0,
        reward_bonus=1.0,
        travel_penalty_scale=1.0,
        xp_rate_scale=1.0,
        skinning_bonus=1.0,
    ),
    "skinning_lw": ProfileConfig(
        profile_id="skinning_lw",
        label="Skinning & Leatherworking",
        description="Prioritizes Leatherworking/Skinning profession quests and high-density beast/dragonkin hunting hubs.",
        weights={"xp": 30, "travel": 18, "dungeon": 12, "reward": 10, "skinning": 30},
        dungeon_bonus=0.85,
        reward_bonus=1.0,
        travel_penalty_scale=0.95,
        xp_rate_scale=0.9,
        skinning_bonus=2.3,
    ),
    "dungeon_focus": ProfileConfig(
        profile_id="dungeon_focus",
        label="Dungeon Focus",
        description="Prioritizes dungeon unlock chains early and schedules every level-appropriate dungeon run at peak readiness.",
        weights={"xp": 28, "travel": 15, "dungeon": 43, "reward": 9, "skinning": 5},
        dungeon_bonus=2.3,
        reward_bonus=0.85,
        travel_penalty_scale=0.75,
        xp_rate_scale=0.8,
        skinning_bonus=0.5,
    ),
    "treasure_bags": ProfileConfig(
        profile_id="treasure_bags",
        label="Gold & Bags",
        description="Balances leveling speed with bag slot upgrades, high-gold quests, class BiS gear, and Skinning/LW rewards.",
        weights={"xp": 22, "travel": 14, "dungeon": 8, "reward": 46, "skinning": 10},
        dungeon_bonus=0.85,
        reward_bonus=2.5,
        travel_penalty_scale=0.8,
        xp_rate_scale=0.75,
        skinning_bonus=0.9,
    ),
    "fast_xp": ProfileConfig(
        profile_id="fast_xp",
        label="Fast XP & Low Travel",
        description="Maximizes pure XP/hour and minimal backtracking; skips slow detours and low-yield dungeon trips.",
        weights={"xp": 55, "travel": 35, "dungeon": 5, "reward": 5, "skinning": 0},
        dungeon_bonus=0.35,
        reward_bonus=0.25,
        travel_penalty_scale=1.65,
        xp_rate_scale=1.35,
        skinning_bonus=0.0,
    ),
}


# Rules: Computes normalized per-quest features and multi-objective cluster/dungeon scores (XP, travel, dungeon, gear/bags, Skinning/LW profession priority)
# and scores candidate quests, sub-zone clusters, and dungeon runs under any ProfileConfig and CharacterState class/race/faction.
class MultiObjectiveScorer:
    dataset: Dataset
    dag: QuestDAG
    xp_model: XPModel
    travel_model: TravelModel
    dungeon_opt: DungeonOptimizer

    def __init__(
        self,
        dataset: Dataset,
        dag: QuestDAG,
        xp_model: XPModel,
        travel_model: TravelModel,
        dungeon_opt: DungeonOptimizer,
    ) -> None:
        self.dataset = dataset
        self.dag = dag
        self.xp_model = xp_model
        self.travel_model = travel_model
        self.dungeon_opt = dungeon_opt

    @staticmethod
    def quest_skinning_value(quest: Quest, state: Optional[CharacterState] = None) -> float:
        """Compute [0..1] Skinning/Leatherworking synergy score for a quest."""
        has_skin = True if state is None else state.skinning
        has_lw = True if state is None else state.leatherworking
        if not has_skin and not has_lw:
            return 0.0
        skin_base = (quest.skinning_score / 100.0) if has_skin else 0.0
        lw_bonus = 0.0
        if has_lw:
            if quest.is_leatherworking:
                lw_bonus = 0.90
            elif quest.lw_rewards:
                lw_bonus = 0.65
        skin_q_bonus = 0.75 if (quest.is_skinning and has_skin) else 0.0
        beast_bonus = 0.15 if (has_skin and quest.beast_target_count >= 12) else 0.0
        return min(1.0, max(skin_base, lw_bonus, skin_q_bonus) + beast_bonus)

    def compute_quest_features(self, quest: Quest, target_class: str = "ROGUE") -> Dict[str, float]:
        """Compute static normalized [0..1] features embedded in GeneratedData.lua."""
        ds = self.dag.downstream(quest.quest_id)
        # Reference XP scale around level-appropriate quests (~1800-8000 XP)
        xp_norm = min(1.0, (quest.xp + 0.35 * ds.discounted_xp) / 9000.0)
        gold_norm = min(1.0, (quest.gold_copper + 0.35 * ds.discounted_gold) / 20000.0)
        bag_slots = max(quest.bag_slots, ds.max_bag_slots if ds.max_bag_slots >= 8 else 0)
        bag_norm = min(1.0, bag_slots / 16.0) if bag_slots > 0 else 0.0
        rogue_norm = min(1.0, max(quest.rogue_score, 0.7 * ds.max_rogue_score) / 100.0)
        target_raw = max(quest.score_for_class(target_class), quest.max_class_score(), 0.7 * max(ds.max_rogue_score, max(ds.max_class_scores.values(), default=0.0)))
        best_class_norm = min(1.0, target_raw / 100.0)
        reward_norm = min(1.0, 0.45 * best_class_norm + 0.35 * bag_norm + 0.20 * gold_norm)
        dungeon_norm = 1.0 if quest.is_dungeon else (0.75 if ds.unlocked_dungeon_ids else 0.0)
        skinning_norm = min(
            1.0,
            self.quest_skinning_value(quest)
            + 0.15 * min(1.0, ds.discounted_skinning / 120.0)
            + (0.15 if ds.has_lw_followup else 0.0),
        )

        # Travel penalty hint: higher for cross-zone starter->turnin hops or isolated breadcrumb quests
        start_c = self.travel_model.quest_starter_coord(quest)
        turnin_c = self.travel_model.quest_turnin_coord(quest)
        cross_zone_hint = 0.35 if start_c.zone_id != turnin_c.zone_id else 0.08
        travel_hint = min(1.0, cross_zone_hint + (0.20 if quest.is_breadcrumb else 0.0))

        return {
            "xpValue": round(xp_norm, 3),
            "travelPenaltyHint": round(travel_hint, 3),
            "dungeonUnlockValue": round(dungeon_norm, 3),
            "rewardValue": round(reward_norm, 3),
            "goldScore": round(gold_norm, 3),
            "bagScore": round(bag_norm, 3),
            "rogueGearScore": round(rogue_norm, 3),
            "bestClassGearScore": round(best_class_norm, 3),
            "skinningValue": round(skinning_norm, 3),
        }

    def estimate_quest_duration_seconds(self, quest: Quest) -> float:
        """Estimate active completion time excluding inter-step travel."""
        if quest.is_dungeon:
            return 360.0
        if self.xp_model.is_talk_or_breadcrumb(quest):
            return 45.0
        base = 175.0
        if quest.objective_count and quest.objective_count > 0:
            base = 95.0 + quest.objective_count * 12.0
        if quest.is_group:
            base += 110.0
        return min(480.0, base)

    def score_single_quest(
        self,
        quest: Quest,
        state: CharacterState,
        profile: ProfileConfig,
        travel_seconds: float,
    ) -> float:
        adj_xp = self.xp_model.adjusted_quest_xp(quest.xp, quest.quest_level, state.level)
        _, mob_xp = self.xp_model.estimate_quest_mob_kills_and_xp(quest, state.level)
        ds = self.dag.downstream(quest.quest_id, state)
        effective_xp = adj_xp + mob_xp + 0.28 * ds.discounted_xp
        cls_score = quest.score_for_class(state.char_class)
        ds_cls_score = ds.max_score_for_class(state.char_class)

        # Penalize high-level quests (>+2) gently even when slider permits them, and grey quests heavily
        level_diff = quest.effective_quest_level(state.level) - state.level
        difficulty_mult = 1.0
        if level_diff >= 5:
            difficulty_mult = 0.45
        elif level_diff >= 4:
            difficulty_mult = 0.55
        elif level_diff == 3:
            difficulty_mult = 0.85
        elif adj_xp < quest.xp * 0.45 and quest.bag_slots == 0 and cls_score < 40 and not ds.unlocked_dungeon_ids:
            difficulty_mult = 0.35

        work_seconds = max(30.0, self.estimate_quest_duration_seconds(quest))
        total_seconds = work_seconds + max(5.0, travel_seconds)
        xp_per_hour = (effective_xp / total_seconds) * 3600.0
        xp_component = (xp_per_hour / 28000.0) * (profile.weights["xp"] / 35.0) * difficulty_mult

        urgency, _ = self.dungeon_opt.dungeon_prep_urgency(quest.quest_id, state)
        dungeon_component = urgency * (profile.weights["dungeon"] / 20.0) * 1.15

        gold_val = min(1.5, (quest.gold_copper + 0.25 * ds.discounted_gold) / 10000.0)
        bag_val = (quest.bag_slots / 8.0) * 1.6 if quest.bag_slots > 0 else (ds.max_bag_slots / 12.0 * 0.45 if ds.max_bag_slots >= 8 else 0.0)
        class_val = (cls_score / 45.0) + (0.25 * ds_cls_score / 65.0)
        reward_component = (0.4 * gold_val + 0.9 * bag_val + 0.75 * class_val) * (profile.weights["reward"] / 20.0)

        w_skin = profile.weights.get("skinning", 0)
        skin_val = self.quest_skinning_value(quest, state)
        skin_ds = 0.25 * (ds.max_skinning_score / 100.0) + (0.35 if ds.has_lw_followup else 0.0)
        skinning_component = (skin_val + skin_ds) * (w_skin / 20.0) * profile.skinning_bonus * 0.85

        # Breadcrumb smart penalty when remote unless it unlocks an imminent dungeon/hub
        breadcrumb_penalty = 0.0
        if self.xp_model.is_talk_or_breadcrumb(quest) and travel_seconds > 180.0 and urgency < 0.5:
            breadcrumb_penalty = 0.45

        travel_penalty = (travel_seconds / 180.0) * (profile.weights["travel"] / 25.0) * 0.55
        return xp_component + dungeon_component + reward_component + skinning_component - travel_penalty - breadcrumb_penalty

    def score_cluster(
        self,
        cluster_quests: List[Quest],
        state: CharacterState,
        profile: ProfileConfig,
    ) -> Tuple[float, float, str]:
        """Score a sub-zone batch of quests together, accounting for shared and cross-zone travel."""
        if not cluster_quests:
            return -999.0, 0.0, ""
        # Travel once from current position to cluster starter anchor
        anchor = self.travel_model.quest_starter_coord(cluster_quests[0])
        travel_to_cluster, _ = self.travel_model.estimate_coord_travel(state, anchor)

        total_adj_xp = 0.0
        total_work = 0.0
        total_reward = 0.0
        total_skinning = 0.0
        prof_quest_bonus = 0.0
        has_skin = state.skinning
        has_lw = state.leatherworking
        skin_weight = profile.weights.get("skinning", 0)
        max_dungeon_urgency = 0.0
        unlock_dungeon_name: Optional[str] = None

        for idx, q in enumerate(cluster_quests):
            adj_xp = self.xp_model.adjusted_quest_xp(q.xp, q.quest_level, state.level)
            _, mob_xp = self.xp_model.estimate_quest_mob_kills_and_xp(q, state.level)
            ds = self.dag.downstream(q.quest_id, state)
            chain_bonus_xp = min(max(600.0, adj_xp * 0.9), 0.18 * ds.discounted_xp)
            total_adj_xp += adj_xp + mob_xp + chain_bonus_xp
            total_work += self.estimate_quest_duration_seconds(q)

            # Local hop between quests in the same cluster is small
            local_hop = 22.0 if idx > 0 else 0.0
            total_work += local_hop

            gold_val = min(1.5, (q.gold_copper + 0.25 * ds.discounted_gold) / 10000.0)
            bag_val = (
                (q.bag_slots / 8.0) * 1.9
                if q.bag_slots > 0
                else ((ds.max_bag_slots / 10.0) * 0.85 if ds.max_bag_slots >= 8 else 0.0)
            )
            cls_score = q.score_for_class(state.char_class)
            ds_cls_score = ds.max_score_for_class(state.char_class)
            class_val = (cls_score / 45.0) + (0.28 * ds_cls_score / 55.0)
            total_reward += 0.35 * gold_val + 0.95 * bag_val + 0.75 * class_val

            q_skin_val = self.quest_skinning_value(q, state)
            lw_prof = (
                (1.30 if (q.is_leatherworking and has_lw) else 0.0)
                + (1.15 if (q.is_skinning and has_skin) else 0.0)
                + (0.80 if (q.lw_rewards and has_lw and not q.is_leatherworking) else 0.0)
            )
            followup_lw = (
                (0.75 if (ds.has_lw_followup and (has_lw or has_skin)) else 0.0)
                + (0.50 * min(2.0, ds.discounted_skinning / 120.0) if (has_lw or has_skin) else 0.0)
            )
            excl_lw_loss = 0.0
            if (
                skin_weight >= 20
                and (has_lw or has_skin)
                and lw_prof == 0.0
                and q.exclusive_to
            ):
                for ex_id in q.exclusive_to:
                    ex_q = self.dataset.quests.get(ex_id)
                    if ex_q is not None and self.dag.is_eligible_for_character(ex_q, state):
                        ex_ds = self.dag.downstream(ex_id, state)
                        if ex_ds.has_lw_followup and ex_ds.discounted_skinning > max(120.0, ds.discounted_skinning * 1.5):
                            excl_lw_loss = 3.5
                            break
            prof_quest_bonus += lw_prof + followup_lw - excl_lw_loss
            total_skinning += (
                q_skin_val
                + 0.35 * min(1.0, ds.discounted_skinning / 120.0)
                + (0.25 if ds.has_lw_followup else 0.0)
            )

            urg, dg = self.dungeon_opt.dungeon_prep_urgency(q.quest_id, state)
            if urg > max_dungeon_urgency:
                max_dungeon_urgency = urg
                unlock_dungeon_name = dg.name if dg else None

        internal_travel, pure_local, all_same_region = self.travel_model.estimate_cluster_internal_travel(
            cluster_quests,
            state.level,
            state.discovered_flights,
            faction=state.faction,
        )
        cluster_zone = self.travel_model.resolve_zone_id(anchor.zone_id)
        cur_zone = self.travel_model.resolve_zone_id(state.zone_id)

        total_travel = travel_to_cluster + internal_travel
        total_sec = max(45.0, total_travel + total_work)
        xp_per_hour = (total_adj_xp / total_sec) * 3600.0

        # Strong zone-continuity bonus avoids zig-zagging across Azeroth before finishing a hub
        cur_region = self.travel_model.zone_region(cur_zone)
        dst_region = self.travel_model.zone_region(cluster_zone)
        same_region = cur_region is not None and dst_region == cur_region and all_same_region

        if cluster_zone == cur_zone and pure_local:
            immediate_hub = 2.4 if (travel_to_cluster <= 45.0 and len(cluster_quests) >= 4) else (0.9 if travel_to_cluster <= 90.0 else 0.0)
            zone_bonus = (2.8 + immediate_hub) * (0.85 + 0.15 * profile.travel_penalty_scale)
        elif cluster_zone == cur_zone:
            zone_bonus = (-0.25 if same_region else -1.8) * profile.travel_penalty_scale
        elif same_region and pure_local:
            zone_bonus = 0.85
        elif same_region:
            zone_bonus = -0.85 * profile.travel_penalty_scale
        else:
            zone_bonus = -2.6 * profile.travel_penalty_scale
        if state.level <= 6 and cluster_zone != cur_zone:
            zone_bonus -= 3.5

        # Penalize isolated 1-2 quest hops with heavy travel unless carrying urgent dungeon/bag/BiS value
        solo_xzone_penalty = 0.0
        if (
            len(cluster_quests) <= 2
            and total_travel >= 220.0
            and max_dungeon_urgency < 0.5
            and total_reward < 0.9
            and (skin_weight < 20 or (total_skinning < 0.65 and prof_quest_bonus < 0.55))
        ):
            base_pen = 1.15 if (not pure_local or len(cluster_quests) == 1) else 0.65
            solo_xzone_penalty = base_pen * profile.travel_penalty_scale

        # Cluster density bonus rewards accepting/clearing 2-6 local quests together
        density_bonus = min(1.5, 0.30 * max(0, len(cluster_quests) - 1)) if pure_local else min(0.5, 0.12 * max(0, len(cluster_quests) - 1))

        # Level fit penalty if quests are borderline above +2 (+3..+5) or all grey
        avg_q_lvl = sum(q.effective_quest_level(state.level) for q in cluster_quests) / len(cluster_quests)
        level_gap = avg_q_lvl - state.level
        fit_mult = 1.0
        if level_gap > 3.5:
            fit_mult = 0.55
        elif level_gap > 2.5:
            fit_mult = 0.80
        elif level_gap < -7.5 and total_reward < 0.8 and max_dungeon_urgency < 0.5:
            fit_mult = 0.35

        travel_penalty = (
            ((travel_to_cluster + 0.8 * internal_travel) / 165.0)
            * (profile.weights["travel"] / 25.0)
            * profile.travel_penalty_scale
            * 0.85
        )
        prof_focus_scale = 4.5 if skin_weight >= 20 else 0.0
        # Non-profession profiles deprioritize pure crafting/skinning skill-req detours (leather prep/trainer stops)
        craft_detour_penalty = 0.0
        if skin_weight < 20:
            craft_count = sum(
                (1.0 if q.is_leatherworking else (0.55 if q.is_skinning else 0.0))
                for q in cluster_quests
            )
            craft_detour_penalty = craft_count * (3.5 if skin_weight == 0 else 2.6)
        skinning_component = (
            (total_skinning + prof_focus_scale * prof_quest_bonus)
            * (skin_weight / 20.0)
            * (0.65 + 0.35 * profile.skinning_bonus)
            * 0.85
            - craft_detour_penalty
        )
        score = (
            (xp_per_hour / 26000.0) * (profile.weights["xp"] / 35.0) * (0.8 + 0.2 * profile.xp_rate_scale) * fit_mult
            + max_dungeon_urgency * (profile.weights["dungeon"] / 20.0) * (0.75 + 0.25 * profile.dungeon_bonus) * 1.15
            + total_reward * (profile.weights["reward"] / 20.0) * (0.7 + 0.3 * profile.reward_bonus) * 0.65
            + skinning_component
            + zone_bonus
            + density_bonus
            - travel_penalty
            - solo_xzone_penalty
        )
        reason = self._build_cluster_reason(
            cluster_quests,
            total_adj_xp,
            total_travel,
            unlock_dungeon_name,
            char_class=state.char_class,
        )
        return score, total_sec, reason

    def score_dungeon_run(
        self,
        info: DungeonReadiness,
        state: CharacterState,
        profile: ProfileConfig,
    ) -> Tuple[float, float]:
        ent = self.travel_model.dungeon_entrance_coord(info.dungeon)
        travel_sec, _ = self.travel_model.estimate_coord_travel(state, ent)
        cur_region = self.travel_model.zone_region(state.zone_id)
        ent_region = self.travel_model.zone_region(ent.zone_id)
        same_zone = self.travel_model.resolve_zone_id(state.zone_id) == self.travel_model.resolve_zone_id(ent.zone_id)
        same_region = cur_region is not None and ent_region == cur_region

        run_sec = 1350.0  # ~22.5 min efficient 5-man quest clear
        total_sec = travel_sec + run_sec
        xp_per_hour = (info.total_dungeon_xp / total_sec) * 3600.0

        ready_count = len(info.ready_quests) + len(info.active_dungeon_quests)
        reward_val = (info.total_gold_copper / 15000.0) * 0.4 + (info.max_bag_slots / 10.0) * 1.4 + (info.max_class_score / 45.0)
        window_bonus = 0.8 if (info.optimal_window and state.level >= info.dungeon.recommended_level - 1) else (0.35 if info.optimal_window else 0.15)
        region_adj = 1.6 if same_zone else (0.35 if same_region else -1.5 * profile.travel_penalty_scale)
        score = (
            (xp_per_hour / 26000.0) * (profile.weights["xp"] / 35.0) * (0.8 + 0.2 * profile.xp_rate_scale)
            + (ready_count * 0.42 + info.readiness_ratio * 0.75 + window_bonus) * (profile.weights["dungeon"] / 20.0) * (0.75 + 0.25 * profile.dungeon_bonus)
            + reward_val * (profile.weights["reward"] / 20.0) * (0.7 + 0.3 * profile.reward_bonus) * 0.65
            + region_adj
            - (travel_sec / 190.0) * (profile.weights["travel"] / 25.0) * profile.travel_penalty_scale * 0.85
        )
        return score, total_sec

    @staticmethod
    def _build_cluster_reason(
        quests: List[Quest],
        total_xp: float,
        travel_sec: float,
        unlock_dungeon_name: Optional[str],
        char_class: str = "ROGUE",
    ) -> str:
        parts: List[str] = []
        if len(quests) > 1:
            parts.append(f"Batches {len(quests)} nearby quests (~{int(round(total_xp)):,} XP)")
        else:
            parts.append(f"~{int(round(total_xp)):,} XP")
        if unlock_dungeon_name:
            parts.append(f"unlocks {unlock_dungeon_name}")
        best_bag = max((q.bag_slots for q in quests), default=0)
        if best_bag > 0:
            parts.append(f"rewards {best_bag}-slot bag")
        best_cls = max((q.score_for_class(char_class) for q in quests), default=0.0)
        if best_cls >= 50:
            parts.append(f"strong {char_class.title()} upgrade (BiS {best_cls:.0f})")
        if any(q.is_leatherworking or q.lw_rewards for q in quests):
            parts.append("Leatherworking quest/recipe")
        best_skin = max((q.skinning_score for q in quests), default=0.0)
        if best_skin >= 50:
            parts.append(f"high beast skinning ({best_skin:.0f}/100)")
        if travel_sec <= 90:
            parts.append("minimal travel")
        return "; ".join(parts)

    @staticmethod
    def explain_quest_step(
        action: str,
        quest: Quest,
        adj_xp: int,
        unlocks_dungeon: Optional[str] = None,
        batch_size: int = 1,
        char_class: str = "ROGUE",
    ) -> str:
        bits: List[str] = []
        if batch_size > 1:
            bits.append(f"Grouped in {batch_size}-quest local loop")
        if action == "DUNGEON_PREP" and unlocks_dungeon:
            bits.append(f"Prerequisite for {unlocks_dungeon}")
        elif unlocks_dungeon:
            bits.append(f"Feeds {unlocks_dungeon} chain")
        if adj_xp > 0:
            bits.append(f"+{adj_xp:,} quest XP")
        if quest.bag_slots > 0:
            bits.append(f"{quest.bag_slots}-slot bag")
        cls_score = quest.score_for_class(char_class)
        if cls_score >= 45:
            bits.append(f"{char_class.title()} BiS ({cls_score:.0f}/100)")
        if quest.is_leatherworking or quest.lw_rewards:
            bits.append("Leatherworking")
        if quest.is_skinning or quest.skinning_score >= 45:
            bits.append(f"Skinnable beasts ({quest.skinning_score:.0f}/100)")
        if quest.gold_copper >= 5000:
            bits.append(f"{quest.gold_copper / 10000:.2f}g")
        return "; ".join(bits) if bits else "On-level hub progression"
