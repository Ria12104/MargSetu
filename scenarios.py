"""scenarios.py — Data classes + JSON scenario loader.

Entry point:  load_scenario(area_name, scenario_name, G, nodes) -> Scenario
              reads areas/<area>/scenarios/<name>.json.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ── Incident ───────────────────────────────────────────────────────────────

@dataclass
class Incident:
    id: str
    node: str
    remaining: int       # victims not yet loaded
    urgency: int         # 1-3
    origin_id: str       # always the original incident id
    origin_zone: str     # zone of node
    first_arrival_tick: Optional[int] = None
    delivered: int = 0

    def snapshot(self) -> dict:
        return {
            "node": self.node,
            "remaining": self.remaining,
            "urgency": self.urgency,
            "origin_zone": self.origin_zone,
            "claimed": 0,   # filled in by sim after commitments resolve
            "delivered": self.delivered,
        }


# ── Shelter ────────────────────────────────────────────────────────────────

@dataclass
class Shelter:
    name: str
    max: int
    occupied: int = 0
    reserved: int = 0

    def remaining(self) -> int:
        return self.max - self.occupied - self.reserved

    def snapshot(self) -> dict:
        return {
            "max": self.max,
            "occupied": self.occupied,
            "reserved": self.reserved,
            "remaining": self.remaining(),
        }


# ── Event ──────────────────────────────────────────────────────────────────

@dataclass
class Event:
    tick: int
    kind: str       # "TRUTH_EDGE_CLOSE" | "TRUTH_AGENT_FAIL" | "TRUTH_SHELTER_SET" | "REPORT"
    edge: Optional[str] = None
    agent_name: Optional[str] = None
    shelter_name: Optional[str] = None
    shelter_field: Optional[str] = None
    shelter_value: Optional[int] = None
    source: Optional[str] = None
    state: Optional[str] = None     # "BLOCKED" | "CLEAR"
    conf0: Optional[float] = None
    message: Optional[str] = None


# ── Scenario (runtime object) ──────────────────────────────────────────────

@dataclass
class Scenario:
    name: str
    incidents: Dict[str, Incident]
    overlays: Dict[str, dict]
    events: List[Event]
    agents: Dict[str, "Agent"]
    shelters: Dict[str, Shelter]


# ── JSON loader ────────────────────────────────────────────────────────────

_AREAS_DIR = Path(__file__).parent / "areas"


def load_scenario(area_name: str, scenario_name: str, G, nodes: dict) -> Scenario:
    """Read areas/<area>/scenarios/<scenario>.json and return a Scenario.

    Validates:
    - every incident node exists in G
    - every agent start node exists in G
    - every agent can reach at least one incident node
    - every shelter in the 'shelters' list has max_capacity > 0 in nodes.json
    """
    path = _AREAS_DIR / area_name / "scenarios" / f"{scenario_name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No scenario file for '{area_name}/{scenario_name}' at {path}"
        )
    with open(path) as f:
        data = json.load(f)

    # ─ Incidents ─
    incidents: Dict[str, Incident] = {}
    for rec in data["incidents"]:
        iid = rec["id"]
        if rec["node"] not in G:
            raise ValueError(
                f"[{area_name}/{scenario_name}] Incident {iid} references "
                f"unknown node '{rec['node']}'"
            )
        incidents[iid] = Incident(
            id=iid,
            node=rec["node"],
            remaining=rec["victims"],
            urgency=rec["urgency"],
            origin_id=iid,
            origin_zone=rec["zone"],
        )

    # ─ Agents ─
    from graph import Agent
    agents: Dict[str, Agent] = {}
    for rec in data["agents"]:
        if rec["start_node"] not in G:
            raise ValueError(
                f"[{area_name}/{scenario_name}] Agent '{rec['name']}' starts at "
                f"unknown node '{rec['start_node']}'"
            )
        agents[rec["name"]] = Agent(
            name=rec["name"],
            kind=rec["kind"],
            capacity=rec["capacity"],
            speed=rec["speed"],
            node=rec["start_node"],
        )

    # ─ Shelters ─
    # Explicit list in JSON preserves priority order; fall back to node scan.
    shelters: Dict[str, Shelter] = {}
    shelter_names = data.get("shelters")
    if shelter_names:
        for node_name in shelter_names:
            cap = nodes.get(node_name, {}).get("max_capacity")
            if not cap:
                raise ValueError(
                    f"[{area_name}/{scenario_name}] Shelter '{node_name}' listed in "
                    f"'shelters' but has no max_capacity in nodes.json"
                )
            shelters[node_name] = Shelter(node_name, cap)
    else:
        for node_name, info in nodes.items():
            cap = info.get("max_capacity")
            if cap:
                shelters[node_name] = Shelter(node_name, cap)

    # ─ Validate reachability ─
    import networkx as nx

    def _passable_land(u, v, d):
        return d.get("is_road", False) and not d.get("is_water", False) and not d.get("closed", False)

    def _passable_water(u, v, d):
        return d.get("is_water", False) and not d.get("closed", False)

    inc_nodes = {inc.node for inc in incidents.values()}
    for ag_name, agent in agents.items():
        fn = _passable_land if agent.kind == "land" else _passable_water
        H = nx.Graph()
        H.add_nodes_from(G.nodes())
        for u, v, d in G.edges(data=True):
            if fn(u, v, d):
                H.add_edge(u, v)
        reachable = set(nx.node_connected_component(H, agent.node)) if agent.node in H else set()
        if reachable.isdisjoint(inc_nodes):
            raise ValueError(
                f"[{area_name}/{scenario_name}] Agent '{ag_name}' at '{agent.node}' "
                f"cannot reach any incident node"
            )

    # ─ Overlays ─
    overlays: Dict[str, dict] = data.get("overlays", {})
    for fields in overlays.values():
        for k in list(fields):
            if k in ("is_road", "is_water", "closed"):
                fields[k] = bool(fields[k])

    # ─ Events ─
    events: List[Event] = [
        Event(
            tick=rec["tick"],
            kind=rec["kind"],
            edge=rec.get("edge"),
            agent_name=rec.get("agent_name"),
            shelter_name=rec.get("shelter_name"),
            shelter_field=rec.get("shelter_field"),
            shelter_value=rec.get("shelter_value"),
            source=rec.get("source"),
            state=rec.get("state"),
            conf0=rec.get("conf0"),
            message=rec.get("message"),
        )
        for rec in data.get("events", [])
    ]

    return Scenario(
        name=scenario_name,
        incidents=incidents,
        overlays=overlays,
        events=events,
        agents=agents,
        shelters=shelters,
    )
