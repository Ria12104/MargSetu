"""dispatch.py — Cost function, handshake, central and decentral dispatch."""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
import copy

if TYPE_CHECKING:
    import networkx as nx
    from graph import Agent, SafetyController
    from scenarios import Incident, Shelter
    from belief import BeliefState

# Sentinel incident id used for DELIVER commitments created by dispatch_carrying
# sim.py knows to look up the incident differently when it sees this prefix
_DELIVER_PREFIX = "DELIVER:"   # "DELIVER:<real_incident_id>"


def _effective_node(agent) -> str:
    """The node the agent will be at when it is next free to act.
    While mid-edge, that is next_node; otherwise it is node.
    """
    return agent.next_node if agent.next_node else agent.node

BIG = 10_000

# Global commitment counter
_commit_counter = [0]
_repool_counter = [0]


def next_commit_id() -> str:
    _commit_counter[0] += 1
    return f"C-{_commit_counter[0]:04d}"


def next_repool_id() -> str:
    _repool_counter[0] += 1
    return f"R{_repool_counter[0]}"


def reset_counters():
    _commit_counter[0] = 0
    _repool_counter[0] = 0


# ── Commitment ─────────────────────────────────────────────────────────────

class Commitment:
    def __init__(self, agent_name, incident_id, passengers, phase="PICKUP"):
        self.id = next_commit_id()
        self.agent = agent_name
        self.incident = incident_id
        self.shelter: Optional[str] = None
        self.passengers = passengers
        self.phase = phase          # "PICKUP" | "DELIVER"
        self.state = "PROPOSED"     # PROPOSED | COMMITTED | COMPLETED | CANCELLED
        self.created_tick: int = 0
        self.history: List[Tuple[int, str, str]] = []

    def transition(self, tick: int, new_state: str, reason: str):
        self.history.append((tick, new_state, reason))
        self.state = new_state


# ── unclaimed helper ───────────────────────────────────────────────────────

def unclaimed(incident, commitments: Dict[str, Commitment]) -> int:
    claimed = sum(
        c.passengers for c in commitments.values()
        if c.incident == incident.id and c.phase == "PICKUP"
        and c.state in ("PROPOSED", "COMMITTED")
    )
    return max(0, incident.remaining - claimed)


# ── Cost function ──────────────────────────────────────────────────────────

def cost(agent, incident, G, shelters: Dict[str, "Shelter"],
         use_belief: bool = False, belief_state=None, tick: int = 0) -> float:
    from graph import shortest_path

    d1 = shortest_path(G, agent, agent.node, incident.node,
                       use_belief=use_belief, belief_state=belief_state, tick=tick)
    if d1 is None:
        return BIG

    best_d2 = None
    for sh in shelters.values():
        d2 = shortest_path(G, agent, incident.node, sh.name,
                           use_belief=use_belief, belief_state=belief_state, tick=tick)
        if d2 is not None:
            if best_d2 is None or d2[0] < best_d2:
                best_d2 = d2[0]

    if best_d2 is None:
        return BIG

    return (d1[0] + best_d2) / incident.urgency


# ── Handshake ──────────────────────────────────────────────────────────────

def handshake(commitment: Commitment, agent, G, shelters: Dict[str, "Shelter"],
              tick: int, reroute_log: list, violations_log: list,
              use_belief: bool = False, belief_state=None) -> bool:
    from graph import shortest_path

    start_node = commitment.incident  # incident node name for PICKUP
    # Sort shelters by travel cost from incident node
    ranked = []
    for sh in shelters.values():
        r = shortest_path(G, agent, start_node, sh.name,
                          use_belief=use_belief, belief_state=belief_state, tick=tick)
        ranked.append((r[0] if r else BIG, sh))
    ranked.sort(key=lambda x: x[0])

    for _, sh in ranked:
        if sh.remaining() >= commitment.passengers:
            sh.reserved += commitment.passengers
            commitment.shelter = sh.name
            commitment.transition(tick, "COMMITTED",
                                  f"CONFIRM_RESERVATION at {sh.name}.")
            return True
        else:
            violations_log.append(
                f"REJECT at {sh.name}: RemainingCapacity={sh.remaining()} < Passengers={commitment.passengers}"
            )

    commitment.transition(tick, "CANCELLED",
                          "No shelter with RemainingCapacity >= Passengers.")
    return False


# ── Cancel ─────────────────────────────────────────────────────────────────

def cancel(commitment: Commitment, agents: dict, shelters: Dict[str, "Shelter"],
           tick: int, reason: str, reroute_log: list):
    was_committed = commitment.state == "COMMITTED"
    commitment.transition(tick, "CANCELLED", reason)
    if commitment.shelter and was_committed:
        shelters[commitment.shelter].reserved -= commitment.passengers

    agent = agents[commitment.agent]
    agent.commitment = None
    agent.route = []   # finish current edge then stop

    entry = {
        "tick": tick,
        "mode": None,   # filled by caller
        "commitment": commitment.id,
        "agent": agent.name,
        "incident": commitment.incident,
        "shelter": commitment.shelter,
        "action": "CANCELLED",
        "reason": reason,
        "reassigned_to": None,
        "new_agent": None,
        "new_shelter": None,
    }
    reroute_log.append(entry)
    return entry


# ── Carrying agents (step 4a) ──────────────────────────────────────────────

def dispatch_carrying(agents: dict, shelters: dict, commitments: dict, G,
                      tick: int, reroute_log: list, violations_log: list,
                      use_belief: bool, belief_state):
    from graph import shortest_path
    for agent in sorted(agents.values(), key=lambda a: a.name):
        if agent.failed or agent.victims == 0 or agent.commitment is not None:
            continue
        # Use the incident id the agent was carrying (set by sim during LOADING)
        carried_iid = getattr(agent, 'carrying_incident_id', None) or "_unknown_"
        c = Commitment(agent.name, carried_iid, agent.victims, phase="DELIVER")
        c.created_tick = tick
        # find nearest shelter by path cost from effective position
        eff = _effective_node(agent)
        ranked = []
        for sh in shelters.values():
            r = shortest_path(G, agent, eff, sh.name,
                              use_belief=use_belief, belief_state=belief_state, tick=tick)
            ranked.append((r[0] if r else BIG, sh))
        ranked.sort(key=lambda x: x[0])

        committed = False
        for _, sh in ranked:
            if sh.remaining() >= c.passengers:
                sh.reserved += c.passengers
                c.shelter = sh.name
                c.transition(tick, "COMMITTED", f"CONFIRM_RESERVATION at {sh.name}.")
                committed = True
                break
            else:
                violations_log.append(
                    f"REJECT at {sh.name}: RemainingCapacity={sh.remaining()} < Passengers={c.passengers}"
                )

        if not committed:
            c.transition(tick, "CANCELLED", "No shelter with RemainingCapacity >= Passengers.")
            continue

        eff = _effective_node(agent)
        r = shortest_path(G, agent, eff, c.shelter,
                          use_belief=use_belief, belief_state=belief_state, tick=tick)
        if r:
            agent.route = list(r[1][1:])   # strip eff itself; agent will be there after current edge
        commitments[c.id] = c
        agent.commitment = c.id


# ── Central dispatch ───────────────────────────────────────────────────────

def central_dispatch(agents: dict, incidents: dict, shelters: dict, commitments: dict,
                     G, tick: int, reroute_log: list, violations_log: list):
    import scipy.optimize
    from graph import shortest_path

    idle_agents = sorted(
        [a for a in agents.values() if a.is_idle() and not a.failed],
        key=lambda a: a.name
    )
    open_incidents = sorted(
        [i for i in incidents.values() if unclaimed(i, commitments) > 0],
        key=lambda i: i.id
    )
    if not idle_agents or not open_incidents:
        return

    M = []
    for agent in idle_agents:
        row = []
        for inc in open_incidents:
            row.append(cost(agent, inc, G, shelters, use_belief=False, tick=tick))
        M.append(row)

    import numpy as np
    M_arr = np.array(M)
    row_idx, col_idx = scipy.optimize.linear_sum_assignment(M_arr)

    for ri, ci in zip(row_idx, col_idx):
        if M[ri][ci] >= BIG:
            continue
        agent = idle_agents[ri]
        inc   = open_incidents[ci]
        passengers = min(agent.capacity, unclaimed(inc, commitments))
        if passengers == 0:
            continue

        c = Commitment(agent.name, inc.id, passengers)
        c.created_tick = tick
        c.transition(tick, "PROPOSED", f"Proposed by central dispatch.")
        commitments[c.id] = c

        ok = handshake(c, agent, G, shelters, tick, reroute_log, violations_log,
                       use_belief=False)
        if ok:
            agent.commitment = c.id
            eff = _effective_node(agent)
            r = shortest_path(G, agent, eff, inc.node, use_belief=False, tick=tick)
            if r:
                agent.route = list(r[1][1:])


# ── Decentral dispatch ─────────────────────────────────────────────────────

def decentral_dispatch(agents: dict, incidents: dict, shelters: dict, commitments: dict,
                       G, tick: int, reroute_log: list, violations_log: list,
                       belief_state):
    from graph import shortest_path

    idle_agents = [a for a in agents.values() if a.is_idle() and not a.failed]
    open_incidents = [i for i in incidents.values() if unclaimed(i, commitments) > 0]
    if not idle_agents or not open_incidents:
        return

    bids = []
    for agent in idle_agents:
        for inc in open_incidents:
            b = cost(agent, inc, G, shelters, use_belief=True, belief_state=belief_state, tick=tick)
            if b < BIG:
                bids.append((b, agent.name, inc.id))

    bids.sort(key=lambda x: (x[0], x[1], x[2]))

    awarded_agents = set()
    awarded_incidents = set()

    for bid_val, aname, iid in bids:
        if aname in awarded_agents or iid in awarded_incidents:
            continue
        if unclaimed(incidents[iid], commitments) == 0:
            continue

        agent = agents[aname]
        inc   = incidents[iid]
        passengers = min(agent.capacity, unclaimed(inc, commitments))
        if passengers == 0:
            continue

        awarded_agents.add(aname)
        awarded_incidents.add(iid)

        c = Commitment(agent.name, inc.id, passengers)
        c.created_tick = tick
        c.transition(tick, "PROPOSED", "Proposed by decentral dispatch.")
        commitments[c.id] = c

        ok = handshake(c, agent, G, shelters, tick, reroute_log, violations_log,
                       use_belief=True, belief_state=belief_state)
        if ok:
            agent.commitment = c.id
            eff = _effective_node(agent)
            r = shortest_path(G, agent, eff, inc.node,
                              use_belief=True, belief_state=belief_state, tick=tick)
            if r:
                agent.route = list(r[1][1:])
