"""graph.py — Agent dataclass, SafetyController, area loader.

Entry point:  load_area(area_name) -> (G, nodes, edges)
              reads areas/<area>/nodes.json + edges.json.
"""
from __future__ import annotations
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import networkx as nx


# ── Area JSON loader ───────────────────────────────────────────────────────

_AREAS_DIR = Path(__file__).parent / "areas"


def load_area(area_name: str) -> Tuple[nx.Graph, dict, list]:
    """Load nodes.json + edges.json for *area_name* and return (G, nodes, edges).

    nodes: dict[name -> {zone, pos?, max_capacity?}]
    edges: list[{u, v, is_road, is_water, length}]
    G:     NetworkX Graph ready for simulation
    """
    base = _AREAS_DIR / area_name
    nodes_path = base / "nodes.json"
    edges_path = base / "edges.json"
    if not nodes_path.exists():
        raise FileNotFoundError(f"No nodes.json for area '{area_name}' at {nodes_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"No edges.json for area '{area_name}' at {edges_path}")

    with open(nodes_path) as f:
        nodes: dict = json.load(f)
    with open(edges_path) as f:
        edges: list = json.load(f)

    # Coerce is_road / is_water to bool in case they arrive as strings
    for e in edges:
        e["is_road"] = bool(e["is_road"])
        e["is_water"] = bool(e["is_water"])

    validate_area(area_name, nodes, edges)

    G = _build_graph(nodes, edges)
    return G, nodes, edges


def _build_graph(nodes: dict, edges: list) -> nx.Graph:
    G = nx.Graph()
    for name, info in nodes.items():
        G.add_node(name, zone=info["zone"], pos=info.get("pos"),
                   max_capacity=info.get("max_capacity"))
    for e in edges:
        u, v = e["u"], e["v"]
        key = "|".join(sorted([u, v]))
        G.add_edge(u, v,
                   key=key,
                   is_road=e["is_road"],
                   is_water=e["is_water"],
                   length=e["length"],
                   closed=False)
    return G


def validate_area(area_name: str, nodes: dict, edges: list) -> None:
    """Fail loudly if the area data is self-inconsistent."""
    node_set = set(nodes)

    for e in edges:
        for side in ("u", "v"):
            if e[side] not in node_set:
                raise ValueError(
                    f"[{area_name}] Edge {e['u']}|{e['v']} references "
                    f"unknown node '{e[side]}'"
                )

    for name, info in nodes.items():
        cap = info.get("max_capacity")
        if cap is not None and cap <= 0:
            raise ValueError(
                f"[{area_name}] Shelter '{name}' has non-positive capacity {cap}"
            )


# ── Helpers ────────────────────────────────────────────────────────────────

def edge_key(u: str, v: str) -> str:
    return "|".join(sorted([u, v]))


def get_edge(G: nx.Graph, u: str, v: str) -> Optional[dict]:
    return G[u][v] if G.has_edge(u, v) else None


# ── Agent dataclass ────────────────────────────────────────────────────────

@dataclass
class Agent:
    name: str
    kind: str           # "land" | "boat"
    capacity: int
    speed: int
    node: str
    next_node: Optional[str] = None
    route: List[str] = field(default_factory=list)
    edge_left: int = 0
    busy_left: int = 0
    status: str = "IDLE"   # IDLE | MOVING | LOADING | UNLOADING | FAILED
    victims: int = 0
    commitment: Optional[str] = None
    idle_ticks: int = 0
    failed: bool = False

    def is_idle(self) -> bool:
        return self.status == "IDLE" and not self.failed

    def snapshot(self) -> dict:
        return {
            "node": self.node,
            "next_node": self.next_node,
            "status": self.status,
            "victims": self.victims,
            "commitment": self.commitment,
            "edge_left": self.edge_left,
            "busy_left": self.busy_left,
        }


# ── Travel cost ────────────────────────────────────────────────────────────

def travel_ticks(agent: Agent, edge_data: dict) -> int:
    return math.ceil(edge_data["length"] / agent.speed)


# ── Shortest path ──────────────────────────────────────────────────────────

def shortest_path(G: nx.Graph, agent: Agent, src: str, dst: str,
                  use_belief: bool = False, belief_state=None,
                  tick: int = 0
                  ) -> Optional[Tuple[int, List[str]]]:
    """Dijkstra over only the edges the agent may traverse (filtered subgraph).
    Returns (cost_ticks, [node, node, ...]) or None if unreachable.
    use_belief=True: use belief_closed() for closed check (decentral).
    use_belief=False: use edge['closed'] truth (central).
    """
    if src == dst:
        return (0, [src])

    def passable(u, v, data) -> bool:
        closed = (belief_state.belief_closed(edge_key(u, v), tick) if use_belief and belief_state
                  else data.get("closed", False))
        if closed:
            return False
        if agent.kind == "land":
            return data["is_road"] and not data["is_water"]
        else:  # boat
            return data["is_water"]

    H = nx.Graph()
    H.add_nodes_from(G.nodes())
    for u, v, data in G.edges(data=True):
        if passable(u, v, data):
            H.add_edge(u, v, **data)

    try:
        path = nx.dijkstra_path(H, src, dst,
                                weight=lambda u, v, data: travel_ticks(agent, data))
        cost = sum(travel_ticks(agent, H[path[i]][path[i + 1]])
                   for i in range(len(path) - 1))
        return (cost, path)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


# ── SafetyController ───────────────────────────────────────────────────────

class SafetyController:
    violations: List[dict] = []

    @classmethod
    def reset(cls):
        cls.violations = []

    @classmethod
    def check_traversal(cls, G: nx.Graph, agent: Agent, u: str, v: str, tick: int) -> Tuple[bool, str]:
        """Check truth-based traversal permission. Appends to violations if rejected."""
        data = get_edge(G, u, v)
        if data is None:
            reason = "no such edge"
            cls.violations.append({"tick": tick, "type": "traversal",
                                    "agent": agent.name, "edge": edge_key(u, v), "reason": reason})
            return False, reason

        if data.get("closed", False):
            reason = "edge closed"
            cls.violations.append({"tick": tick, "type": "traversal",
                                    "agent": agent.name, "edge": edge_key(u, v), "reason": reason})
            return False, reason

        if agent.kind == "land" and data["is_water"]:
            reason = "land team on water edge"
            cls.violations.append({"tick": tick, "type": "traversal",
                                    "agent": agent.name, "edge": edge_key(u, v), "reason": reason})
            return False, reason

        if agent.kind == "boat" and not data["is_water"]:
            reason = "boat on dry road edge"
            cls.violations.append({"tick": tick, "type": "traversal",
                                    "agent": agent.name, "edge": edge_key(u, v), "reason": reason})
            return False, reason

        return True, "ok"

    @classmethod
    def check_payload(cls, agent: Agent, victims: int, tick: int) -> Tuple[bool, str]:
        if victims > agent.capacity:
            reason = "payload over capacity"
            cls.violations.append({"tick": tick, "type": "payload",
                                    "agent": agent.name, "reason": reason})
            return False, reason
        return True, "ok"

    @classmethod
    def route_violations(cls) -> int:
        return sum(1 for v in cls.violations if v["type"] == "traversal")

    @classmethod
    def payload_violations(cls) -> int:
        return sum(1 for v in cls.violations if v["type"] == "payload")


# ── Self-check ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    G, _, _ = load_area("muj")
    print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
    assert G.number_of_nodes() == 17, f"Expected 17 nodes, got {G.number_of_nodes()}"
    assert G.number_of_edges() == 23, f"Expected 23 edges, got {G.number_of_edges()}"
    for u, v, d in G.edges(data=True):
        assert "is_road" in d and "is_water" in d, f"Edge {u}-{v} missing bool attrs"

    SafetyController.reset()
    land = Agent("LAND-T", "land", 10, 1, "DOME")
    boat = Agent("BOAT-T", "boat", 8, 2, "FLOOD_POND")

    ok, r = SafetyController.check_traversal(G, land, "PARKING_2", "DOME", 0)
    assert not ok and r == "land team on water edge", f"Test 2 failed: {r}"

    ok, r = SafetyController.check_traversal(G, boat, "DOME", "VIP_ENTRY", 0)
    assert not ok and r == "boat on dry road edge", f"Test 3 failed: {r}"

    ok, r = SafetyController.check_traversal(G, land, "FLOOD_POND", "GHS_HOSTEL", 0)
    assert not ok and r == "land team on water edge", f"Test 4 failed: {r}"

    ok, r = SafetyController.check_payload(boat, 9, 0)
    assert not ok and r == "payload over capacity", f"Test 5 failed: {r}"

    assert len(SafetyController.violations) == 4, f"Test 6 failed: {len(SafetyController.violations)} violations"

    print("All Phase 1 acceptance tests PASSED.")
