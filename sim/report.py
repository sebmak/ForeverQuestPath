"""Write structured simulation metrics JSON and comparative markdown route reports."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .models import Dataset, ProfileConfig, RouteStep, SimulationMetrics


def _summary_dict(m: SimulationMetrics) -> Dict[str, Any]:
    return {
        "strategyName": m.strategy_name,
        "profileId": m.profile_id,
        "faction": m.faction,
        "race": m.race,
        "class": m.char_class,
        "startZoneId": m.start_zone_id,
        "startLevel": m.start_level,
        "endLevel": m.end_level,
        "endXpPct": m.end_xp_pct,
        "totalSteps": m.total_steps,
        "questsCompleted": m.quests_completed,
        "dungeonQuestsCompleted": m.dungeon_quests_completed,
        "dungeonsScheduled": len(m.dungeons_scheduled),
        "dungeonIds": list(m.dungeons_scheduled),
        "totalTimeHours": m.total_time_hours,
        "travelTimeHours": m.travel_time_hours,
        "travelSharePct": m.travel_share_pct,
        "avgXpPerHour": round(m.avg_xp_per_hour, 1),
        "totalXpEarned": m.total_xp_earned,
        "totalGoldCopper": m.total_gold_copper,
        "bagsEarned": m.bags_earned,
        "maxBagSlotsEarned": m.max_bag_slots_earned,
        "rogueUpgrades": m.rogue_upgrades,
        "classUpgrades": m.class_upgrades,
        "lwQuestsCompleted": m.lw_quests_completed,
        "skinningQuestsCompleted": m.skinning_quests_completed,
        "beastQuestsCompleted": m.beast_quests_completed,
        "totalSkinningScore": round(m.total_skinning_score, 1),
        "prereqViolations": m.prereq_violations,
        "minLevelViolations": m.min_level_violations,
    }


def _fmt_gold(copper: int) -> str:
    g = copper // 10000
    s = (copper % 10000) // 100
    c = copper % 100
    return f"{g}g {s:02d}s {c:02d}c"


# Rules: Formats and writes routes/sim_metrics.json and routes/simulation_report.md from verified
# multi-race/class/faction SimulationMetrics and RouteStep outputs without extrapolating numbers.
class ReportWriter:
    dataset: Dataset

    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset

    def write_outputs(
        self,
        routes_dir: str | Path,
        strategy_benchmarks: Sequence[SimulationMetrics],
        profile_results: Mapping[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]],
        level24_benchmarks: Optional[Tuple[Sequence[SimulationMetrics], Mapping[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]]]] = None,
        archetype_metrics: Optional[Sequence[Tuple[str, SimulationMetrics, int, int]]] = None,
        starter_routes: Optional[Mapping[str, Tuple[str, str, int, str, str, List[RouteStep], SimulationMetrics]]] = None,
        horde_results: Optional[Mapping[str, Tuple[ProfileConfig, List[RouteStep], SimulationMetrics]]] = None,
        max_level_offset: int = 2,
    ) -> Tuple[Path, Path]:
        out_dir = Path(routes_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        metrics_path = out_dir / "sim_metrics.json"
        report_path = out_dir / "simulation_report.md"

        lead = strategy_benchmarks[-1] if strategy_benchmarks else None
        start_lvl = lead.start_level if lead else 1
        start_zone_id = lead.start_zone_id if lead else 1
        start_zone_name = (
            self.dataset.zones[start_zone_id].name
            if start_zone_id in self.dataset.zones
            else f"Zone {start_zone_id}"
        )
        start_faction = lead.faction if lead else "Alliance"
        start_race = lead.race if lead else "Gnome"
        start_class = lead.char_class if lead else "ROGUE"

        leveling_quests = [q for q in self.dataset.quests.values() if q.is_leveling_quest]
        alliance_count = sum(1 for q in leveling_quests if q.faction_mask in (0, 1, 3))
        horde_count = sum(1 for q in leveling_quests if q.faction_mask in (0, 2, 3))

        payload: Dict[str, Any] = {
            "meta": {
                "maxLevelOffset": int(max_level_offset),
                "startCharacter": {
                    "faction": start_faction,
                    "race": start_race,
                    "class": start_class,
                    "startLevel": start_lvl,
                    "startZoneId": start_zone_id,
                    "startZone": start_zone_name,
                },
                "datasetStats": {
                    "totalQuestsLoaded": len(self.dataset.quests),
                    "levelingQuestsLoaded": len(leveling_quests),
                    "allianceEligibleQuests": alliance_count,
                    "hordeEligibleQuests": horde_count,
                    "eligibleAllianceGnomeRogueQuests": sum(1 for q in self.dataset.quests.values() if q.eligible_alliance_gnome_rogue),
                    "totalDungeons": len(self.dataset.dungeons),
                    "totalZoneNodes": len(self.dataset.zones),
                    "totalTravelEdges": len(self.dataset.edges),
                    "totalRaces": len(self.dataset.races),
                    "totalClasses": len(self.dataset.classes),
                    "missingPrereqsDropped": len(self.dataset.missing_prereqs),
                    "cyclesRemoved": len(self.dataset.broken_cycles_removed),
                },
            },
            "baseline": {m.strategy_name: _summary_dict(m) for m in strategy_benchmarks},
            "profiles": {pkey: _summary_dict(m) for pkey, (_, _, m) in profile_results.items()},
            "strategyComparison": [asdict(m) for m in strategy_benchmarks],
            "profileComparison": {
                pkey: {
                    "label": cfg.label,
                    "weights": dict(cfg.weights),
                    "metrics": asdict(m),
                }
                for pkey, (cfg, _, m) in profile_results.items()
            },
        }

        if level24_benchmarks is not None:
            l24_strats, l24_profs = level24_benchmarks
            payload["level24Benchmarks"] = {
                "strategyComparison": [asdict(m) for m in l24_strats],
                "profileComparison": {
                    pkey: {
                        "label": cfg.label,
                        "weights": dict(cfg.weights),
                        "metrics": asdict(m),
                    }
                    for pkey, (cfg, _, m) in l24_profs.items()
                },
            }

        if archetype_metrics:
            payload["archetypes"] = {
                label: {
                    **_summary_dict(m),
                    "redViolations": red_v,
                    "eligibilityViolations": elig_v,
                }
                for label, m, red_v, elig_v in archetype_metrics
            }

        if starter_routes:
            payload["starterRoutes"] = {
                str(rkey): {
                    "label": label,
                    "faction": faction,
                    "zoneId": zid,
                    "representativeRace": rep_race,
                    "representativeClass": rep_class,
                    **_summary_dict(m),
                }
                for rkey, (label, faction, zid, rep_race, rep_class, _, m) in starter_routes.items()
            }

        if horde_results:
            payload["hordeRoutes"] = {
                pkey: {
                    "label": cfg.label,
                    "metrics": asdict(m),
                }
                for pkey, (cfg, _, m) in horde_results.items()
            }

        metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        md_lines: List[str] = [
            "# ForeverQuestPath Simulation & Route Optimization Report",
            "",
            f"**Primary Target Character:** Level {start_lvl} {start_faction} {start_race} {start_class.title()} starting in **{start_zone_name}** (progression through Level 60, max quest level offset `+{max_level_offset}`)",
            f"**Dataset Loaded:** {len(self.dataset.quests)} total quests ({len(leveling_quests)} leveling: "
            f"{alliance_count} Alliance-eligible, {horde_count} Horde-eligible), "
            f"{len(self.dataset.dungeons)} dungeons, {len(self.dataset.zones)} zone hubs, "
            f"{len(self.dataset.edges)} travel edges, {len(self.dataset.races)} races, {len(self.dataset.classes)} classes.",
            "",
            "## 1. Comparative Algorithm Simulation (`balanced` profile)",
            "",
            "| Strategy | End Level | Total Time (h) | Travel Share (%) | Avg XP / hr | Quests Done | Dungeons Run | Gold Earned | Bags | Class Upgrades | Prereq / Level Violations |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for m in strategy_benchmarks:
            md_lines.append(
                f"| `{m.strategy_name}` | {m.end_level} ({m.end_xp_pct:.0f}%) | {m.total_time_hours:.2f} | "
                f"{m.travel_share_pct:.1f}% | {m.avg_xp_per_hour:,.0f} | {m.quests_completed} | "
                f"{len(m.dungeons_scheduled)} | {_fmt_gold(m.total_gold_copper)} | {m.bags_earned} | "
                f"{m.class_upgrades} | {m.prereq_violations} / {m.min_level_violations} |"
            )

        md_lines.extend(
            [
                "",
                "## 2. Optimization Profile Comparison (`beam_optimizer`)",
                "",
                "| Profile | Weights (XP / Travel / Dungeon / Reward / Skin) | End Level | Time (h) | Travel (%) | XP / hr | Dungeons Scheduled | Gold | Bags (Max Slots) | Class Upgrades | LW / Beast Quests |",
                "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|",
            ]
        )
        for pkey, (cfg, _, m) in profile_results.items():
            w = cfg.weights
            dg_str = ", ".join(m.dungeons_scheduled) if m.dungeons_scheduled else "None"
            md_lines.append(
                f"| `{pkey}` ({cfg.label}) | {w['xp']}/{w['travel']}/{w['dungeon']}/{w['reward']}/{w.get('skinning', 0)} | "
                f"{m.end_level} | {m.total_time_hours:.2f} | {m.travel_share_pct:.1f}% | "
                f"{m.avg_xp_per_hour:,.0f} | {dg_str} | {_fmt_gold(m.total_gold_copper)} | "
                f"{m.bags_earned} ({m.max_bag_slots_earned}s) | {m.class_upgrades} | "
                f"{m.lw_quests_completed + m.skinning_quests_completed} / {m.beast_quests_completed} |"
            )

        if archetype_metrics:
            md_lines.extend(
                [
                    "",
                    "## 3. Multi-Race & Multi-Class Simulation Matrix (`balanced` profile)",
                    "",
                    "| Character | Start -> End | Time (h) | Travel (%) | XP / hr | Quests | Dungeons | Class Upgrades | Violations (Prereq / MinLv / Red / Elig) |",
                    "|---|---|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for label, m, red_v, elig_v in archetype_metrics:
                start_z = self.dataset.zones[m.start_zone_id].name if m.start_zone_id in self.dataset.zones else str(m.start_zone_id)
                md_lines.append(
                    f"| `{label}` ({m.faction} {m.race} {m.char_class}) | Lv {m.start_level} {start_z} -> {m.end_level} | "
                    f"{m.total_time_hours:.2f} | {m.travel_share_pct:.1f}% | {m.avg_xp_per_hour:,.0f} | "
                    f"{m.quests_completed} | {len(m.dungeons_scheduled)} | {m.class_upgrades} | "
                    f"{m.prereq_violations} / {m.min_level_violations} / {red_v} / {elig_v} |"
                )

        if starter_routes or horde_results:
            md_lines.extend(["", "## 4. Precomputed Starter Zones (`1->24`) & Horde Spine (`20->60`)", ""])
            if starter_routes:
                md_lines.extend(
                    [
                        "### Starting Zones (Level 1-24)",
                        "",
                        "| Route Key | Zone | Faction | Representative | End Lv | Time (h) | Steps | Quests | Violations (P/M) |",
                        "|---|---|---|---|---:|---:|---:|---:|---:|",
                    ]
                )
                for rkey, (label, faction, zid, rep_race, rep_class, _, m) in starter_routes.items():
                    z_name = self.dataset.zones[zid].name if zid in self.dataset.zones else str(zid)
                    md_lines.append(
                        f"| `{rkey}` | {z_name} ({zid}) | {faction} | {rep_race} {rep_class} | "
                        f"{m.end_level} | {m.total_time_hours:.2f} | {m.total_steps} | {m.quests_completed} | "
                        f"{m.prereq_violations}/{m.min_level_violations} |"
                    )
                md_lines.append("")
            if horde_results:
                md_lines.extend(
                    [
                        "### Horde Spine Profiles (`20->60`)",
                        "",
                        "| Profile | End Lv | Time (h) | Travel (%) | XP / hr | Dungeons | Class Upgrades | Violations (P/M) |",
                        "|---|---:|---:|---:|---:|---:|---:|---:|",
                    ]
                )
                for pkey, (cfg, _, m) in horde_results.items():
                    md_lines.append(
                        f"| `{pkey}` ({cfg.label}) | {m.end_level} | {m.total_time_hours:.2f} | "
                        f"{m.travel_share_pct:.1f}% | {m.avg_xp_per_hour:,.0f} | {len(m.dungeons_scheduled)} | "
                        f"{m.class_upgrades} | {m.prereq_violations}/{m.min_level_violations} |"
                    )
                md_lines.append("")

        if level24_benchmarks is not None:
            l24_strats, l24_profs = level24_benchmarks
            md_lines.extend(
                [
                    "## 5. Level 24 Wetlands Regression Benchmark (`Alliance Gnome Rogue`, `24->60`)",
                    "",
                    "| Strategy / Profile | End Lv | Time (h) | Travel (%) | XP / hr | Dungeons | LW / Beast | Violations (P/M) |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for m in l24_strats:
                if m.strategy_name == "beam_optimizer":
                    continue
                md_lines.append(
                    f"| `{m.strategy_name}` | {m.end_level} | {m.total_time_hours:.2f} | {m.travel_share_pct:.1f}% | "
                    f"{m.avg_xp_per_hour:,.0f} | {len(m.dungeons_scheduled)} | "
                    f"{m.lw_quests_completed + m.skinning_quests_completed}/{m.beast_quests_completed} | "
                    f"{m.prereq_violations}/{m.min_level_violations} |"
                )
            for pkey, (_, _, m) in l24_profs.items():
                md_lines.append(
                    f"| `beam:{pkey}` | {m.end_level} | {m.total_time_hours:.2f} | {m.travel_share_pct:.1f}% | "
                    f"{m.avg_xp_per_hour:,.0f} | {len(m.dungeons_scheduled)} | "
                    f"{m.lw_quests_completed + m.skinning_quests_completed}/{m.beast_quests_completed} | "
                    f"{m.prereq_violations}/{m.min_level_violations} |"
                )
            md_lines.append("")

        # Level progression splits
        if "balanced" in profile_results:
            _, bal_steps, bal_m = profile_results["balanced"]
            md_lines.extend(
                [
                    "## 6. Leveling Milestones (`balanced` route)",
                    "",
                    "| Milestone Level | Elapsed Hours |",
                    "|---:|---:|",
                ]
            )
            for lvl in sorted(bal_m.level_timestamps_hours):
                if lvl in (5, 10, 15, 20, 24, 28, 30, 35, 40, 45, 50, 55, 60) or lvl == max(bal_m.level_timestamps_hours):
                    md_lines.append(f"| Level {lvl} | {bal_m.level_timestamps_hours[lvl]:.2f}h |")

            md_lines.extend(
                [
                    "",
                    f"## 7. Opening Steps from Level {start_lvl} {start_zone_name} (`balanced` route preview, first 40 steps)",
                    "",
                    "| # | Action | Zone | Coords | Step | Lv | XP | Gold | Why |",
                    "|---:|---|---|---|---:|---:|---:|---:|---|",
                ]
            )
            for st in bal_steps[:40]:
                gold_str = _fmt_gold(st.gold_copper) if st.gold_copper > 0 else "-"
                md_lines.append(
                    f"| {st.index} | `{st.action}` | {st.zone_name} | ({st.x:.1f}, {st.y:.1f}) | "
                    f"{st.title} | {st.expected_level} | {st.xp_gain:,} | {gold_str} | {st.why} |"
                )

            # Dungeon schedule breakdown across all profiles
            md_lines.extend(["", "## 8. Scheduled Dungeon Windows (`dungeon_focus` vs `balanced`)", ""])
            for pkey in ("balanced", "dungeon_focus"):
                if pkey not in profile_results:
                    continue
                _, psteps, _ = profile_results[pkey]
                dg_steps = [s for s in psteps if s.action == "DUNGEON_RUN"]
                md_lines.append(f"### Profile `{pkey}`")
                if not dg_steps:
                    md_lines.append("- No dungeons scheduled.")
                else:
                    for s in dg_steps:
                        md_lines.append(
                            f"- **Step {s.index} — {s.title}** at Level {s.expected_level} "
                            f"in {s.zone_name} ({s.x:.1f}, {s.y:.1f}): {len(s.quest_ids)} quest(s), {s.why}"
                        )
                md_lines.append("")

        report_path.write_text("\n".join(md_lines), encoding="utf-8")
        return metrics_path, report_path
