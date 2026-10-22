"""sim.py — 31-tick simulation loop, CLI entry point.

Usage:
    python sim.py --area muj --scenario S1 --mode central
    python sim.py --area muj --scenario S2 --mode decentral --no-chaos
    python sim.py --area toy --scenario S1 --mode central
    python sim.py --scenario S1 --mode central   # --area defaults to 'muj'
"""
from __future__ import annotations
import argparse
import copy
import json
import os
import math
from pathlib import Path
from typing import Dict, List, Optional

import networkx as nx

from graph import load_area, Agent, SafetyController, edge_key, get_edge, travel_ticks, shortest_path
from scenarios import load_scenario, Incident
from belief import BeliefState, analyse_false_reports
from dispatch import (
    Commitment, reset_counters, next_repool_id,
    dispatch_carrying, central_dispatch, decentral_dispatch,
    cancel, BIG, unclaimed
)

# ── Helpers ────────────────────────────────────────────────────────────────

def _edge_data(G: nx.Graph, u: str, v: str) -> Optional[dict]:
    return G[u][v] if G.has_edge(u, v) else None


def _target_node(c: Commitment, incidents: dict) -> str:
    """Return the node the agent should be heading toward for this commitment."""
    if c.phase == "PICKUP":
        return incidents[c.incident].node
    else:
        return c.shelter


# ── Main sim ───────────────────────────────────────────────────────────────

def run(scenario_name: str, mode: str, no_chaos: bool = False,
        area_name: str = "muj") -> dict:
    assert mode in ("central", "decentral"), f"Unknown mode {mode}"

    G, nodes, _ = load_area(area_name)
    scenario = load_scenario(area_name, scenario_name, G, nodes)
    G_sim = copy.deepcopy(G)

    # Apply scenario overlays at t=0
    for ek, fields in scenario.overlays.items():
        u, v = ek.split("|")
        if G_sim.has_edge(u, v):
            for fld, val in fields.items():
                G_sim[u][v][fld] = val

    G = G_sim
    agents = copy.deepcopy(scenario.agents)
    shelters = copy.deepcopy(scenario.shelters)
    incidents: Dict[str, Incident] = copy.deepcopy(scenario.incidents)
    commitments: Dict[str, Commitment] = {}
    belief = BeliefState()
    reroute_log: List[dict] = []
    violations_log: List[str] = []    # shelter REJECT messages
    SafetyController.reset()
    reset_counters()

    # truth_log for false-report analysis: edge_key -> {tick: closed}
    truth_log: Dict[str, Dict[int, bool]] = {}

    # requested_total for invariant
    requested_total = sum(i.remaining for i in incidents.values())

    tick_entries = []

    use_belief = (mode == "decentral")

    # group events by tick
    events_by_tick: Dict[int, list] = {}
    for ev in scenario.events:
        events_by_tick.setdefault(ev.tick, []).append(ev)

    for t in range(31):

        tick_events_log = []

        # ── 1. EVENTS ──────────────────────────────────────────────────────
        if not no_chaos or t < 15:
            tick_evs = events_by_tick.get(t, [])
        else:
            tick_evs = [ev for ev in events_by_tick.get(t, []) if ev.tick < 15]

        for ev in tick_evs:
            if ev.kind == "TRUTH_EDGE_CLOSE":
                u, v = ev.edge.split("|")
                if G.has_edge(u, v):
                    G[u][v]["closed"] = True
                tick_events_log.append(f"TRUTH_EDGE_CLOSE {ev.edge}")

            elif ev.kind == "TRUTH_AGENT_FAIL":
                a = agents.get(ev.agent_name)
                if a and not a.failed:
                    a.status = "FAILED"
                    a.failed = True
                tick_events_log.append(f"TRUTH_AGENT_FAIL {ev.agent_name}")

            elif ev.kind == "TRUTH_SHELTER_SET":
                sh = shelters.get(ev.shelter_name)
                if sh:
                    setattr(sh, ev.shelter_field, ev.shelter_value)
                tick_events_log.append(
                    f"TRUTH_SHELTER_SET {ev.shelter_name}.{ev.shelter_field}={ev.shelter_value}"
                )

            elif ev.kind == "REPORT":
                if ev.edge and ev.state:
                    belief.add_claim(ev.source, ev.edge, ev.state, t)
                    tick_events_log.append(
                        f"REPORT {ev.source} {ev.edge} {ev.state}"
                    )
                elif ev.message:
                    tick_events_log.append(f"REPORT {ev.source} \"{ev.message}\"")

        # Record truth for this tick for all edges
        for u, v, d in G.edges(data=True):
            ek = edge_key(u, v)
            truth_log.setdefault(ek, {})[t] = d.get("closed", False)

        # ── 2. BELIEF — computed on demand (nothing to do here) ────────────

        # ── 3. VOIDS ───────────────────────────────────────────────────────
        # 3a: agent failure
        for agent in list(agents.values()):
            if not agent.failed:
                continue
            for c in list(commitments.values()):
                if c.agent != agent.name or c.state not in ("PROPOSED", "COMMITTED"):
                    continue
                reason = f"Agent failed: {agent.name}. Contract revoked."
                entry = cancel(c, agents, shelters, t, reason, reroute_log)
                entry["mode"] = mode
                # repool
                if agent.victims > 0:
                    # find origin from carried commitment
                    orig_id   = incidents.get(c.incident, Incident("?","?",0,1,"?","?")).origin_id
                    orig_zone = incidents.get(c.incident, Incident("?","?",0,1,"?","?")).origin_zone
                    rid = next_repool_id()
                    incidents[rid] = Incident(rid, agent.node, agent.victims, 1,
                                              orig_id, orig_zone)
                    agent.victims = 0

        # 3b (central): void route-closed or shelter-overflow commitments
        if mode == "central":
            for c in list(commitments.values()):
                if c.state != "COMMITTED":
                    continue
                ag = agents[c.agent]
                if ag.busy_left > 0:
                    continue
                # check remaining route for closed edges
                route_nodes = [ag.node] + ag.route
                voided = False
                for i in range(len(route_nodes) - 1):
                    ed = _edge_data(G, route_nodes[i], route_nodes[i + 1])
                    if ed and ed.get("closed", False):
                        ek = edge_key(route_nodes[i], route_nodes[i + 1])
                        reason = f"Route edge closed: {ek}. Contract revoked."
                        entry = cancel(c, agents, shelters, t, reason, reroute_log)
                        entry["mode"] = mode
                        voided = True
                        break
                if voided:
                    continue
                # check shelter overflow
                if c.shelter:
                    sh = shelters[c.shelter]
                    if sh.occupied + sh.reserved > sh.max:
                        reason = "Destination reached max capacity. Contract revoked."
                        entry = cancel(c, agents, shelters, t, reason, reroute_log)
                        entry["mode"] = mode

        # 3c (decentral): broadcast void at tick 15
        if mode == "decentral" and t == 15:
            for c in list(commitments.values()):
                if c.state != "COMMITTED":
                    continue
                ag = agents[c.agent]
                if ag.busy_left > 0:
                    continue
                reason = "Tick 15 broadcast: commitments voided. Re-bidding."
                entry = cancel(c, agents, shelters, t, reason, reroute_log)
                entry["mode"] = mode

        # ── 4. DISPATCH ────────────────────────────────────────────────────
        # 4a: carrying agents
        dispatch_carrying(agents, shelters, commitments, G, t, reroute_log,
                          violations_log, use_belief, belief)
        # 4b: main dispatch
        if mode == "central":
            central_dispatch(agents, incidents, shelters, commitments, G,
                             t, reroute_log, violations_log)
        else:
            decentral_dispatch(agents, incidents, shelters, commitments, G,
                               t, reroute_log, violations_log, belief)

        # Fill reassigned_to in reroute entries created this tick
        # (match cancelled commitment's incident to a new COMMITTED commitment)
        for entry in reroute_log:
            if entry.get("tick") == t and entry.get("reassigned_to") is None:
                inc_id = entry.get("incident")
                for c2 in commitments.values():
                    if (c2.incident == inc_id and c2.state == "COMMITTED"
                            and c2.id != entry["commitment"]):
                        entry["reassigned_to"] = c2.id
                        entry["new_agent"]   = c2.agent
                        entry["new_shelter"] = c2.shelter
                        break

        # ── 5. MOVE AND ACT ────────────────────────────────────────────────
        transitions_this_tick = []

        for agent in sorted(agents.values(), key=lambda a: a.name):
            if agent.failed:
                continue

            c = commitments.get(agent.commitment) if agent.commitment else None

            if agent.busy_left > 0:
                agent.busy_left -= 1
                if agent.busy_left == 0:
                    if agent.status == "LOADING" and c:
                        # finish pickup
                        inc = incidents[c.incident]
                        agent.victims = c.passengers
                        agent.carrying_incident_id = c.incident   # remember for DELIVER
                        inc.remaining -= c.passengers
                        c.phase = "DELIVER"
                        # set route to shelter
                        r = shortest_path(G, agent, agent.node, c.shelter,
                                          use_belief=use_belief,
                                          belief_state=belief if use_belief else None, tick=t)
                        if r:
                            agent.route = list(r[1][1:])
                            agent.status = "MOVING"
                        else:
                            agent.status = "IDLE"
                    elif agent.status == "UNLOADING" and c:
                        # finish delivery
                        sh = shelters[c.shelter]
                        sh.occupied += agent.victims
                        sh.reserved -= agent.victims
                        # credit the carried incident (not the DELIVER commitment's incident field directly)
                        carried_iid = agent.carrying_incident_id or c.incident
                        if carried_iid in incidents:
                            incidents[carried_iid].delivered += agent.victims
                        c.transition(t, "COMPLETED",
                                     f"Delivered {agent.victims} to {c.shelter}.")
                        transitions_this_tick.append({
                            "commitment": c.id,
                            "from": "COMMITTED",
                            "to": "COMPLETED",
                            "reason": c.history[-1][2],
                        })
                        agent.victims = 0
                        agent.carrying_incident_id = None
                        agent.commitment = None
                        agent.status = "IDLE"

            elif agent.edge_left > 0:
                agent.edge_left -= 1
                if agent.edge_left == 0:
                    agent.node = agent.next_node
                    agent.next_node = None
                    if (c and c.phase == "PICKUP"
                            and agent.node == incidents.get(c.incident, Incident("","",0,1,"","")).node):
                        inc = incidents[c.incident]
                        if inc.first_arrival_tick is None:
                            inc.first_arrival_tick = t

            else:
                # at a node
                if agent.route:
                    nxt = agent.route[0]
                    # Guard: skip self-loop (should not happen but defensive)
                    if nxt == agent.node:
                        agent.route.pop(0)
                        continue
                    ok, reason = SafetyController.check_traversal(G, agent, agent.node, nxt, t)
                    if not ok:
                        if c:
                            reason2 = f"Traversal rejected by SafetyController: {reason}. Contract revoked."
                            entry = cancel(c, agents, shelters, t, reason2, reroute_log)
                            entry["mode"] = mode
                            c = None
                        continue
                    agent.route.pop(0)
                    agent.next_node = nxt
                    ed = _edge_data(G, agent.node, nxt)
                    agent.edge_left = travel_ticks(agent, ed)  # full ticks; arrives when hits 0
                    agent.status = "MOVING"

                elif c and agent.node == _target_node(c, incidents):
                    agent.busy_left = 1
                    if c.phase == "PICKUP":
                        agent.status = "LOADING"
                        ok, _ = SafetyController.check_payload(agent, c.passengers, t)
                    else:
                        agent.status = "UNLOADING"
                elif agent.commitment is None:
                    agent.status = "IDLE"

        # ── 6. ACCOUNTING ──────────────────────────────────────────────────
        for agent in agents.values():
            if not agent.failed and agent.status == "IDLE":
                agent.idle_ticks += 1

        # Record belief vs truth for edge entries
        all_edge_entries = {}
        for u, v, d in G.edges(data=True):
            ek = edge_key(u, v)
            tc = d.get("closed", False)
            bc = belief.belief_closed(ek, t)
            all_edge_entries[ek] = {"truth_closed": tc, "belief_closed": bc}

        # Collect all transitions
        for c in commitments.values():
            for (htick, hstate, hreason) in c.history:
                if htick == t and hstate != "PROPOSED":
                    # only log non-trivial transitions in the tick entry
                    trans = {
                        "commitment": c.id,
                        "from": _prev_state(c.history, hstate),
                        "to": hstate,
                        "reason": hreason,
                    }
                    if trans not in transitions_this_tick:
                        transitions_this_tick.append(trans)

        # Build incident snapshot with current claimed
        inc_snap = {}
        for iid, inc in incidents.items():
            snap = inc.snapshot()
            snap["claimed"] = sum(
                c.passengers for c in commitments.values()
                if c.incident == iid and c.phase == "PICKUP"
                and c.state in ("PROPOSED", "COMMITTED")
            )
            inc_snap[iid] = snap

        # Invariant check (§16.4)
        delivered_total = sum(i.delivered for i in incidents.values())
        remaining_total = sum(i.remaining for i in incidents.values())
        aboard_total    = sum(a.victims for a in agents.values())
        assert (delivered_total + remaining_total + aboard_total == requested_total), (
            f"Invariant violated at tick {t}: "
            f"delivered={delivered_total} + remaining={remaining_total} + aboard={aboard_total} "
            f"!= requested={requested_total}"
        )

        # ── 7. LEDGER ──────────────────────────────────────────────────────
        tick_entries.append({
            "tick": t,
            "events": tick_events_log,
            "agents": {a.name: a.snapshot() for a in agents.values()},
            "incidents": inc_snap,
            "shelters": {sh.name: sh.snapshot() for sh in shelters.values()},
            "edges": all_edge_entries,
            "transitions": transitions_this_tick,
            "violations": [v for v in SafetyController.violations if v["tick"] == t],
        })

    # ── Post-sim ───────────────────────────────────────────────────────────
    false_reports = analyse_false_reports(belief, truth_log, max_tick=30)

    ledger = {
        "scenario": scenario_name,
        "mode": mode,
        "ticks": tick_entries,
        "reroute_explanations": reroute_log,
        "false_reports": false_reports,
    }
    return ledger


def _prev_state(history, cur_state):
    """Return the state just before cur_state in the history list."""
    STATE_ORDER = ["PROPOSED", "COMMITTED", "COMPLETED", "CANCELLED"]
    idx = STATE_ORDER.index(cur_state) if cur_state in STATE_ORDER else -1
    if idx > 0:
        return STATE_ORDER[idx - 1]
    return "PROPOSED"


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MargSetu Flood Dispatch Simulation")
    parser.add_argument("--area", default="muj",
                        help="Area name under areas/ (default: muj)")
    parser.add_argument("--scenario", required=True,
                        help="Scenario name, e.g. S1 or S2")
    parser.add_argument("--mode", choices=["central", "decentral"], required=True)
    parser.add_argument("--no-chaos", action="store_true",
                        help="Suppress all chaos events at tick ≥15")
    args = parser.parse_args()

    ledger = run(args.scenario, args.mode, no_chaos=args.no_chaos, area_name=args.area)

    out_dir = Path("out") / f"{args.scenario}_{args.mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "ledger.json"
    with open(out_path, "w") as f:
        json.dump(ledger, f, indent=2)

    print(f"Wrote {out_path}  ({len(ledger['ticks'])} ticks)")
    vcount = sum(len(e["violations"]) for e in ledger["ticks"])
    rcount = len(ledger["reroute_explanations"])
    print(f"Route violations: {vcount}, Reroute explanations: {rcount}")

    # Quick sanity
    assert len(ledger["ticks"]) == 31, "Expected 31 tick entries"
    if not args.no_chaos:
        assert rcount >= 1, "Expected at least one reroute explanation at tick 15"
    print("OK")


if __name__ == "__main__":
    main()
