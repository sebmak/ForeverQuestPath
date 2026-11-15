"""Travel-time graph routing, in-zone movement estimation, Hearthstone, and sub-zone clustering."""

from __future__ import annotations

import heapq
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import CharacterState, Coord, Dataset, Dungeon, Quest, TravelEdge, ZoneNode
from .nav import NavGraph

# Approximate zone widths in yards for converting 0..100 coordinates into realistic run distances.
# Defaults to 2800 yards for outdoor zones and 950 yards for capital cities.
ZONE_YARD_SCALES: Dict[int, float] = {
    1: 3600.0,     # Dun Morogh
    3: 2600.0,     # Badlands
    4: 2600.0,     # Blasted Lands
    8: 2300.0,     # Swamp of Sorrows
    10: 2700.0,    # Duskwood
    11: 3200.0,    # Wetlands
    12: 2800.0,    # Elwynn Forest
    14: 3400.0,    # Durotar
    15: 3200.0,    # Dustwallow Marsh
    16: 3500.0,    # Azshara
    17: 6000.0,    # The Barrens
    28: 2800.0,    # Western Plaguelands
    33: 4600.0,    # Stranglethorn Vale
    36: 2500.0,    # Alterac Mountains
    38: 2800.0,    # Loch Modan
    40: 3000.0,    # Westfall
    41: 1800.0,    # Deadwind Pass
    44: 2600.0,    # Redridge Mountains
    45: 3000.0,    # Arathi Highlands
    46: 2700.0,    # Burning Steppes
    47: 3000.0,    # The Hinterlands
    51: 2400.0,    # Searing Gorge
    85: 3000.0,    # Tirisfal Glades
    130: 3000.0,   # Silverpine Forest
    139: 3200.0,   # Eastern Plaguelands
    141: 3400.0,   # Teldrassil
    148: 4000.0,   # Darkshore
    215: 3500.0,   # Mulgore
    267: 2900.0,   # Hillsbrad Foothills
    331: 3800.0,   # Ashenvale
    357: 4200.0,   # Feralas
    361: 3600.0,   # Felwood
    400: 3600.0,   # Thousand Needles
    405: 3200.0,   # Desolace
    406: 3400.0,   # Stonetalon Mountains
    440: 4200.0,   # Tanaris
    490: 3000.0,   # Un'Goro Crater
    493: 1800.0,   # Moonglade
    618: 4000.0,   # Winterspring
    1377: 3200.0,  # Silithus
    1497: 950.0,   # Undercity
    1519: 1100.0,  # Stormwind City
    1537: 900.0,   # Ironforge
    1637: 950.0,   # Orgrimmar
    1638: 900.0,   # Thunder Bluff
    1657: 1000.0,  # Darnassus
    16591: 2800.0, # Riverglades
    16593: 2000.0, # Zephras Isle
}

# Fallback instance zoneId -> outdoor entrance zoneId mapping.
INSTANCE_ENTRANCE_ZONES: Dict[int, int] = {
    209: 130,
    491: 17,
    717: 1519,
    718: 17,
    719: 331,
    721: 1,
    722: 17,
    796: 85,
    1176: 440,
    1337: 3,
    1477: 8,
    1581: 40,
    1583: 46,
    1584: 51,
    2017: 139,
    2057: 28,
    2100: 405,
    2437: 1637,
    2557: 357,
    16544: 36,
    16550: 33,
    16611: 1497,
    16732: 11,
    16842: 16591,
    16919: 1537,
}

UNMOUNTED_YARDS_PER_SEC = 7.5   # 7 yds/s base + Rogue Sprint uptime
MOUNTED_YARDS_PER_SEC = 11.4    # Level 40+ 60% mount + Sprint
HEARTH_COOLDOWN_SECONDS = 3600.0


# Rules: Travel plan result summarizing total seconds, mode breakdown, and human-readable path note.
class TravelPlan:
    total_seconds: float
    steps: List[Tuple[int, int, str, int, str]]
    note: str
    used_hearth: bool

    def __init__(
        self,
        total_seconds: float,
        steps: List[Tuple[int, int, str, int, str]],
        note: str,
        used_hearth: bool = False,
    ) -> None:
        self.total_seconds = total_seconds
        self.steps = steps
        self.note = note
        self.used_hearth = used_hearth


# Rules: Models WoW Forever travel connectivity across both Alliance and Horde (in-zone terrain/nav-grid run,
# faction-filtered flight paths, boats, zeppelins, Deeprun Tram, and Hearthstone), sub-zone clustering,
# and local crafting fallback for profession quests whose only objective coordinate is an out-of-zone trade-good vendor.
class TravelModel:
    dataset: Dataset
    nav: Optional[NavGraph]
    adj: Dict[int, List[TravelEdge]]
    adj_by_faction: Dict[str, Dict[int, List[TravelEdge]]]
    cluster_radius_pct: float
    _flight_bit: Dict[int, int]
    _path_cache: Dict[Tuple[str, int, int, bool, int, int], TravelPlan]
    _in_zone_cache: Dict[Tuple[int, float, float, float, float, bool], float]

    def __init__(self, dataset: Dataset, cluster_radius_pct: float = 16.0) -> None:
        self.dataset = dataset
        self.nav = dataset.nav_graph
        self.cluster_radius_pct = cluster_radius_pct
        self.adj_by_faction = {"Alliance": {}, "Horde": {}}
        flight_zones: set[int] = set()
        for edge in dataset.edges:
            if edge.mode == "flight":
                flight_zones.add(edge.to_zone_id)
            if edge.faction in ("Alliance", "Both"):
                self.adj_by_faction["Alliance"].setdefault(edge.from_zone_id, []).append(edge)
            if edge.faction in ("Horde", "Both"):
                self.adj_by_faction["Horde"].setdefault(edge.from_zone_id, []).append(edge)
        self.adj = self.adj_by_faction["Alliance"]
        self._flight_bit = {zid: (1 << idx) for idx, zid in enumerate(sorted(flight_zones))}
        self._path_cache = {}
        self._in_zone_cache = {}

    def resolve_zone_id(self, zone_id: int) -> int:
        if zone_id in self.dataset.zones:
            return zone_id
        if zone_id in self.dataset.dungeon_by_zone:
            return self.dataset.dungeon_by_zone[zone_id].entrance_zone_id
        return INSTANCE_ENTRANCE_ZONES.get(zone_id, zone_id)

    def zone_name(self, zone_id: int) -> str:
        node = self.dataset.zones.get(zone_id)
        if node:
            return node.name
        dungeon = self.dataset.dungeon_by_zone.get(zone_id)
        if dungeon:
            return dungeon.name
        return f"Zone {zone_id}"

    def hub_coord(self, zone_id: int) -> Coord:
        resolved = self.resolve_zone_id(zone_id)
        node = self.dataset.zones.get(resolved)
        if node:
            return node.hub_coords
        return Coord(zone_id=resolved, x=50.0, y=50.0, label="Hub")

    @staticmethod
    def run_speed(player_level: int) -> float:
        return MOUNTED_YARDS_PER_SEC if player_level >= 40 else UNMOUNTED_YARDS_PER_SEC

    def in_zone_run_seconds(
        self,
        zone_id: int,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        player_level: int = 24,
    ) -> float:
        resolved = self.resolve_zone_id(zone_id)
        if self.nav is not None and resolved in self.nav.zone_cells:
            mounted = player_level >= 40
            key = (resolved, round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1), mounted)
            cached = self._in_zone_cache.get(key)
            if cached is not None:
                return cached
            nav_sec = self.nav.in_zone_travel_seconds(resolved, x1, y1, x2, y2, player_level=player_level)
            if nav_sec is not None:
                if len(self._in_zone_cache) > 50000:
                    self._in_zone_cache.clear()
                self._in_zone_cache[key] = nav_sec
                return nav_sec
        scale = ZONE_YARD_SCALES.get(resolved, 2800.0)
        dx = (x2 - x1) / 100.0 * scale
        dy = (y2 - y1) / 100.0 * (scale * 0.72)  # WoW zone maps are ~3:2 aspect ratio
        # Terrain winding factor 1.18x over straight-line Euclidean distance
        yards = math.hypot(dx, dy) * 1.18
        return yards / self.run_speed(player_level)

    def quest_starter_coord(self, quest: Quest) -> Coord:
        if quest.starter_coords:
            return quest.starter_coords
        if quest.turnin_coords:
            return quest.turnin_coords
        if quest.objective_coords:
            return quest.objective_coords[0]
        return self.hub_coord(quest.zone_id)

    def quest_turnin_coord(self, quest: Quest) -> Coord:
        if quest.turnin_coords:
            return quest.turnin_coords
        if quest.starter_coords:
            return quest.starter_coords
        if quest.objective_coords:
            return quest.objective_coords[0]
        return self.hub_coord(quest.zone_id)

    def quest_objective_centroid(self, quest: Quest) -> Coord:
        if quest.objective_coords:
            pref_zone = quest.starter_coords.zone_id if quest.starter_coords else quest.zone_id
            same_zone = [c for c in quest.objective_coords if c.zone_id == pref_zone]
            if not same_zone and quest.zone_id > 0:
                same_zone = [c for c in quest.objective_coords if c.zone_id == quest.zone_id]
            if not same_zone:
                is_prof_craft = bool(
                    quest.is_leatherworking or quest.is_skinning or quest.required_skill_id in (165, 393)
                )
                if (
                    is_prof_craft
                    and quest.turnin_coords
                    and self.resolve_zone_id(quest.turnin_coords.zone_id) == self.resolve_zone_id(pref_zone)
                    and all("(vendor)" in (c.label or "") for c in quest.objective_coords)
                ):
                    return quest.turnin_coords
                z = quest.objective_coords[0].zone_id
                same_zone = [c for c in quest.objective_coords if c.zone_id == z]
            z = same_zone[0].zone_id
            avg_x = sum(c.x for c in same_zone) / len(same_zone)
            avg_y = sum(c.y for c in same_zone) / len(same_zone)
            return Coord(zone_id=z, x=round(avg_x, 1), y=round(avg_y, 1), label=same_zone[0].label)
        if quest.turnin_coords:
            return quest.turnin_coords
        return self.quest_starter_coord(quest)

    def dungeon_entrance_coord(self, dungeon: Dungeon) -> Coord:
        if dungeon.entrance_coords:
            return dungeon.entrance_coords
        return self.hub_coord(dungeon.entrance_zone_id)

    def zone_region(self, zone_id: int) -> Optional[int]:
        resolved = self.resolve_zone_id(zone_id)
        node = self.dataset.zones.get(resolved)
        return node.region if node else None

    def zone_continent(self, zone_id: int) -> Optional[str]:
        resolved = self.resolve_zone_id(zone_id)
        node = self.dataset.zones.get(resolved)
        return node.continent if node else None

    def quest_hop_zones(self, quest: Quest) -> Tuple[int, int, int, bool, bool]:
        """Return (starter_zone, objective_zone, turnin_zone, is_pure_local, is_same_region)."""
        sz = self.resolve_zone_id(self.quest_starter_coord(quest).zone_id)
        if quest.objective_coords and not quest.is_breadcrumb:
            oz = self.resolve_zone_id(self.quest_objective_centroid(quest).zone_id)
        else:
            oz = sz
        tz = self.resolve_zone_id(self.quest_turnin_coord(quest).zone_id)
        is_pure_local = (oz == sz and tz == sz)
        s_reg = self.zone_region(sz)
        is_same_region = s_reg is not None and self.zone_region(oz) == s_reg and self.zone_region(tz) == s_reg
        return sz, oz, tz, is_pure_local, is_same_region

    def shortest_zone_path(
        self,
        from_zone_id: int,
        to_zone_id: int,
        can_hearth: bool = False,
        hearth_zone_id: int = 11,
        discovered_flights: Optional[set[int]] = None,
        faction: str = "Alliance",
    ) -> TravelPlan:
        src = self.resolve_zone_id(from_zone_id)
        dst = self.resolve_zone_id(to_zone_id)
        if src == dst:
            return TravelPlan(0.0, [], f"Already in {self.zone_name(dst)}", False)

        if discovered_flights is None:
            fmask = -1
        else:
            fmask = 0
            for zid in discovered_flights:
                fmask |= self._flight_bit.get(zid, 0)
        cache_key = (faction, src, dst, can_hearth, hearth_zone_id if can_hearth else 0, fmask)
        cached = self._path_cache.get(cache_key)
        if cached is not None:
            return cached

        faction_adj = self.adj_by_faction.get(faction, self.adj)

        # Dijkstra over zone graph
        dist: Dict[int, float] = {src: 0.0}
        prev: Dict[int, Tuple[int, str, int, str, bool]] = {}
        pq: List[Tuple[float, int, bool]] = [(0.0, src, False)]

        if can_hearth and hearth_zone_id != src:
            hearth_sec = 12.0
            if hearth_sec < dist.get(hearth_zone_id, float("inf")):
                dist[hearth_zone_id] = hearth_sec
                prev[hearth_zone_id] = (src, "hearth", int(hearth_sec), f"Hearthstone to {self.zone_name(hearth_zone_id)}", True)
                heapq.heappush(pq, (hearth_sec, hearth_zone_id, True))

        while pq:
            cost, u, used_h = heapq.heappop(pq)
            if cost > dist.get(u, float("inf")):
                continue
            if u == dst:
                break
            for edge in faction_adj.get(u, []):
                # Flight paths require destination discovered or nearby hub; if undiscovered, allow slight penalty (first-time unlock)
                penalty = 0.0
                if edge.mode == "flight" and discovered_flights is not None and edge.to_zone_id not in discovered_flights:
                    penalty = 45.0
                ncost = cost + edge.seconds + penalty
                if ncost < dist.get(edge.to_zone_id, float("inf")):
                    dist[edge.to_zone_id] = ncost
                    note = edge.note or f"{edge.mode.title()} to {self.zone_name(edge.to_zone_id)}"
                    prev[edge.to_zone_id] = (u, edge.mode, int(edge.seconds + penalty), note, used_h)
                    heapq.heappush(pq, (ncost, edge.to_zone_id, used_h))

        if dst not in dist:
            # Fallback cross-region estimate when graph has an unlinked custom zone
            src_node = self.dataset.zones.get(src)
            dst_node = self.dataset.zones.get(dst)
            same_region = src_node and dst_node and src_node.region == dst_node.region
            est = 210.0 if same_region else 390.0
            note = f"Travel from {self.zone_name(src)} to {self.zone_name(dst)}"
            res = TravelPlan(est, [(src, dst, "run", int(est), note)], note, False)
            self._path_cache[cache_key] = res
            return res

        chain: List[Tuple[int, int, str, int, str]] = []
        cur = dst
        used_hearth_flag = False
        while cur != src and cur in prev:
            p_zone, mode, sec, note, uh = prev[cur]
            used_hearth_flag = used_hearth_flag or (mode == "hearth")
            chain.append((p_zone, cur, mode, sec, note))
            cur = p_zone
        chain.reverse()
        summary = " -> ".join(step[4] for step in chain)
        res = TravelPlan(dist[dst], chain, summary, used_hearth_flag)
        self._path_cache[cache_key] = res
        return res

    def estimate_coord_travel(
        self,
        state: CharacterState,
        target: Coord,
        allow_hearth: bool = True,
    ) -> Tuple[float, TravelPlan]:
        src_zone = self.resolve_zone_id(state.zone_id)
        dst_zone = self.resolve_zone_id(target.zone_id)
        can_hearth = allow_hearth and state.hearth_cooldown_remaining <= 0.0
        if self.nav is not None and src_zone in self.nav.zone_cells and dst_zone in self.nav.zone_cells:
            node_dists = self.nav.single_source_node_dists(
                src_zone,
                state.x,
                state.y,
                player_level=state.level,
                faction=state.faction,
                allow_hearth=can_hearth,
                hearth_zone_id=state.hearth_zone_id,
                discovered_flights=state.discovered_flights,
            )
            if node_dists is not None:
                sec = self.nav.estimate_from_node_dists(
                    node_dists,
                    src_zone,
                    state.x,
                    state.y,
                    dst_zone,
                    target.x,
                    target.y,
                    player_level=state.level,
                )
                if sec is not None:
                    note = f"Nav to {self.zone_name(dst_zone)} ({target.x:.1f}, {target.y:.1f})"
                    return sec, TravelPlan(sec, [], note, used_hearth=False)

        if src_zone == dst_zone:
            sec = self.in_zone_run_seconds(src_zone, state.x, state.y, target.x, target.y, state.level)
            plan = TravelPlan(sec, [], f"Run in {self.zone_name(dst_zone)} to ({target.x:.1f}, {target.y:.1f})")
            return sec, plan

        plan = self.shortest_zone_path(
            src_zone,
            dst_zone,
            can_hearth=can_hearth,
            hearth_zone_id=state.hearth_zone_id,
            discovered_flights=state.discovered_flights,
            faction=state.faction,
        )
        src_hub = self.hub_coord(src_zone)
        dst_hub = self.hub_coord(dst_zone)
        # If first hop is flight/boat/tram/hearth, include short run from current coords to source hub (unless hearth)
        first_mode = plan.steps[0][2] if plan.steps else "run"
        run_out = 0.0 if first_mode == "hearth" else self.in_zone_run_seconds(src_zone, state.x, state.y, src_hub.x, src_hub.y, state.level) * 0.55
        run_in = self.in_zone_run_seconds(dst_zone, dst_hub.x, dst_hub.y, target.x, target.y, state.level)
        total = run_out + plan.total_seconds + run_in
        return total, TravelPlan(total, plan.steps, plan.note, plan.used_hearth)

    def execute_travel_to(
        self,
        state: CharacterState,
        target: Coord,
        allow_hearth: bool = True,
    ) -> TravelPlan:
        src_zone = self.resolve_zone_id(state.zone_id)
        dst_zone = self.resolve_zone_id(target.zone_id)
        can_hearth = allow_hearth and state.hearth_cooldown_remaining <= 0.0
        plan: Optional[TravelPlan] = None
        if self.nav is not None and src_zone in self.nav.zone_cells and dst_zone in self.nav.zone_cells:
            nav_res = self.nav.shortest_path(
                src_zone,
                state.x,
                state.y,
                dst_zone,
                target.x,
                target.y,
                player_level=state.level,
                faction=state.faction,
                allow_hearth=can_hearth,
                hearth_zone_id=state.hearth_zone_id,
                discovered_flights=state.discovered_flights,
            )
            if nav_res is not None:
                plan = TravelPlan(nav_res.total_seconds, nav_res.steps, nav_res.note, nav_res.used_hearth)
        if plan is None:
            _, plan = self.estimate_coord_travel(state, target, allow_hearth=allow_hearth)
        seconds = plan.total_seconds
        state.elapsed_seconds += seconds
        state.travel_seconds += seconds
        if plan.used_hearth:
            state.hearth_cooldown_remaining = HEARTH_COOLDOWN_SECONDS
        else:
            state.hearth_cooldown_remaining = max(0.0, state.hearth_cooldown_remaining - seconds)
        state.discovered_flights.add(dst_zone)
        for _, z_to, _, _, _ in plan.steps:
            state.discovered_flights.add(z_to)
        state.zone_id = dst_zone
        state.x = target.x
        state.y = target.y
        return plan

    def estimate_cluster_internal_travel(
        self,
        cluster_quests: Sequence[Quest],
        player_level: int,
        discovered_flights: Optional[set[int]] = None,
        faction: str = "Alliance",
    ) -> Tuple[float, bool, bool]:
        """Estimate objective sweep + turn-in travel inside a cluster, including any zone transitions."""
        if not cluster_quests:
            return 0.0, True, True
        anchor = self.quest_starter_coord(cluster_quests[0])
        sz = self.resolve_zone_id(anchor.zone_id)
        s_reg = self.zone_region(sz)
        s_hub = self.hub_coord(sz)

        # Representative non-breadcrumb objective zone & turn-in zone for this cluster
        obj_zones = {
            self.resolve_zone_id(self.quest_objective_centroid(q).zone_id)
            for q in cluster_quests
            if q.objective_coords and not q.is_breadcrumb
        }
        turnin_zones = {self.resolve_zone_id(self.quest_turnin_coord(q).zone_id) for q in cluster_quests}

        pure_local = all(z == sz for z in obj_zones) and all(z == sz for z in turnin_zones)
        all_same_region = (
            s_reg is not None
            and all(self.zone_region(z) == s_reg for z in obj_zones)
            and all(self.zone_region(z) == s_reg for z in turnin_zones)
        )

        # In-zone TSP approximation over objective centroids
        cur_zone = sz
        cur_c = anchor
        internal_sec = 0.0

        for z in sorted(obj_zones):
            obj_qs = [
                q for q in cluster_quests
                if q.objective_coords and not q.is_breadcrumb
                and self.resolve_zone_id(self.quest_objective_centroid(q).zone_id) == z
            ]
            for q in obj_qs:
                oc = self.quest_objective_centroid(q)
                if z == cur_zone:
                    internal_sec += self.in_zone_run_seconds(cur_zone, cur_c.x, cur_c.y, oc.x, oc.y, player_level) * 0.72
                else:
                    plan = self.shortest_zone_path(
                        cur_zone,
                        z,
                        can_hearth=False,
                        discovered_flights=discovered_flights,
                        faction=faction,
                    )
                    z_hub = self.hub_coord(z)
                    c_hub = self.hub_coord(cur_zone)
                    internal_sec += (
                        self.in_zone_run_seconds(cur_zone, cur_c.x, cur_c.y, c_hub.x, c_hub.y, player_level) * 0.55
                        + plan.total_seconds
                        + self.in_zone_run_seconds(z, z_hub.x, z_hub.y, oc.x, oc.y, player_level)
                    )
                cur_zone = z
                cur_c = oc

        for tz in sorted(turnin_zones):
            t_qs = [q for q in cluster_quests if self.resolve_zone_id(self.quest_turnin_coord(q).zone_id) == tz]
            tc = self.quest_turnin_coord(t_qs[0])
            if tz == cur_zone:
                internal_sec += self.in_zone_run_seconds(cur_zone, cur_c.x, cur_c.y, tc.x, tc.y, player_level) * 0.78
            else:
                plan = self.shortest_zone_path(
                    cur_zone,
                    tz,
                    can_hearth=False,
                    discovered_flights=discovered_flights,
                    faction=faction,
                )
                t_hub = self.hub_coord(tz)
                c_hub = self.hub_coord(cur_zone)
                internal_sec += (
                    self.in_zone_run_seconds(cur_zone, cur_c.x, cur_c.y, c_hub.x, c_hub.y, player_level) * 0.55
                    + plan.total_seconds
                    + self.in_zone_run_seconds(tz, t_hub.x, t_hub.y, tc.x, tc.y, player_level)
                )
            cur_zone = tz
            cur_c = tc

        return internal_sec, pure_local, all_same_region

    def cluster_quests_by_subzone(self, quests: Sequence[Quest]) -> Dict[str, List[Quest]]:
        """Group quests by terrain-component & starter hub, isolating ridges and cross-zone deliveries."""
        # Bucket by (starter_zone, starter_component, obj_zone_if_xzone, turnin_zone_if_xzone)
        buckets: Dict[Tuple[int, int, int, int], List[Quest]] = {}
        for q in quests:
            sz, oz, tz, is_local, _ = self.quest_hop_zones(q)
            s_comp = 0
            if self.nav is not None:
                sc = self.quest_starter_coord(q)
                _, s_comp, _, _ = self.nav.coord_anchors(sz, sc.x, sc.y)
            key = (sz, s_comp, 0, 0) if is_local else (sz, s_comp, oz, tz)
            buckets.setdefault(key, []).append(q)

        clusters: Dict[str, List[Quest]] = {}
        comp_seq: Dict[Tuple[int, int, int], int] = {}
        for (sz, s_comp, oz, tz), b_quests in sorted(buckets.items()):
            sorted_q = sorted(b_quests, key=lambda q: (q.min_level, q.quest_level, q.quest_id))
            use_nav = self.nav is not None and sz in self.nav.zone_cells
            sec_limit = (
                self.nav.chord_seconds(sz, 50.0, 50.0, 50.0 + self.cluster_radius_pct, 50.0, player_level=24, factor=1.18)
                if use_nav
                else 0.0
            )
            # Track (rep_x, rep_y, sum_x, sum_y, items)
            hub_groups: List[Tuple[float, float, float, float, List[Quest]]] = []
            for q in sorted_q:
                sc = self.quest_starter_coord(q)
                best_idx = -1
                best_dist = float("inf")
                for idx, (rx, ry, sx_sum, sy_sum, items) in enumerate(hub_groups):
                    if use_nav:
                        d = self.in_zone_run_seconds(sz, sc.x, sc.y, rx, ry, player_level=24)
                        if d <= sec_limit and d < best_dist:
                            best_dist = d
                            best_idx = idx
                    else:
                        n = len(items)
                        hx, hy = sx_sum / n, sy_sum / n
                        d = math.hypot(sc.x - hx, sc.y - hy)
                        if d <= self.cluster_radius_pct and d < best_dist:
                            best_dist = d
                            best_idx = idx
                if best_idx >= 0:
                    rx, ry, sx_sum, sy_sum, items = hub_groups[best_idx]
                    items.append(q)
                    hub_groups[best_idx] = (rx, ry, sx_sum + sc.x, sy_sum + sc.y, items)
                else:
                    hub_groups.append((sc.x, sc.y, sc.x, sc.y, [q]))

            prefix = f"z{sz}" if (oz == 0 and tz == 0) else f"z{sz}_x{oz}_{tz}"
            sub_idx = comp_seq.get((sz, oz, tz), 0)
            for _, _, sx_sum, sy_sum, h_items in hub_groups:
                hx = sx_sum / len(h_items)
                hy = sy_sum / len(h_items)
                if len(h_items) <= 6:
                    sub_idx += 1
                    cid = f"{prefix}_c{sub_idx}"
                    for q in h_items:
                        q.cluster_id = cid
                    clusters[cid] = h_items
                    continue
                # Split large hubs into directional objective loops of <= 6 quests each
                ordered = sorted(
                    h_items,
                    key=lambda q: (
                        math.atan2(
                            self.quest_objective_centroid(q).y - hy,
                            self.quest_objective_centroid(q).x - hx,
                        ),
                        math.hypot(
                            self.quest_objective_centroid(q).x - hx,
                            self.quest_objective_centroid(q).y - hy,
                        ),
                    ),
                )
                for start_i in range(0, len(ordered), 6):
                    chunk = ordered[start_i : start_i + 6]
                    sub_idx += 1
                    cid = f"{prefix}_c{sub_idx}"
                    for q in chunk:
                        q.cluster_id = cid
                    clusters[cid] = chunk
            comp_seq[(sz, oz, tz)] = sub_idx
        return clusters

    def order_coords_tsp(
        self,
        start_coord: Coord,
        coords: Sequence[Tuple[int, Coord]],
        zone_id: int,
        player_level: int,
    ) -> List[int]:
        """Nearest-neighbor + 2-opt tour over objective coordinates inside a cluster."""
        if len(coords) <= 1:
            return [qid for qid, _ in coords]

        remaining = list(coords)
        ordered: List[Tuple[int, Coord]] = []
        cur = start_coord
        while remaining:
            best_i = min(
                range(len(remaining)),
                key=lambda i: self.in_zone_run_seconds(zone_id, cur.x, cur.y, remaining[i][1].x, remaining[i][1].y, player_level),
            )
            nxt = remaining.pop(best_i)
            ordered.append(nxt)
            cur = nxt[1]

        # 2-opt local refinement
        if len(ordered) >= 4:
            improved = True
            while improved:
                improved = False
                for i in range(len(ordered) - 1):
                    for j in range(i + 2, len(ordered)):
                        a = start_coord if i == 0 else ordered[i - 1][1]
                        b = ordered[i][1]
                        c = ordered[j - 1][1]
                        d = ordered[j][1]
                        cur_cost = (
                            self.in_zone_run_seconds(zone_id, a.x, a.y, b.x, b.y, player_level)
                            + self.in_zone_run_seconds(zone_id, c.x, c.y, d.x, d.y, player_level)
                        )
                        new_cost = (
                            self.in_zone_run_seconds(zone_id, a.x, a.y, c.x, c.y, player_level)
                            + self.in_zone_run_seconds(zone_id, b.x, b.y, d.x, d.y, player_level)
                        )
                        if new_cost + 0.5 < cur_cost:
                            ordered[i:j] = reversed(ordered[i:j])
                            improved = True
        return [qid for qid, _ in ordered]
