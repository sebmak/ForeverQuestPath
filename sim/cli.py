"""CLI runner for ForeverQuestPath simulations, route optimization, and Lua export."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .dag import QuestDAG
from .dungeon import DungeonOptimizer
from .loader import DatasetLoader
from .lua_exporter import LuaExporter
from .models import (
    CharacterState,
    Dataset,
    ProfileConfig,
    RouteStep,
    SimulationMetrics,
    canonical_class_key,
    canonical_race_key,
)
from .optimizer import BeamAnnealingOptimizer, HubClusterLookaheadStrategy, NaiveGreedyStrategy
from .report import ReportWriter
from .scoring import MultiObjectiveScorer, PROFILES
from .simulator import RouteSimulator
from .travel import TravelModel
from .xp import MAX_LEVEL, XPModel


PREFERRED_STARTER_REPS: Dict[Tuple[int, str], Tuple[str, str]] = {
    (1, "Alliance"): ("Gnome", "ROGUE"),
    (12, "Alliance"): ("Human", "PALADIN"),
    (141, "Alliance"): ("NightElf", "HUNTER"),
    (14, "Horde"): ("Orc", "WARRIOR"),
    (215, "Horde"): ("Tauren", "DRUID"),
    (85, "Horde"): ("Scourge", "MAGE"),
    (16593, "Alliance"): ("HighOrderSkyborne", "MAGE"),
    (16593, "Horde"): ("WindshaperSkyborne", "SHAMAN"),
}


def _pick_representative_class(dataset: Dataset, race_key: str, fallback: str = "WARRIOR") -> str:
    r_info = dataset.races.get(race_key)
    if r_info and r_info.allowed_classes:
        for pref in ("ROGUE", "PALADIN", "HUNTER", "WARRIOR", "MAGE", "DRUID", "SHAMAN"):
            if pref in r_info.allowed_classes:
                return pref
        return r_info.allowed_classes[0]
    return fallback


def _run_benchmark_and_profiles(
    dataset: Dataset,
    dag: QuestDAG,
    xp_model: XPModel,
    travel_model: TravelModel,
    dungeon_opt: DungeonOptimizer,
    scorer: MultiObjectiveScorer,
    simulator: RouteSimulator,
    initial_state: CharacterState,
    target_level: int,
) -> Tuple[List[SimulationMetrics], Dict[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]]]:
    naive = NaiveGreedyStrategy(dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator)
    lookahead = HubClusterLookaheadStrategy(dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator)
    beam = BeamAnnealingOptimizer(dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator)

    balanced_cfg = PROFILES["balanced"]
    _, _, m_naive = naive.build_route(initial_state, balanced_cfg, target_level=target_level)
    _, _, m_lookahead = lookahead.build_route(initial_state, balanced_cfg, target_level=target_level)

    profile_results: Dict[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]] = {}
    m_beam_balanced: Optional[SimulationMetrics] = None
    for pkey, cfg in PROFILES.items():
        _, steps, metrics = beam.build_route(initial_state, cfg, target_level=target_level)
        profile_results[pkey] = (cfg, steps, metrics)
        if pkey == "balanced":
            m_beam_balanced = metrics

    benchmarks = [m_naive, m_lookahead]
    if m_beam_balanced is not None:
        benchmarks.append(m_beam_balanced)
    return benchmarks, profile_results


def _build_starter_routes(
    dataset: Dataset,
    dag: QuestDAG,
    xp_model: XPModel,
    travel_model: TravelModel,
    dungeon_opt: DungeonOptimizer,
    scorer: MultiObjectiveScorer,
    simulator: RouteSimulator,
    max_level_offset: int = 2,
) -> Dict[str, Tuple[str, str, int, str, str, List[RouteStep], SimulationMetrics]]:
    beam = BeamAnnealingOptimizer(
        dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator,
        beam_width=2, branch_factor=2, sa_iterations=4,
    )
    balanced_cfg = PROFILES["balanced"]
    by_zone_faction: Dict[Tuple[int, str], List[str]] = {}
    for rkey, rinfo in sorted(dataset.races.items()):
        by_zone_faction.setdefault((rinfo.start_zone_id, rinfo.faction), []).append(rkey)

    zones_with_multiple_factions = {
        zid
        for zid in {z for z, _ in by_zone_faction}
        if len({f for z, f in by_zone_faction if z == zid}) > 1
    }

    starter_routes: Dict[str, Tuple[str, str, int, str, str, List[RouteStep], SimulationMetrics]] = {}
    for (zid, faction), race_keys in sorted(by_zone_faction.items()):
        if zid not in dataset.zones:
            continue
        pref_race, pref_cls = PREFERRED_STARTER_REPS.get((zid, faction), (race_keys[0], "WARRIOR"))
        rep_race = pref_race if pref_race in race_keys else race_keys[0]
        r_info = dataset.races.get(rep_race)
        rep_cls = pref_cls if (r_info and (not r_info.allowed_classes or pref_cls in r_info.allowed_classes)) else _pick_representative_class(dataset, rep_race)

        st = CharacterState.for_character(
            dataset=dataset,
            faction=faction,
            race=rep_race,
            char_class=rep_cls,
            level=1,
            zone_id=zid,
            max_level_offset=max_level_offset,
        )
        # Skip starter zones with zero Level 1-5 quests
        if not any(q.min_level <= 3 and dag.is_eligible_for_character(q, st) for q in dataset.quests.values()):
            continue

        _, steps, metrics = beam.build_route(st, balanced_cfg, target_level=24)
        z_name = dataset.zones[zid].name
        route_key = f"{zid}_{faction}" if zid in zones_with_multiple_factions else str(zid)
        label = f"{z_name} ({faction} 1-24)"
        starter_routes[route_key] = (label, faction, zid, rep_race, rep_cls, steps, metrics)
    return starter_routes


def _build_horde_routes(
    dataset: Dataset,
    dag: QuestDAG,
    xp_model: XPModel,
    travel_model: TravelModel,
    dungeon_opt: DungeonOptimizer,
    scorer: MultiObjectiveScorer,
    simulator: RouteSimulator,
    max_level_offset: int = 2,
) -> Dict[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]]:
    horde_start_zone = 17 if 17 in dataset.zones else (1637 if 1637 in dataset.zones else 14)
    st = CharacterState.for_character(
        dataset=dataset,
        faction="Horde",
        race="Orc",
        char_class="WARRIOR",
        level=20,
        zone_id=horde_start_zone,
        max_level_offset=max_level_offset,
    )
    if not any(q.faction_mask in (0, 2, 3) and q.min_level >= 18 and q.min_level <= 24 for q in dataset.quests.values()):
        return {}

    beam = BeamAnnealingOptimizer(dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator)
    horde_routes: Dict[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]] = {}
    for pkey, cfg in PROFILES.items():
        _, steps, metrics = beam.build_route(st, cfg, target_level=MAX_LEVEL)
        horde_routes[pkey] = (cfg, steps, metrics)
    return horde_routes


def _run_archetype_matrix(
    dataset: Dataset,
    dag: QuestDAG,
    xp_model: XPModel,
    travel_model: TravelModel,
    dungeon_opt: DungeonOptimizer,
    scorer: MultiObjectiveScorer,
    simulator: RouteSimulator,
    primary_balanced: SimulationMetrics,
    primary_steps: List[RouteStep],
    primary_state_template: CharacterState,
    l24_balanced: Optional[Tuple[List[RouteStep], SimulationMetrics]] = None,
) -> List[Tuple[str, SimulationMetrics, int, int]]:
    results: List[Tuple[str, SimulationMetrics, int, int]] = []
    red_v, elig_v = simulator.validate_character_steps(primary_steps, primary_state_template)[2:]
    results.append((
        f"{primary_state_template.faction}_{primary_state_template.race}_{primary_state_template.char_class}_{primary_state_template.level}",
        primary_balanced,
        red_v,
        elig_v,
    ))

    if l24_balanced is not None:
        l24_steps, l24_m = l24_balanced
        l24_tmpl = CharacterState.for_character(
            dataset,
            "Alliance",
            "Gnome",
            "ROGUE",
            level=24,
            zone_id=11,
            max_level_offset=primary_state_template.max_level_offset,
        )
        rv24, ev24 = simulator.validate_character_steps(l24_steps, l24_tmpl)[2:]
        results.append(("Alliance_Gnome_ROGUE_24", l24_m, rv24, ev24))

    beam = BeamAnnealingOptimizer(
        dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator,
        beam_width=2, branch_factor=2, sa_iterations=4,
    )
    balanced_cfg = PROFILES["balanced"]

    specs: List[Tuple[str, str, str, int]] = [
        ("Alliance", "Human", "PALADIN", 1),
        ("Alliance", "NightElf", "HUNTER", 1),
        ("Horde", "Orc", "WARRIOR", 1),
        ("Horde", "Scourge", "MAGE", 1),
    ]
    # Add eligible custom Forever race(s) (race_id > 8) when their starter zone has Level 1 quests
    for rkey, rinfo in sorted(dataset.races.items()):
        if rinfo.race_id > 8 and rinfo.start_zone_id in dataset.zones:
            rep_cls = _pick_representative_class(dataset, rkey)
            specs.append((rinfo.faction, rkey, rep_cls, 1))

    for faction, race_key, cls_key, start_lvl in specs:
        r_key = canonical_race_key(race_key, dataset.races)
        c_key = canonical_class_key(cls_key, dataset.classes)
        st = CharacterState.for_character(
            dataset,
            faction=faction,
            race=r_key,
            char_class=c_key,
            level=start_lvl,
            max_level_offset=primary_state_template.max_level_offset,
        )
        if st.zone_id not in dataset.zones:
            continue
        if not any(q.min_level <= start_lvl + 2 and dag.is_eligible_for_character(q, st) for q in dataset.quests.values()):
            continue
        _, steps, metrics = beam.build_route(st, balanced_cfg, target_level=MAX_LEVEL)
        _, _, rv, ev = simulator.validate_character_steps(steps, st)
        results.append((f"{faction}_{r_key}_{c_key}_{start_lvl}", metrics, rv, ev))
    return results


def run_pipeline(
    data_dir: Path,
    routes_dir: Path,
    lua_out: Path,
    start_level: int = 1,
    start_zone_id: Optional[int] = None,
    target_level: int = MAX_LEVEL,
    faction: Optional[str] = None,
    race: str = "Gnome",
    char_class: str = "ROGUE",
    full_matrix: bool = True,
    max_level_offset: int = 2,
    nav_lua_out: Optional[Path] = None,
) -> Tuple[List[SimulationMetrics], Dict[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]]]:
    dataset = DatasetLoader(data_dir).load()
    xp_model = XPModel(dataset.xp_curve)
    travel_model = TravelModel(dataset)
    dag = QuestDAG(dataset)
    dungeon_opt = DungeonOptimizer(dataset, dag, xp_model, travel_model)
    scorer = MultiObjectiveScorer(dataset, dag, xp_model, travel_model, dungeon_opt)
    simulator = RouteSimulator(dataset, dag, xp_model, travel_model, dungeon_opt, scorer)

    r_key = canonical_race_key(race, dataset.races)
    c_key = canonical_class_key(char_class, dataset.classes)
    r_info = dataset.races.get(r_key)
    resolved_faction = faction or (r_info.faction if r_info else "Alliance")
    resolved_zone = start_zone_id if start_zone_id is not None else (r_info.start_zone_id if r_info else 1)

    initial_state = CharacterState.for_character(
        dataset=dataset,
        faction=resolved_faction,
        race=r_key,
        char_class=c_key,
        level=start_level,
        zone_id=resolved_zone,
        max_level_offset=max_level_offset,
    )

    benchmarks, profile_results = _run_benchmark_and_profiles(
        dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator,
        initial_state, target_level=target_level,
    )

    level24_benchmarks = None
    archetype_metrics = None
    starter_routes = None
    horde_routes = None
    if full_matrix:
        if start_level < 24 and 11 in dataset.zones:
            l24_state = CharacterState.for_character(
                dataset=dataset, faction="Alliance", race="Gnome", char_class="ROGUE",
                level=24, zone_id=11, max_level_offset=max_level_offset,
            )
            l24_b, l24_p = _run_benchmark_and_profiles(
                dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator,
                l24_state, target_level=target_level,
            )
            level24_benchmarks = (l24_b, l24_p)
            l24_bal = (l24_p["balanced"][1], l24_p["balanced"][2])
        else:
            l24_bal = None

        starter_routes = _build_starter_routes(
            dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator,
            max_level_offset=max_level_offset,
        )
        horde_routes = _build_horde_routes(
            dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator,
            max_level_offset=max_level_offset,
        )
        bal_steps = profile_results["balanced"][1]
        bal_metrics = profile_results["balanced"][2]
        archetype_metrics = _run_archetype_matrix(
            dataset, dag, xp_model, travel_model, dungeon_opt, scorer, simulator,
            bal_metrics, bal_steps, initial_state, l24_balanced=l24_bal,
        )

    ReportWriter(dataset).write_outputs(
        routes_dir,
        benchmarks,
        profile_results,
        level24_benchmarks=level24_benchmarks,
        archetype_metrics=archetype_metrics,
        starter_routes=starter_routes,
        horde_results=horde_routes,
        max_level_offset=max_level_offset,
    )
    exporter = LuaExporter(dataset, travel_model, scorer)
    exporter.write_lua(
        lua_out,
        profile_results,
        start_level=start_level,
        start_zone_id=resolved_zone,
        starter_routes=starter_routes,
        horde_routes=horde_routes,
        target_race=r_key,
        target_class=c_key,
        target_faction=resolved_faction,
        default_max_level_offset=max_level_offset,
    )
    resolved_nav_out = nav_lua_out if nav_lua_out is not None else Path(lua_out).parent / "NavData.lua"
    exporter.write_nav_lua(resolved_nav_out)
    return benchmarks, profile_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ForeverQuestPath route optimization & Lua generation.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--routes-dir", type=Path, default=Path("routes"))
    parser.add_argument("--lua-out", type=Path, default=Path("addon/Data/GeneratedData.lua"))
    parser.add_argument("--nav-lua-out", type=Path, default=None)
    parser.add_argument("--faction", type=str, default=None)
    parser.add_argument("--race", type=str, default="Gnome")
    parser.add_argument("--class", dest="char_class", type=str, default="ROGUE")
    parser.add_argument("--start-level", type=int, default=1)
    parser.add_argument("--start-zone-id", type=int, default=None)
    parser.add_argument("--target-level", type=int, default=MAX_LEVEL)
    parser.add_argument("--max-level-offset", type=int, default=2)
    parser.add_argument("--skip-matrix", action="store_true")
    args = parser.parse_args()

    benchmarks, profile_results = run_pipeline(
        data_dir=args.data_dir,
        routes_dir=args.routes_dir,
        lua_out=args.lua_out,
        start_level=args.start_level,
        start_zone_id=args.start_zone_id,
        target_level=args.target_level,
        faction=args.faction,
        race=args.race,
        char_class=args.char_class,
        full_matrix=not args.skip_matrix,
        max_level_offset=args.max_level_offset,
        nav_lua_out=args.nav_lua_out,
    )
    for m in benchmarks:
        print(
            f"[Benchmark] {m.strategy_name} ({m.faction} {m.race} {m.char_class}): Lv {m.end_level} in {m.total_time_hours:.2f}h, "
            f"XP/hr={m.avg_xp_per_hour:,.0f}, travel={m.travel_share_pct:.1f}%, "
            f"dungeons={len(m.dungeons_scheduled)}, violations={m.prereq_violations}/{m.min_level_violations}"
        )
    for pkey, (_, steps, m) in profile_results.items():
        print(
            f"[Profile] {pkey}: {len(steps)} steps, Lv {m.end_level} in {m.total_time_hours:.2f}h, "
            f"XP/hr={m.avg_xp_per_hour:,.0f}, dungeons={m.dungeons_scheduled}, "
            f"bags={m.bags_earned}, gold={m.total_gold_copper}c, {m.char_class.lower()}={m.class_upgrades}"
        )


if __name__ == "__main__":
    main()
