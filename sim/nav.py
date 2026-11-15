"""Condensed multimodal navigation graph, Bidirectional Dijkstra, and frontier nearest-quest search."""

from __future__ import annotations

from collections import OrderedDict
import heapq
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import Coord, Quest

NODE_KIND_TO_INT: Dict[str, int] = {
    "hub": 1,
    "hearth": 1,
    "gate": 2,
    "flight": 3,
    "boat": 4,
    "zeppelin": 4,
    "airship": 4,
    "tram": 4,
    "road": 5,
    "dungeon": 5,
    "anchor": 5,
}

EDGE_MODE_TO_INT: Dict[str, int] = {
    "walk": 1,
    "road": 1,
    "gate": 2,
    "tunnel": 2,
    "bridge": 2,
    "portal": 2,
    "flight": 3,
    "boat": 4,
    "zeppelin": 5,
    "airship": 6,
    "tram": 7,
    "hearth": 8,
}

INT_TO_EDGE_MODE: Dict[int, str] = {
    1: "walk",
    2: "gate",
    3: "flight",
    4: "boat",
    5: "zeppelin",
    6: "airship",
    7: "tram",
    8: "hearth",
}

FACTION_TO_MASK: Dict[Any, int] = {
    "Alliance": 1,
    "Horde": 2,
    "Both": 3,
    1: 1,
    2: 2,
    3: 3,
    0: 3,
}

TERRAIN_SPEED_FACTOR: Dict[str, float] = {
    "road": 1.00,
    "walkable": 1.18,
    "swim": 1.85,
}

DEFAULT_ZONE_YARD_SCALES: Dict[int, float] = {
    1: 3600.0,
    3: 2600.0,
    4: 2600.0,
    8: 2300.0,
    10: 2700.0,
    11: 3200.0,
    12: 2800.0,
    14: 3400.0,
    15: 3200.0,
    16: 3500.0,
    17: 6000.0,
    28: 2800.0,
    33: 4600.0,
    36: 2500.0,
    38: 2800.0,
    40: 3000.0,
    41: 1800.0,
    44: 2600.0,
    45: 3000.0,
    46: 2700.0,
    47: 3000.0,
    51: 2400.0,
    65: 3200.0,
    85: 3200.0,
    130: 2900.0,
    139: 3100.0,
    141: 3200.0,
    148: 3500.0,
    215: 3400.0,
    267: 2900.0,
    331: 4200.0,
    357: 3900.0,
    361: 3200.0,
    400: 3300.0,
    405: 3000.0,
    406: 3200.0,
    440: 3800.0,
    490: 2800.0,
    493: 1800.0,
    616: 4200.0,
    618: 3600.0,
    1377: 2700.0,
    1497: 950.0,
    1519: 1150.0,
    1537: 950.0,
    1637: 1100.0,
    1638: 900.0,
    1657: 950.0,
    16591: 4771.0,
    16593: 5830.0,
    16651: 1950.0,
}


# Rules: Immutable descriptor for a strategic NavGraph node (gate, tunnel, hub, flight master, dock, road waypoint).
# IDs are contiguous 1..N integers so Python and exported Lua CSR arrays index identically.
class NavNode:
    node_id: int
    key: str
    zone_id: int
    x: float
    y: float
    kind: str
    kind_int: int
    faction_mask: int
    component_id: int
    flight_node_id: Optional[int]
    name: str

    def __init__(
        self,
        node_id: int,
        key: str,
        zone_id: int,
        x: float,
        y: float,
        kind: str,
        faction_mask: int,
        component_id: int,
        flight_node_id: Optional[int],
        name: str,
    ) -> None:
        self.node_id = node_id
        self.key = key
        self.zone_id = zone_id
        self.x = x
        self.y = y
        self.kind = kind
        self.kind_int = NODE_KIND_TO_INT.get(kind, 5)
        self.faction_mask = faction_mask
        self.component_id = component_id
        self.flight_node_id = flight_node_id
        self.name = name


# Rules: Shortest-path output containing total seconds, collapsed zone/gate steps, intermediate gate pin, and note.
class NavPathResult:
    total_seconds: float
    steps: List[Tuple[int, int, str, int, str]]
    note: str
    used_hearth: bool
    node_path: List[int]
    next_hop: Optional[Tuple[int, float, float, str, str]]

    def __init__(
        self,
        total_seconds: float,
        steps: List[Tuple[int, int, str, int, str]],
        note: str,
        used_hearth: bool = False,
        node_path: Optional[List[int]] = None,
        next_hop: Optional[Tuple[int, float, float, str, str]] = None,
    ) -> None:
        self.total_seconds = total_seconds
        self.steps = steps
        self.note = note
        self.used_hearth = used_hearth
        self.node_path = node_path or []
        self.next_hop = next_hop


# Rules: Condensed CSR multimodal NavGraph supporting exact multi-anchor Bidirectional Dijkstra,
# O(1) Hearthstone seeding, terrain component/barrier checks, and no-full-scan frontier nearest-quest search.
class NavGraph:
    version: int
    unmounted_yps: float
    mounted_yps: float
    grid_width: int
    grid_height: int
    node_count: int
    edge_count: int
    nodes: List[Optional[NavNode]]
    node_zone: List[int]
    node_x: List[float]
    node_y: List[float]
    node_kind: List[int]
    node_comp: List[int]
    node_faction: List[int]
    node_name: List[str]
    node_fp_id: Dict[int, int]
    hearth_nodes: Dict[int, int]
    zone_bounds: Dict[int, Tuple[int, float, float, float, float]]
    zone_cells: Dict[int, Dict[int, Tuple[str, int, List[Tuple[int, float]]]]]
    quest_anchors: Dict[int, Tuple[List[Tuple[int, float]], List[Tuple[int, float]], Optional[List[Tuple[int, float]]]]]
    f_head: List[int]
    f_to: List[int]
    e_cost: List[float]
    e_mode: List[int]
    e_ground: List[int]
    e_faction: List[int]
    e_dest_zone: List[int]
    edge_notes: Dict[int, str]
    r_head: List[int]
    r_from: List[int]
    r_fwd_idx: List[int]
    _quest_buckets: Dict[str, Dict[int, List[Tuple[int, float]]]]
    _f_dist: List[float]
    _b_dist: List[float]
    _f_stamp: List[int]
    _b_stamp: List[int]
    _f_prev: List[int]
    _b_prev: List[int]
    _f_edge: List[int]
    _b_edge: List[int]
    _search_stamp: int
    _flight_zone_bit: Dict[int, int]
    _sssp_cache: "OrderedDict[Tuple[Any, ...], List[float]]"
    _zone_names: Dict[int, str]

    def __init__(
        self,
        version: int,
        unmounted_yps: float,
        mounted_yps: float,
        grid_width: int,
        grid_height: int,
        nodes: List[Optional[NavNode]],
        hearth_nodes: Dict[int, int],
        zone_bounds: Dict[int, Tuple[int, float, float, float, float]],
        zone_cells: Dict[int, Dict[int, Tuple[str, int, List[Tuple[int, float]]]]],
        quest_anchors: Dict[int, Tuple[List[Tuple[int, float]], List[Tuple[int, float]], Optional[List[Tuple[int, float]]]]],
        f_head: List[int],
        f_to: List[int],
        e_cost: List[float],
        e_mode: List[int],
        e_ground: List[int],
        e_faction: List[int],
        e_dest_zone: List[int],
        edge_notes: Dict[int, str],
        r_head: List[int],
        r_from: List[int],
        r_fwd_idx: List[int],
        zone_names: Optional[Dict[int, str]] = None,
    ) -> None:
        self.version = version
        self.unmounted_yps = unmounted_yps
        self.mounted_yps = mounted_yps
        self.grid_width = grid_width
        self.grid_height = grid_height
        self.nodes = nodes
        self.node_count = len(nodes) - 1
        self.edge_count = len(f_to) - 1
        self.node_zone = [0] * (self.node_count + 1)
        self.node_x = [0.0] * (self.node_count + 1)
        self.node_y = [0.0] * (self.node_count + 1)
        self.node_kind = [5] * (self.node_count + 1)
        self.node_comp = [0] * (self.node_count + 1)
        self.node_faction = [3] * (self.node_count + 1)
        self.node_name = [""] * (self.node_count + 1)
        self.node_fp_id = {}
        for i in range(1, self.node_count + 1):
            nd = nodes[i]
            if nd is None:
                continue
            self.node_zone[i] = nd.zone_id
            self.node_x[i] = nd.x
            self.node_y[i] = nd.y
            self.node_kind[i] = nd.kind_int
            self.node_comp[i] = nd.component_id
            self.node_faction[i] = nd.faction_mask
            self.node_name[i] = nd.name
            if nd.flight_node_id is not None:
                self.node_fp_id[i] = nd.flight_node_id
        self.hearth_nodes = hearth_nodes
        self.zone_bounds = zone_bounds
        self.zone_cells = zone_cells
        self.quest_anchors = quest_anchors
        self.f_head = f_head
        self.f_to = f_to
        self.e_cost = e_cost
        self.e_mode = e_mode
        self.e_ground = e_ground
        self.e_faction = e_faction
        self.e_dest_zone = e_dest_zone
        self.edge_notes = edge_notes
        self.r_head = r_head
        self.r_from = r_from
        self.r_fwd_idx = r_fwd_idx
        self._zone_names = dict(zone_names or {})

        # Preallocated scratch buffers (epoch-stamped, zero per-query table allocation)
        sz = self.node_count + 1
        self._f_dist = [0.0] * sz
        self._b_dist = [0.0] * sz
        self._f_stamp = [0] * sz
        self._b_stamp = [0] * sz
        self._f_prev = [0] * sz
        self._b_prev = [0] * sz
        self._f_edge = [0] * sz
        self._b_edge = [0] * sz
        self._search_stamp = 0
        flight_zones = sorted({e_dest_zone[i] for i in range(1, self.edge_count + 1) if e_mode[i] == 3 and e_dest_zone[i] > 0})
        self._flight_zone_bit = {zid: (1 << idx) for idx, zid in enumerate(flight_zones)}
        self._sssp_cache = OrderedDict()
        self._quest_buckets = {"s": {}, "o": {}, "t": {}}
        self._build_quest_buckets()

    def _flights_bitmask(self, discovered_flights: Optional[Set[int]]) -> Optional[int]:
        if discovered_flights is None:
            return None
        mask = 0
        fb = self._flight_zone_bit
        for z in discovered_flights:
            b = fb.get(z)
            if b:
                mask |= b
        return mask

    def _build_quest_buckets(self) -> None:
        buckets_s: Dict[int, List[Tuple[int, float]]] = {}
        buckets_o: Dict[int, List[Tuple[int, float]]] = {}
        buckets_t: Dict[int, List[Tuple[int, float]]] = {}
        for qid, (s_anc, o_anc, t_anc) in self.quest_anchors.items():
            for nid, sec in s_anc:
                buckets_s.setdefault(nid, []).append((qid, sec))
            for nid, sec in o_anc:
                buckets_o.setdefault(nid, []).append((qid, sec))
            t_list = t_anc if t_anc is not None else s_anc
            for nid, sec in t_list:
                buckets_t.setdefault(nid, []).append((qid, sec))
        self._quest_buckets = {"s": buckets_s, "o": buckets_o, "t": buckets_t}

    @classmethod
    def from_json(cls, path: str | Path, zone_names: Optional[Dict[int, str]] = None) -> "NavGraph":
        with Path(path).open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls.from_dict(raw, zone_names=zone_names)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], zone_names: Optional[Dict[int, str]] = None) -> "NavGraph":
        meta = raw.get("meta", {})
        version = int(meta.get("version", 1) or 1)
        unmounted_yps = float(meta.get("unmountedYdsPerSec", 7.5) or 7.5)
        mounted_yps = float(meta.get("mountedYdsPerSec", 11.4) or 11.4)
        grid_width = int(meta.get("gridWidth", 20) or 20)
        grid_height = int(meta.get("gridHeight", 20) or 20)

        raw_nodes = raw.get("nodes", [])
        sorted_nodes = sorted(raw_nodes, key=lambda n: int(n["id"]))
        id_map: Dict[int, int] = {}
        nodes: List[Optional[NavNode]] = [None]
        for idx, rn in enumerate(sorted_nodes, start=1):
            orig_id = int(rn["id"])
            id_map[orig_id] = idx
            kind = str(rn.get("kind", "anchor") or "anchor").lower()
            nodes.append(
                NavNode(
                    node_id=idx,
                    key=str(rn.get("key", f"n_{idx}")),
                    zone_id=int(rn["zoneId"]),
                    x=float(rn["x"]),
                    y=float(rn["y"]),
                    kind=kind,
                    faction_mask=FACTION_TO_MASK.get(rn.get("faction", "Both"), 3),
                    component_id=int(rn.get("componentId", 1) or 1),
                    flight_node_id=int(rn["flightNodeId"]) if rn.get("flightNodeId") is not None else None,
                    name=str(rn.get("name", "") or ""),
                )
            )

        n_count = len(nodes) - 1
        raw_edges = raw.get("edges", [])
        out_lists: List[List[Tuple[int, float, int, int, int, int, str]]] = [[] for _ in range(n_count + 1)]
        for re in raw_edges:
            u = id_map.get(int(re["fromId"]))
            v = id_map.get(int(re["toId"]))
            if u is None or v is None or u == v:
                continue
            mode_str = str(re.get("mode", "walk") or "walk").lower()
            mode_int = EDGE_MODE_TO_INT.get(mode_str, 1)
            sec = max(0.0, float(re.get("seconds", 0.0) or 0.0))
            ground_scaled = 1 if bool(re.get("groundScaled", mode_int <= 2)) else 0
            fmask = FACTION_TO_MASK.get(re.get("faction", "Both"), 3)
            dest_z = int(re.get("flightDestZoneId") or (nodes[v].zone_id if mode_int == 3 and nodes[v] else 0))
            note = str(re.get("note", "") or "")
            out_lists[u].append((v, sec, mode_int, ground_scaled, fmask, dest_z, note))

        for u in range(1, n_count + 1):
            out_lists[u].sort(key=lambda item: (item[0], item[2], item[1]))

        f_head = [1] * (n_count + 2)
        f_to: List[int] = [0]
        e_cost: List[float] = [0.0]
        e_mode: List[int] = [0]
        e_ground: List[int] = [0]
        e_faction: List[int] = [3]
        e_dest_zone: List[int] = [0]
        edge_notes: Dict[int, str] = {}
        rev_lists: List[List[Tuple[int, int]]] = [[] for _ in range(n_count + 1)]

        cur_e = 1
        for u in range(1, n_count + 1):
            f_head[u] = cur_e
            for v, sec, m_int, g_int, fm, dz, note in out_lists[u]:
                f_to.append(v)
                e_cost.append(sec)
                e_mode.append(m_int)
                e_ground.append(g_int)
                e_faction.append(fm)
                e_dest_zone.append(dz)
                if note:
                    edge_notes[cur_e] = note
                rev_lists[v].append((u, cur_e))
                cur_e += 1
        f_head[n_count + 1] = cur_e

        r_head = [1] * (n_count + 2)
        r_from: List[int] = [0]
        r_fwd_idx: List[int] = [0]
        cur_r = 1
        for v in range(1, n_count + 1):
            r_head[v] = cur_r
            rev_lists[v].sort(key=lambda item: (item[0], item[1]))
            for u, e_idx in rev_lists[v]:
                r_from.append(u)
                r_fwd_idx.append(e_idx)
                cur_r += 1
        r_head[n_count + 1] = cur_r

        def _remap_anchors(raw_anc: Any) -> List[Tuple[int, float]]:
            out: List[Tuple[int, float]] = []
            for item in raw_anc or []:
                nid = id_map.get(int(item[0]))
                if nid is not None:
                    out.append((nid, max(0.0, float(item[1]))))
            out.sort(key=lambda a: a[1])
            return out

        hearth_nodes: Dict[int, int] = {}
        for z_str, nid_raw in (raw.get("hearthNodes") or {}).items():
            nid = id_map.get(int(nid_raw))
            if nid is not None:
                hearth_nodes[int(z_str)] = nid

        zone_bounds: Dict[int, Tuple[int, float, float, float, float]] = {}
        zone_cells: Dict[int, Dict[int, Tuple[str, int, List[Tuple[int, float]]]]] = {}
        for z_str, zgrid in (raw.get("zoneGrids") or {}).items():
            zid = int(z_str)
            wb = zgrid.get("worldBounds")
            if isinstance(wb, list) and len(wb) >= 5:
                zone_bounds[zid] = (int(wb[0]), float(wb[1]), float(wb[2]), float(wb[3]), float(wb[4]))
            cells_out: Dict[int, Tuple[str, int, List[Tuple[int, float]]]] = {}
            for c_str, cobj in (zgrid.get("cells") or {}).items():
                c_idx = int(c_str)
                terrain = str(cobj.get("terrain", "walkable") or "walkable")
                comp_id = int(cobj.get("componentId", 1) or 1)
                anc = _remap_anchors(cobj.get("anchors"))
                if anc:
                    cells_out[c_idx] = (terrain, comp_id, anc)
            zone_cells[zid] = cells_out

        quest_anchors: Dict[int, Tuple[List[Tuple[int, float]], List[Tuple[int, float]], Optional[List[Tuple[int, float]]]]] = {}
        for q_str, qobj in (raw.get("questAnchors") or {}).items():
            qid = int(q_str)
            s_anc = _remap_anchors(qobj.get("s"))
            o_anc = _remap_anchors(qobj.get("o")) or list(s_anc)
            t_raw = qobj.get("t")
            t_anc = _remap_anchors(t_raw) if t_raw is not None else None
            if s_anc or o_anc or t_anc:
                quest_anchors[qid] = (s_anc or o_anc, o_anc or s_anc, t_anc)

        return cls(
            version=version,
            unmounted_yps=unmounted_yps,
            mounted_yps=mounted_yps,
            grid_width=grid_width,
            grid_height=grid_height,
            nodes=nodes,
            hearth_nodes=hearth_nodes,
            zone_bounds=zone_bounds,
            zone_cells=zone_cells,
            quest_anchors=quest_anchors,
            f_head=f_head,
            f_to=f_to,
            e_cost=e_cost,
            e_mode=e_mode,
            e_ground=e_ground,
            e_faction=e_faction,
            e_dest_zone=e_dest_zone,
            edge_notes=edge_notes,
            r_head=r_head,
            r_from=r_from,
            r_fwd_idx=r_fwd_idx,
            zone_names=zone_names,
        )

    def ground_speed(self, player_level: int) -> float:
        return self.mounted_yps if player_level >= 40 else self.unmounted_yps

    def ground_scale(self, player_level: int) -> float:
        return (self.unmounted_yps / self.mounted_yps) if player_level >= 40 else 1.0

    def _cell_xy(self, x: float, y: float) -> Tuple[int, int, int]:
        cx = min(self.grid_width - 1, max(0, int(max(0.0, min(99.999, x)) * self.grid_width / 100.0)))
        cy = min(self.grid_height - 1, max(0, int(max(0.0, min(99.999, y)) * self.grid_height / 100.0)))
        return cx, cy, cy * self.grid_width + cx + 1

    def chord_yards(self, zone_id: int, x1: float, y1: float, x2: float, y2: float) -> float:
        wb = self.zone_bounds.get(zone_id)
        if wb is not None:
            _, left_wy, right_wy, top_wx, bottom_wx = wb
            dy_yards = (x2 - x1) / 100.0 * abs(left_wy - right_wy)
            dx_yards = (y2 - y1) / 100.0 * abs(top_wx - bottom_wx)
            return math.hypot(dx_yards, dy_yards)
        scale = DEFAULT_ZONE_YARD_SCALES.get(zone_id, 2800.0)
        dx = (x2 - x1) / 100.0 * scale
        dy = (y2 - y1) / 100.0 * (scale * 0.72)
        return math.hypot(dx, dy)

    def chord_seconds(self, zone_id: int, x1: float, y1: float, x2: float, y2: float, player_level: int = 24, factor: float = 1.18) -> float:
        return (self.chord_yards(zone_id, x1, y1, x2, y2) * factor) / self.ground_speed(player_level)

    def coord_anchors(
        self,
        zone_id: int,
        x: float,
        y: float,
    ) -> Tuple[int, int, str, List[Tuple[int, float]]]:
        """Return (cell_idx, component_id, terrain, [(node_id, unmounted_walk_sec), ...])."""
        zcells = self.zone_cells.get(zone_id)
        if not zcells:
            return 0, 0, "walkable", []
        cx0, cy0, cell_idx = self._cell_xy(x, y)
        hit = zcells.get(cell_idx)
        if hit is not None:
            terrain, comp_id, anchors = hit
            return cell_idx, comp_id, terrain, anchors

        # Walk expanding rings when coord falls on a blocked border/dock pixel
        best_dist = float("inf")
        best_entry: Optional[Tuple[int, int, str, List[Tuple[int, float]]]] = None
        w = self.grid_width
        h = self.grid_height
        for radius in range(1, max(w, h)):
            found_ring = False
            for dy in range(-radius, radius + 1):
                ny = cy0 + dy
                if ny < 0 or ny >= h:
                    continue
                for dx in range(-radius, radius + 1):
                    if abs(dx) != radius and abs(dy) != radius:
                        continue
                    nx = cx0 + dx
                    if nx < 0 or nx >= w:
                        continue
                    idx = ny * w + nx + 1
                    cand = zcells.get(idx)
                    if cand is not None:
                        found_ring = True
                        cx_pct = (nx + 0.5) * 100.0 / w
                        cy_pct = (ny + 0.5) * 100.0 / h
                        d = self.chord_yards(zone_id, x, y, cx_pct, cy_pct)
                        if d < best_dist:
                            best_dist = d
                            terrain, comp_id, anchors = cand
                            approach_sec = (d * 1.18) / self.unmounted_yps
                            shifted = [(nid, round(ws + approach_sec, 2)) for nid, ws in anchors]
                            best_entry = (idx, comp_id, terrain, shifted)
            if found_ring and best_entry is not None:
                return best_entry
        return cell_idx, 0, "walkable", []

    def quest_role_anchors(self, quest: Quest, role: str, fallback_coord: Coord) -> List[Tuple[int, float]]:
        qa = self.quest_anchors.get(quest.quest_id)
        if qa is not None:
            s_anc, o_anc, t_anc = qa
            if role == "s":
                return s_anc
            if role == "o":
                return o_anc
            return t_anc if t_anc is not None else s_anc
        _, _, _, anc = self.coord_anchors(fallback_coord.zone_id, fallback_coord.x, fallback_coord.y)
        return anc

    def line_is_clear(self, zone_id: int, x1: float, y1: float, x2: float, y2: float) -> Tuple[bool, float]:
        """Verify all grid samples on segment share one non-blocked component; return (clear, mean_factor)."""
        zcells = self.zone_cells.get(zone_id)
        if not zcells:
            return False, 1.18
        span_x = abs(x2 - x1) * self.grid_width / 100.0
        span_y = abs(y2 - y1) * self.grid_height / 100.0
        steps = max(2, int(math.ceil(max(span_x, span_y) * 2.0)))
        comp_ref: Optional[int] = None
        factor_sum = 0.0
        for s in range(steps + 1):
            t = s / steps
            px = x1 + (x2 - x1) * t
            py = y1 + (y2 - y1) * t
            _, _, idx = self._cell_xy(px, py)
            cell = zcells.get(idx)
            if cell is None:
                return False, 1.85
            terrain, comp_id, _ = cell
            if comp_ref is None:
                comp_ref = comp_id
            elif comp_id != comp_ref:
                return False, 1.85
            factor_sum += TERRAIN_SPEED_FACTOR.get(terrain, 1.18)
        return True, factor_sum / (steps + 1)

    def _zone_name(self, zone_id: int) -> str:
        return self._zone_names.get(zone_id, f"Zone {zone_id}")

    def _edge_cost(
        self,
        e_idx: int,
        faction_mask: int,
        g_scale: float,
        discovered_flights: Optional[Set[int]],
    ) -> Optional[float]:
        if (self.e_faction[e_idx] & faction_mask) == 0:
            return None
        sec = self.e_cost[e_idx]
        if self.e_ground[e_idx]:
            sec *= g_scale
        if self.e_mode[e_idx] == 3 and discovered_flights is not None:
            dz = self.e_dest_zone[e_idx]
            if dz and dz not in discovered_flights:
                sec += 45.0
        return sec

    def _resolve_hearth_node(self, hearth_zone_id: int) -> Optional[int]:
        nid = self.hearth_nodes.get(hearth_zone_id)
        if nid is not None:
            return nid
        for i in range(1, self.node_count + 1):
            if self.node_zone[i] == hearth_zone_id:
                return i
        return None

    def shortest_path(
        self,
        src_zone: int,
        src_x: float,
        src_y: float,
        dst_zone: int,
        dst_x: float,
        dst_y: float,
        player_level: int = 24,
        faction: str = "Alliance",
        allow_hearth: bool = False,
        hearth_zone_id: int = 11,
        discovered_flights: Optional[Set[int]] = None,
        dst_anchors_override: Optional[List[Tuple[int, float]]] = None,
    ) -> Optional[NavPathResult]:
        if src_zone not in self.zone_cells or dst_zone not in self.zone_cells:
            return None

        src_cell, src_comp, _, src_anchors = self.coord_anchors(src_zone, src_x, src_y)
        if dst_anchors_override:
            dst_anchors = dst_anchors_override
            _, dst_comp, _, _ = self.coord_anchors(dst_zone, dst_x, dst_y)
        else:
            _, dst_comp, _, dst_anchors = self.coord_anchors(dst_zone, dst_x, dst_y)
        if not src_anchors or not dst_anchors:
            return None

        g_scale = self.ground_scale(player_level)
        faction_mask = FACTION_TO_MASK.get(faction, 3)

        # Unobstructed short local walk check inside same zone & terrain component
        direct_sec: Optional[float] = None
        if src_zone == dst_zone and src_comp > 0 and src_comp == dst_comp:
            clear, factor = self.line_is_clear(src_zone, src_x, src_y, dst_x, dst_y)
            if clear:
                direct_sec = (self.chord_yards(src_zone, src_x, src_y, dst_x, dst_y) * factor) / self.ground_speed(player_level)

        # Multi-source, multi-target Bidirectional Dijkstra with epoch-stamped arrays
        self._search_stamp += 1
        stamp = self._search_stamp
        f_dist = self._f_dist
        b_dist = self._b_dist
        f_stamp = self._f_stamp
        b_stamp = self._b_stamp
        f_prev = self._f_prev
        b_prev = self._b_prev
        f_edge = self._f_edge
        b_edge = self._b_edge

        f_pq: List[Tuple[float, int]] = []
        b_pq: List[Tuple[float, int]] = []

        for nid, base_w in src_anchors:
            w = base_w * g_scale
            if f_stamp[nid] != stamp or w < f_dist[nid]:
                f_stamp[nid] = stamp
                f_dist[nid] = w
                f_prev[nid] = 0
                f_edge[nid] = -1
                heapq.heappush(f_pq, (w, nid))

        if allow_hearth:
            h_nid = self._resolve_hearth_node(hearth_zone_id)
            if h_nid is not None and (hearth_zone_id != src_zone or src_comp != self.node_comp[h_nid]):
                h_cost = 12.0
                if f_stamp[h_nid] != stamp or h_cost < f_dist[h_nid]:
                    f_stamp[h_nid] = stamp
                    f_dist[h_nid] = h_cost
                    f_prev[h_nid] = 0
                    f_edge[h_nid] = -2  # Sentinel for Hearthstone
                    heapq.heappush(f_pq, (h_cost, h_nid))

        best_cost = direct_sec if direct_sec is not None else float("inf")
        meet_node = 0

        for nid, base_w in dst_anchors:
            w = base_w * g_scale
            if b_stamp[nid] != stamp or w < b_dist[nid]:
                b_stamp[nid] = stamp
                b_dist[nid] = w
                b_prev[nid] = 0
                b_edge[nid] = -1
                heapq.heappush(b_pq, (w, nid))
            if f_stamp[nid] == stamp:
                cand = f_dist[nid] + b_dist[nid]
                if cand < best_cost:
                    best_cost = cand
                    meet_node = nid

        f_head = self.f_head
        f_to = self.f_to
        r_head = self.r_head
        r_from = self.r_from
        r_fwd_idx = self.r_fwd_idx

        while f_pq and b_pq:
            if f_pq[0][0] + b_pq[0][0] >= best_cost:
                break

            if len(f_pq) <= len(b_pq):
                cost, u = heapq.heappop(f_pq)
                if cost > f_dist[u]:
                    continue
                for e_idx in range(f_head[u], f_head[u + 1]):
                    ec = self._edge_cost(e_idx, faction_mask, g_scale, discovered_flights)
                    if ec is None:
                        continue
                    v = f_to[e_idx]
                    ncost = cost + ec
                    if f_stamp[v] != stamp or ncost < f_dist[v]:
                        f_stamp[v] = stamp
                        f_dist[v] = ncost
                        f_prev[v] = u
                        f_edge[v] = e_idx
                        heapq.heappush(f_pq, (ncost, v))
                        if b_stamp[v] == stamp:
                            tot = ncost + b_dist[v]
                            if tot < best_cost:
                                best_cost = tot
                                meet_node = v
            else:
                cost, v = heapq.heappop(b_pq)
                if cost > b_dist[v]:
                    continue
                for ri in range(r_head[v], r_head[v + 1]):
                    e_idx = r_fwd_idx[ri]
                    ec = self._edge_cost(e_idx, faction_mask, g_scale, discovered_flights)
                    if ec is None:
                        continue
                    u = r_from[ri]
                    ncost = cost + ec
                    if b_stamp[u] != stamp or ncost < b_dist[u]:
                        b_stamp[u] = stamp
                        b_dist[u] = ncost
                        b_prev[u] = v
                        b_edge[u] = e_idx
                        heapq.heappush(b_pq, (ncost, u))
                        if f_stamp[u] == stamp:
                            tot = f_dist[u] + ncost
                            if tot < best_cost:
                                best_cost = tot
                                meet_node = u

        if direct_sec is not None and direct_sec <= best_cost:
            return NavPathResult(
                total_seconds=direct_sec,
                steps=[],
                note=f"Run in {self._zone_name(dst_zone)} to ({dst_x:.1f}, {dst_y:.1f})",
                used_hearth=False,
            )

        if meet_node == 0 or not math.isfinite(best_cost):
            return None

        # Reconstruct node path and forward edge sequence
        f_nodes: List[int] = []
        f_edges: List[int] = []
        cur = meet_node
        used_hearth = False
        while cur != 0:
            f_nodes.append(cur)
            e_taken = f_edge[cur]
            if e_taken == -2:
                used_hearth = True
            elif e_taken > 0:
                f_edges.append(e_taken)
            cur = f_prev[cur]
        f_nodes.reverse()
        f_edges.reverse()

        b_nodes: List[int] = []
        b_edges: List[int] = []
        cur = meet_node
        while b_prev[cur] != 0:
            e_taken = b_edge[cur]
            nxt = b_prev[cur]
            if e_taken > 0:
                b_edges.append(e_taken)
            b_nodes.append(nxt)
            cur = nxt

        full_nodes = f_nodes + b_nodes
        full_edges = f_edges + b_edges

        return self._summarize_path(
            total_seconds=best_cost,
            src_zone=src_zone,
            dst_zone=dst_zone,
            dst_x=dst_x,
            dst_y=dst_y,
            full_nodes=full_nodes,
            full_edges=full_edges,
            used_hearth=used_hearth,
            g_scale=g_scale,
            discovered_flights=discovered_flights,
            faction_mask=faction_mask,
        )

    def _summarize_path(
        self,
        total_seconds: float,
        src_zone: int,
        dst_zone: int,
        dst_x: float,
        dst_y: float,
        full_nodes: List[int],
        full_edges: List[int],
        used_hearth: bool,
        g_scale: float,
        discovered_flights: Optional[Set[int]],
        faction_mask: int,
    ) -> NavPathResult:
        steps: List[Tuple[int, int, str, int, str]] = []
        next_hop: Optional[Tuple[int, float, float, str, str]] = None
        note_parts: List[str] = []

        if used_hearth and full_nodes:
            hz = self.node_zone[full_nodes[0]]
            hnote = f"Hearthstone to {self._zone_name(hz)}"
            steps.append((src_zone, hz, "hearth", 12, hnote))
            note_parts.append(hnote)
            next_hop = (src_zone, self.node_x[full_nodes[0]], self.node_y[full_nodes[0]], "hearth", hnote)

        # Collapse contiguous intra-zone walk edges while preserving gates & transports
        walk_accum_sec = 0.0
        walk_start_z = self.node_zone[full_nodes[0]] if full_nodes else src_zone

        for idx, e_idx in enumerate(full_edges):
            u = full_nodes[idx]
            v = full_nodes[idx + 1]
            uz = self.node_zone[u]
            vz = self.node_zone[v]
            mode_int = self.e_mode[e_idx]
            mode_str = INT_TO_EDGE_MODE.get(mode_int, "walk")
            sec = self._edge_cost(e_idx, faction_mask, g_scale, discovered_flights) or self.e_cost[e_idx]
            e_note = self.edge_notes.get(e_idx, "")

            if mode_int == 1 and uz == vz:
                walk_accum_sec += sec
                continue

            if walk_accum_sec > 0.0:
                walk_accum_sec = 0.0
                walk_start_z = uz

            label = e_note or (
                f"{mode_str.title()} {self._zone_name(uz)} -> {self._zone_name(vz)}"
                if uz != vz
                else (self.node_name[v] or mode_str.title())
            )
            steps.append((uz, vz, mode_str, int(round(sec)), label))
            note_parts.append(label)
            if next_hop is None:
                next_hop = (uz, self.node_x[u], self.node_y[u], mode_str, label)
            walk_start_z = vz

        if walk_accum_sec > 0.0 and not note_parts and src_zone != dst_zone:
            steps.append((walk_start_z, dst_zone, "walk", int(round(walk_accum_sec)), f"Walk to {self._zone_name(dst_zone)}"))
            note_parts.append(f"Walk to {self._zone_name(dst_zone)}")

        summary = " -> ".join(note_parts) if note_parts else f"Run in {self._zone_name(dst_zone)} to ({dst_x:.1f}, {dst_y:.1f})"
        return NavPathResult(
            total_seconds=total_seconds,
            steps=steps,
            note=summary,
            used_hearth=used_hearth,
            node_path=full_nodes,
            next_hop=next_hop,
        )

    def single_source_node_dists(
        self,
        src_zone: int,
        src_x: float,
        src_y: float,
        player_level: int = 24,
        faction: str = "Alliance",
        allow_hearth: bool = False,
        hearth_zone_id: int = 11,
        discovered_flights: Optional[Set[int]] = None,
    ) -> Optional[List[float]]:
        """Compute and LRU-cache shortest cost from player position to all reachable NavNodes."""
        zcells = self.zone_cells.get(src_zone)
        if zcells is None:
            return None
        cell_idx, src_comp, _, src_anchors = self.coord_anchors(src_zone, src_x, src_y)
        if not src_anchors:
            return None

        mounted = player_level >= 40
        f_mask = self._flights_bitmask(discovered_flights)
        pos_key = cell_idx if cell_idx in zcells else (round(src_x, 1), round(src_y, 1))
        cache_key = (
            faction,
            src_zone,
            pos_key,
            mounted,
            bool(allow_hearth),
            hearth_zone_id if allow_hearth else 0,
            f_mask,
        )
        cached = self._sssp_cache.get(cache_key)
        if cached is not None:
            self._sssp_cache.move_to_end(cache_key)
            return cached

        g_scale = self.ground_scale(player_level)
        faction_mask = FACTION_TO_MASK.get(faction, 3)

        self._search_stamp += 1
        stamp = self._search_stamp
        f_dist = self._f_dist
        f_stamp = self._f_stamp
        heappush = heapq.heappush
        heappop = heapq.heappop
        pq: List[Tuple[float, int]] = []

        for nid, base_w in src_anchors:
            w = base_w * g_scale
            if f_stamp[nid] != stamp or w < f_dist[nid]:
                f_stamp[nid] = stamp
                f_dist[nid] = w
                heappush(pq, (w, nid))

        if allow_hearth:
            h_nid = self._resolve_hearth_node(hearth_zone_id)
            if h_nid is not None and (hearth_zone_id != src_zone or src_comp != self.node_comp[h_nid]):
                if f_stamp[h_nid] != stamp or 12.0 < f_dist[h_nid]:
                    f_stamp[h_nid] = stamp
                    f_dist[h_nid] = 12.0
                    heappush(pq, (12.0, h_nid))

        f_head = self.f_head
        f_to = self.f_to
        e_cost = self.e_cost
        e_mode = self.e_mode
        e_ground = self.e_ground
        e_faction = self.e_faction
        e_dest_zone = self.e_dest_zone
        inf = float("inf")
        out_dist = [inf] * (self.node_count + 1)

        if discovered_flights is None:
            while pq:
                cost, u = heappop(pq)
                if cost > f_dist[u]:
                    continue
                out_dist[u] = cost
                for e_idx in range(f_head[u], f_head[u + 1]):
                    if (e_faction[e_idx] & faction_mask) == 0:
                        continue
                    ec = e_cost[e_idx]
                    if e_ground[e_idx]:
                        ec *= g_scale
                    v = f_to[e_idx]
                    ncost = cost + ec
                    if f_stamp[v] != stamp or ncost < f_dist[v]:
                        f_stamp[v] = stamp
                        f_dist[v] = ncost
                        heappush(pq, (ncost, v))
        else:
            while pq:
                cost, u = heappop(pq)
                if cost > f_dist[u]:
                    continue
                out_dist[u] = cost
                for e_idx in range(f_head[u], f_head[u + 1]):
                    if (e_faction[e_idx] & faction_mask) == 0:
                        continue
                    ec = e_cost[e_idx]
                    if e_ground[e_idx]:
                        ec *= g_scale
                    if e_mode[e_idx] == 3:
                        dz = e_dest_zone[e_idx]
                        if dz and dz not in discovered_flights:
                            ec += 45.0
                    v = f_to[e_idx]
                    ncost = cost + ec
                    if f_stamp[v] != stamp or ncost < f_dist[v]:
                        f_stamp[v] = stamp
                        f_dist[v] = ncost
                        heappush(pq, (ncost, v))

        self._sssp_cache[cache_key] = out_dist
        if len(self._sssp_cache) > 4096:
            self._sssp_cache.popitem(last=False)
        return out_dist

    def estimate_from_node_dists(
        self,
        node_dists: List[float],
        src_zone: int,
        src_x: float,
        src_y: float,
        dst_zone: int,
        dst_x: float,
        dst_y: float,
        player_level: int = 24,
        dst_anchors: Optional[List[Tuple[int, float]]] = None,
    ) -> Optional[float]:
        """Fast O(1) distance query reusing precomputed single-source node distances."""
        dst_comp = 0
        anchors = dst_anchors
        if anchors is None:
            _, dst_comp, _, anchors = self.coord_anchors(dst_zone, dst_x, dst_y)
        elif src_zone == dst_zone:
            _, dst_comp, _, _ = self.coord_anchors(dst_zone, dst_x, dst_y)
        if not anchors:
            return None

        g_scale = self.ground_scale(player_level)
        best = float("inf")
        for nid, base_w in anchors:
            d = node_dists[nid]
            if d < best:
                cand = d + base_w * g_scale
                if cand < best:
                    best = cand

        if src_zone == dst_zone:
            _, src_comp, _, _ = self.coord_anchors(src_zone, src_x, src_y)
            if src_comp > 0 and src_comp == dst_comp:
                clear, factor = self.line_is_clear(src_zone, src_x, src_y, dst_x, dst_y)
                if clear:
                    d_sec = (self.chord_yards(src_zone, src_x, src_y, dst_x, dst_y) * factor) / self.ground_speed(player_level)
                    if d_sec < best:
                        best = d_sec
        return best if math.isfinite(best) else None

    def nearest_eligible_quests(
        self,
        src_zone: int,
        src_x: float,
        src_y: float,
        eligible_pool: Sequence[Quest],
        score_fn: Callable[[Quest, float], float],
        upper_bound_fn: Callable[[float], float],
        coord_fn: Callable[[Quest], Coord],
        role: str = "s",
        player_level: int = 24,
        faction: str = "Alliance",
        allow_hearth: bool = True,
        hearth_zone_id: int = 11,
        discovered_flights: Optional[Set[int]] = None,
    ) -> Tuple[Optional[Quest], float]:
        """Frontier Dijkstra search over precomputed nodeToEligible buckets with exact upper-bound cutoff."""
        if not eligible_pool or src_zone not in self.zone_cells:
            return None, -1e9

        _, src_comp, _, src_anchors = self.coord_anchors(src_zone, src_x, src_y)
        if not src_anchors:
            return None, -1e9

        g_scale = self.ground_scale(player_level)
        faction_mask = FACTION_TO_MASK.get(faction, 3)
        pool_map: Dict[int, Quest] = {q.quest_id: q for q in eligible_pool}
        buckets = self._quest_buckets.get(role, self._quest_buckets["s"])
        extra_buckets: Dict[int, List[Tuple[int, float]]] = {}

        best_q: Optional[Quest] = None
        best_val = -1e9
        best_t: Dict[int, float] = {}

        # Evaluate local unobstructed quests in source zone first & index any quests absent from questAnchors
        for q in eligible_pool:
            qc = coord_fn(q)
            if q.quest_id not in self.quest_anchors:
                _, _, _, fallback_anc = self.coord_anchors(qc.zone_id, qc.x, qc.y)
                for nid, sec in fallback_anc:
                    extra_buckets.setdefault(nid, []).append((q.quest_id, sec))
            if qc.zone_id == src_zone:
                _, q_comp, _, _ = self.coord_anchors(qc.zone_id, qc.x, qc.y)
                if src_comp > 0 and q_comp == src_comp:
                    clear, factor = self.line_is_clear(src_zone, src_x, src_y, qc.x, qc.y)
                    if clear:
                        d_sec = (self.chord_yards(src_zone, src_x, src_y, qc.x, qc.y) * factor) / self.ground_speed(player_level)
                        best_t[q.quest_id] = d_sec
                        val = score_fn(q, d_sec)
                        if val > best_val:
                            best_val = val
                            best_q = q

        self._search_stamp += 1
        stamp = self._search_stamp
        f_dist = self._f_dist
        f_stamp = self._f_stamp
        pq: List[Tuple[float, int]] = []

        for nid, base_w in src_anchors:
            w = base_w * g_scale
            if f_stamp[nid] != stamp or w < f_dist[nid]:
                f_stamp[nid] = stamp
                f_dist[nid] = w
                heapq.heappush(pq, (w, nid))

        if allow_hearth:
            h_nid = self._resolve_hearth_node(hearth_zone_id)
            if h_nid is not None and (hearth_zone_id != src_zone or src_comp != self.node_comp[h_nid]):
                if f_stamp[h_nid] != stamp or 12.0 < f_dist[h_nid]:
                    f_stamp[h_nid] = stamp
                    f_dist[h_nid] = 12.0
                    heapq.heappush(pq, (12.0, h_nid))

        f_head = self.f_head
        f_to = self.f_to

        while pq:
            cost, u = heapq.heappop(pq)
            if cost > f_dist[u]:
                continue
            if best_q is not None and upper_bound_fn(cost) <= best_val:
                break

            for bucket_src in (buckets.get(u, ()), extra_buckets.get(u, ())):
                for qid, anchor_w in bucket_src:
                    q = pool_map.get(qid)
                    if q is None:
                        continue
                    t_sec = cost + anchor_w * g_scale
                    prev_best = best_t.get(qid)
                    if prev_best is None or t_sec < prev_best:
                        best_t[qid] = t_sec
                        val = score_fn(q, t_sec)
                        if val > best_val:
                            best_val = val
                            best_q = q

            for e_idx in range(f_head[u], f_head[u + 1]):
                ec = self._edge_cost(e_idx, faction_mask, g_scale, discovered_flights)
                if ec is None:
                    continue
                v = f_to[e_idx]
                ncost = cost + ec
                if f_stamp[v] != stamp or ncost < f_dist[v]:
                    f_stamp[v] = stamp
                    f_dist[v] = ncost
                    heapq.heappush(pq, (ncost, v))

        return best_q, best_val

    def in_zone_travel_seconds(
        self,
        zone_id: int,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        player_level: int = 24,
        faction: str = "Both",
    ) -> Optional[float]:
        """Compute true terrain/road-aware in-zone travel seconds via grid check + graph shortest path."""
        res = self.shortest_path(
            src_zone=zone_id,
            src_x=x1,
            src_y=y1,
            dst_zone=zone_id,
            dst_x=x2,
            dst_y=y2,
            player_level=player_level,
            faction=faction,
            allow_hearth=False,
        )
        return res.total_seconds if res is not None else None
