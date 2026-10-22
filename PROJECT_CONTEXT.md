# MargSetu: Full Project Context

This file is the complete spec. Build from it. If a chat message conflicts with it, this file wins unless the user changes it here.

---

## 1. The brief

**Problem.** Flood response plans become stale as routes close, shelters fill and reports conflict. Coordinate rescue teams, boats, shelters and incident reports as autonomous agents with partial information, resource contention and different urgency levels.

**Implementation.**
- Dynamic re-planning over a changing road/water graph.
- Uncertainty-tagged reports (source, confidence 0.0 to 1.0, timestamp), with temporal decay.
- Capacity constraints.
- Fairness criteria across neighbourhoods.
- Explicit safety invariants.
- Compare decentralised negotiation with a centralised baseline.

**Evaluation.** Time to service, unmet requests, resource idle time, route violations, fairness across neighbourhoods, recovery after contradictory updates.

**Stretch.** Trigger simultaneous bridge closure, boat failure and shelter overflow. Show which commitments are cancelled, who is re-assigned, and why.

**Minimum deliverable.** A time-stepped simulation, injected events, an assignment log, reroute explanations and an after-action replay. Synthetic locations and scenarios only, no live feeds.

**Brief's phase plan.**
1. Dual-layer graph (tagged edges, zones) and SafetyController gates.
2. Agent types (RescueBoat, LandRescueTeam), assignment state machine, centralised Hungarian dispatcher.
3. Belief engine, CBBA-inspired bidding, contract-net shelter handshake.
4. 30-tick loop, at least two scenarios, Tick 15 chaos, reroute JSON logs.
5. Replay UI, final metrics, Jain's fairness index.

---

## 2. Decisions (fixed, do not revisit)

- **Synthetic campus.** Nodes are placed on a schematic layout, not real geography. No OSM, OSMnx, OSRM, waypoints or map drawing until the simulation passes every acceptance test.
- **Single source of truth** for nodes and edges: `muj_campus.py`. The node list and edge list in `graph.py` are stale. Delete them and import from `muj_campus.py`.
- **Deterministic.** No randomness anywhere. Same input, same output.
- **Units.** Time is in ticks. Edge length is in abstract units.
- **Shelters:** DOME, KNOWLEDGE_CENTRE, GHS_HOSTEL.
- **Fairness zone** of an incident = zone of its node.
- **Decentralised dispatch** is a single-round greedy auction, not full CBBA. State this in the report.

---

## 3. Files

```
margsetu/
  muj_campus.py       NODES, EDGES (exists; the only node/edge source)
  graph.py            Graph, Agent dataclass, SafetyController (edit: drop its own NODES/EDGES)
  scenarios.py        S1 and S2 definitions (incidents, overlays, events, agents, shelters)
  belief.py           Claim, belief state, decay
  dispatch.py         cost(), handshake(), central_dispatch(), decentral_dispatch()
  sim.py              tick loop, commitments, cancellations, ledger writer, CLI
  metrics.py          metrics from ledger.json, Jain's index, writes metrics.json
  compare.py          table across all four runs
  replay.html         slider replay over ledger.json (no map)
  out/                generated output, one folder per run
  README.md           overview, run commands, simplifications
```

`muj_campus.py` also has a `pos` key on each node (pixel positions). The simulation ignores it. It exists only for an optional map later.

---

## 4. Campus graph

### 4.1 Nodes (17)

| Node | Zone | Shelter max capacity | Notes |
|---|---|---|---|
| AB1 | Academic | - | |
| AB2 | Academic | - | |
| AB3 | Academic | - | |
| LHC | Academic | - | |
| KNOWLEDGE_CENTRE | Academic | 150 | shelter |
| DOME | Admin | 300 | shelter |
| VIP_ENTRY | Admin | - | |
| OLD_MESS | Campus | - | |
| PARKING_1 | Campus | - | |
| PARKING_2 | Campus | - | |
| CIVIL_WS | Campus | - | |
| GARDEN | Campus | - | |
| FOOTBALL | Sports | - | |
| CRICKET | Sports | - | |
| BASKETBALL | Sports | - | |
| GHS_HOSTEL | Hostels | 120 | shelter |
| FLOOD_POND | Water | - | synthetic, boat-only |

A node is a shelter iff it has `max_capacity`.

### 4.2 Edges (23)

Each edge has `is_road`, `is_water` and `length`.

**Length rule:** road and flooded edges have length 3. Water-only edges have length 2.

| # | Edge | is_road | is_water | length | Usable by |
|---|---|---|---|---|---|
| 1 | GHS_HOSTEL-PARKING_1 | T | F | 3 | land |
| 2 | PARKING_1-AB1 | T | F | 3 | land |
| 3 | PARKING_1-AB3 | T | F | 3 | land |
| 4 | AB3-LHC | T | F | 3 | land |
| 5 | LHC-CRICKET | T | F | 3 | land |
| 6 | LHC-FOOTBALL | T | F | 3 | land |
| 7 | CRICKET-BASKETBALL | T | F | 3 | land |
| 8 | BASKETBALL-CIVIL_WS | T | F | 3 | land |
| 9 | CIVIL_WS-AB2 | T | F | 3 | land |
| 10 | AB2-KNOWLEDGE_CENTRE | T | F | 3 | land |
| 11 | KNOWLEDGE_CENTRE-OLD_MESS | T | F | 3 | land |
| 12 | OLD_MESS-FOOTBALL | T | F | 3 | land |
| 13 | OLD_MESS-LHC | T | F | 3 | land |
| 14 | OLD_MESS-AB1 | T | F | 3 | land |
| 15 | AB1-DOME | T | F | 3 | land |
| 16 | KNOWLEDGE_CENTRE-DOME | T | F | 3 | land |
| 17 | DOME-VIP_ENTRY | T | F | 3 | land |
| 18 | PARKING_2-GARDEN | T | F | 3 | land |
| 19 | GARDEN-GHS_HOSTEL | T | F | 3 | land |
| 20 | PARKING_2-DOME | T | T | 3 | boat only (flooded road) |
| 21 | GHS_HOSTEL-FLOOD_POND | F | T | 2 | boat only |
| 22 | GARDEN-FLOOD_POND | F | T | 2 | boat only |
| 23 | PARKING_2-FLOOD_POND | F | T | 2 | boat only |

**Canonical edge key:** the two node names sorted alphabetically and joined with `|`, e.g. `AB1|PARKING_1`. Use this key everywhere (belief claims, ledger, edge state).

**Base reachability (no overlays):**
- Land can reach all nodes except FLOOD_POND.
- Boats can reach FLOOD_POND, GHS_HOSTEL, GARDEN, PARKING_2 and DOME (via the flooded edge).

---

## 5. Edge state, permissions and cost

### 5.1 Edge state

Each edge has:
- `is_road`, `is_water`, `length`: static, from `muj_campus.py`. Overlays may set `is_water = True` on a road (creating a flooded road).
- `closed`: bool, default `False`. Set only by events. A closed edge is impassable for everyone.

### 5.2 Permissions (truth)

- **Land team** may use an edge iff `is_road and not is_water and not closed`.
- **Boat** may use an edge iff `is_water and not closed`.

### 5.3 Permissions (belief)

The decentralised agents use the same rules with `closed` replaced by `belief_closed(edge)`. See section 9.

### 5.4 Travel cost

`travel_ticks(agent, edge) = ceil(length / agent.speed)`

- Land speed 1: road edge = 3 ticks.
- Boat speed 2: flooded edge = 2 ticks, water edge = 1 tick.

### 5.5 Shortest path

Dijkstra over the edges the agent may use under the chosen knowledge (truth for central, belief for decentral), weighted by `travel_ticks`. Returns `(cost_ticks, path_of_nodes)` or `None` if unreachable.

---

## 6. Agents

| Name | Kind | Capacity | Speed | Start node |
|---|---|---|---|---|
| LAND-1 | land | 10 | 1 | DOME |
| LAND-2 | land | 10 | 1 | GHS_HOSTEL |
| LAND-3 | land | 10 | 1 | PARKING_1 |
| BOAT-1 | boat | 8 | 2 | FLOOD_POND |
| BOAT-2 | boat | 8 | 2 | FLOOD_POND |

### 6.1 Agent fields

`name, kind, capacity, speed, node, next_node, route (list of nodes still to visit), edge_left (int), busy_left (int), status, victims (int), commitment (id or None), idle_ticks (int), failed (bool)`

### 6.2 Status values

- `IDLE`: no movement, no action. Counts toward idle time.
- `MOVING`: on an edge (`edge_left > 0`).
- `LOADING`: 1 tick at an incident node.
- `UNLOADING`: 1 tick at a shelter.
- `FAILED`: permanently out of service.

### 6.3 Rules

- An agent has at most one active commitment at a time.
- `node` is always the last node reached. While on an edge, `node` is the departure node until arrival.
- A cancelled agent finishes the edge it is on, then stops at the far end.

---

## 7. Shelters and incidents

### 7.1 Shelters

| Name | Max | Occupied (start) | Reserved (start) |
|---|---|---|---|
| DOME | 300 | 0 | 0 |
| KNOWLEDGE_CENTRE | 150 | 0 | 0 |
| GHS_HOSTEL | 120 | 0 | 0 |

`remaining() = max - occupied - reserved` (can go negative after overflow).

- `reserved` = passengers confirmed by the handshake but not yet unloaded.
- `occupied` = passengers unloaded.

### 7.2 Incident fields

`id, node, remaining (victims not yet loaded), urgency (1 to 3), origin_id, origin_zone, first_arrival_tick (None), delivered (count delivered from this record)`

Initial incidents: `origin_id = id`, `origin_zone = zone of node`.

REPOOL incidents (created on agent failure): id `R1`, `R2`, ... inherit `origin_id` and `origin_zone` from the commitment's incident.

### 7.3 Unclaimed victims

`claimed(incident) = sum(passengers of active commitments with phase PICKUP on that incident)`

`unclaimed(incident) = incident.remaining - claimed(incident)`

---

## 8. Scenarios

Both scenarios share: ticks 0 to 30 inclusive, the base flooded edge PARKING_2-DOME (from `muj_campus.py`), the agents in section 6, the shelters in section 7.1, and the event schedule in section 13.

### 8.1 S1

**Overlays:** none beyond the base graph.

**Incidents:**

| ID | Node | Victims | Urgency | Zone |
|---|---|---|---|---|
| I1 | GARDEN | 12 | 3 | Campus |
| I2 | PARKING_2 | 6 | 2 | Campus |
| I3 | BASKETBALL | 8 | 1 | Sports |
| I4 | AB3 | 5 | 2 | Academic |

Total victims: 31.

**Requested victims by zone:** Campus 18, Sports 8, Academic 5.

### 8.2 S2

**Overlays:** GARDEN-GHS_HOSTEL becomes flooded (`is_water = True`, so it is now a flooded road).

**Incidents:**

| ID | Node | Victims | Urgency | Zone |
|---|---|---|---|---|
| I1 | GARDEN | 6 | 3 | Campus |
| I2 | FOOTBALL | 10 | 3 | Sports |
| I3 | VIP_ENTRY | 9 | 3 | Admin |
| I4 | CIVIL_WS | 7 | 2 | Campus |
| I5 | CRICKET | 4 | 1 | Sports |

Total victims: 36.

**Requested victims by zone:** Campus 13, Sports 14, Admin 9.

---

## 9. Belief engine

### 9.1 Claims

A report is a claim: `{source, edge_key, state, conf0, tick}`.
- `state` is `BLOCKED` or `CLEAR`. Only closure is believed. Flooding is static map knowledge from tick 0 and is not a claim.
- A report's `tick` is the tick it arrives at the belief layer.

### 9.2 Source confidence

| Source | conf0 |
|---|---|
| COMMAND | 1.0 |
| DRONE | 0.9 |
| VOLUNTEER | 0.4 |

### 9.3 Decay

`eff(claim, now) = conf0 * 0.9 ** (now - claim.tick)`

### 9.4 Belief

`belief(edge, now)` = the `state` of the claim with the highest `eff` at `now`. Ties go to the later tick. No claims means `CLEAR`.

`belief_closed(edge, now) = (belief(edge, now) == BLOCKED)`

### 9.5 Truth vs belief

- Truth: `edge.closed`, changed only by events.
- Central dispatcher uses truth (global oracle).
- Decentral agents use belief for planning.
- The SafetyController always checks truth. This is how a decentral agent with a wrong belief gets a route violation.

### 9.6 False reports and recovery

A report is **false** if its `state` disagrees with truth at its tick.

For each false report at tick `r` on edge `e`:
- `corrected_tick` = first tick `t >= r` at which `belief(e, t) == truth(e, t)`, checked at the end of tick `t`.
- `recovery = corrected_tick - r`.
- If not corrected by tick 30, mark it `uncorrected` and exclude it from the mean.

---

## 10. Commitments and the shelter handshake

### 10.1 Commitment fields

`id ("C-0001", ...), agent, incident (incident id), shelter (None until handshake), passengers, phase (PICKUP | DELIVER), state (PROPOSED | COMMITTED | COMPLETED | CANCELLED), created_tick, history [(tick, state, reason)]`

### 10.2 State transitions

| From | To | When | Reason string |
|---|---|---|---|
| (new) | PROPOSED | dispatch selects agent and incident | "Proposed by {mode} dispatch." |
| PROPOSED | COMMITTED | handshake succeeds | "CONFIRM_RESERVATION at {shelter}." |
| PROPOSED | CANCELLED | handshake fails | "No shelter with RemainingCapacity >= Passengers." |
| COMMITTED | COMPLETED | unload finishes | "Delivered {n} to {shelter}." |
| COMMITTED | CANCELLED | any cancel (section 10.4) | see 10.4 |

Phase changes from PICKUP to DELIVER when loading finishes. The commitment stays COMMITTED.

### 10.3 Handshake (atomic, per commitment)

```
handshake(c):
    for s in shelters, sorted by knowledge-cost from c's start node, ascending:
        if s.remaining() >= c.passengers:          # check and reserve in one step
            s.reserved += c.passengers
            c.shelter = s.name
            transition(c, COMMITTED, "CONFIRM_RESERVATION at " + s.name + ".")
            return True
        log_reject(c, s, "REJECT at " + s.name + ": RemainingCapacity=" + str(s.remaining()) + " < Passengers=" + str(c.passengers))
    transition(c, CANCELLED, "No shelter with RemainingCapacity >= Passengers.")
    return False
```

For a PICKUP commitment, the start node is the incident node. For a DELIVER commitment, it is the agent's node.

### 10.4 Cancel

```
cancel(c, tick, reason):
    c.state = CANCELLED; history.append((tick, CANCELLED, reason))
    if c.shelter and c.state_was_committed:
        shelters[c.shelter].reserved -= c.passengers
    agent = agents[c.agent]
    agent.commitment = None
    agent.route = []                 # finish current edge, then stop
    log reroute entry (section 15.2) with action CANCELLED and this reason
```

If the agent still has `victims > 0`, it needs a new DELIVER commitment (step 4a).

Commitments with `agent.busy_left > 0` (currently loading or unloading) are never voided by step 3.

### 10.5 Agent failure

When an agent fails (`FAILED`):
1. Cancel all its active commitments with reason `"Agent failed: {name}. Contract revoked."`.
2. If it carried victims, create a REPOOL incident at `agent.node` with `remaining = victims`, inheriting `origin_id` and `origin_zone` from the carried commitment's incident. Set `victims = 0`.
3. Set `status = FAILED`, `failed = True`.

---

## 11. Dispatch

### 11.1 Cost (shared by both modes)

```
BIG = 10000

cost(agent, incident, knowledge):
    d1 = shortest(agent, from agent.node to incident.node, knowledge)
    d2 = min over shelters s reachable from incident.node of shortest(agent, from incident.node to s.node, knowledge)
    if d1 is None or d2 is None:
        return BIG
    return (d1.cost + d2.cost) / incident.urgency
```

Shelter capacity is ignored in cost. The handshake enforces it.

### 11.2 Central dispatcher (oracle)

Runs at tick t if there is at least one idle non-failed agent and at least one incident with `unclaimed > 0`.

1. Rows: idle non-failed agents, sorted by name. Columns: incidents with `unclaimed > 0`, sorted by id.
2. `M[i][j] = cost(agent_i, incident_j, TRUTH)`.
3. `pairs = scipy.optimize.linear_sum_assignment(M)` (rectangular matrix is allowed).
4. For each `(i, j)` with `M[i][j] < BIG`:
   - Create PROPOSED commitment with `passengers = min(agent.capacity, unclaimed(incident_j))`.
   - Run handshake. If COMMITTED, set `agent.commitment` and `agent.route = shortest path (agent.node to incident, TRUTH)`.
5. One agent per incident per dispatch round.

### 11.3 Decentral dispatcher (greedy auction, belief-based)

Runs at tick t, after step 4a.

1. For each idle non-failed agent `a` and each incident `i` with `unclaimed > 0`: compute `bid = cost(a, i, BELIEF)`.
2. Build the list of `(bid, a.name, i.id)` with `bid < BIG`. Sort ascending by bid, then by name, then by id.
3. `awarded_agents = {}`, `awarded_incidents = {}`.
4. For each `(bid, a, i)` in order:
   - Skip if `a` is in `awarded_agents`, or `i` is in `awarded_incidents`, or `unclaimed(i) == 0`.
   - Create PROPOSED commitment with `passengers = min(agent.capacity, unclaimed(i))`.
   - Add `a` and `i` to the award sets.
   - Run handshake. If COMMITTED, set `agent.commitment` and `agent.route = shortest path (agent.node to incident, BELIEF)`.
5. One agent per incident per dispatch round.

This is a single-round greedy auction. It is not full CBBA. It does not iterate until consensus.

### 11.4 Carrying agents (both modes, step 4a)

For each non-failed agent with `victims > 0` and `commitment is None`:
1. Create DELIVER commitment with `passengers = victims`.
2. Handshake with shelters sorted by `shortest(agent.node to shelter)`, using TRUTH for central and BELIEF for decentral.
3. If COMMITTED, set `agent.route` to that path. If CANCELLED, the agent waits and retries next tick.

---

## 12. Tick loop (exact order)

```
for t in 0..30:
    # 1. EVENTS
    apply every event scheduled for tick t (section 13), in the order listed.
    truth changes: edge.closed, agent.status, shelter.occupied.
    reports: append claim {source, edge_key, state, conf0, tick=t} to belief.

    # 2. BELIEF
    belief is computed on demand from claims at tick t (section 9). Nothing to do here.

    # 3. VOIDS
    a. for each agent with status FAILED that still has commitments:
           apply section 10.5.
    b. CENTRAL ONLY: for each COMMITTED commitment whose agent.busy_left == 0:
           if its remaining route (agent.route, plus the edge the agent is on) contains an edge with closed == True:
               cancel(c, t, "Route edge closed: {edge}. Contract revoked.")
           elif shelters[c.shelter].occupied + shelters[c.shelter].reserved > shelters[c.shelter].max:
               cancel(c, t, "Destination reached max capacity. Contract revoked.")
    c. DECENTRAL ONLY, when t == 15: for each COMMITTED commitment whose agent.busy_left == 0:
           cancel(c, t, "Tick 15 broadcast: commitments voided. Re-bidding.")

    # 4. DISPATCH
    a. carrying agents without commitment (section 11.4).
    b. central_dispatch (central mode) or decentral_dispatch (decentral mode) (section 11).

    # 5. MOVE AND ACT (agents processed in name order, skipping FAILED)
    if busy_left > 0:
        busy_left -= 1
        if busy_left == 0:
            if status == LOADING:               # finish pickup
                agent.victims = passengers
                incident.remaining -= passengers
                commitment.phase = DELIVER
                agent.route = shortest path (agent.node to shelter, knowledge)
                status = MOVING if route else IDLE
            elif status == UNLOADING:           # finish delivery
                shelter.occupied += agent.victims
                shelter.reserved -= agent.victims
                incident.delivered += agent.victims
                transition(commitment, COMPLETED, "Delivered {n} to {shelter}.")
                agent.victims = 0
                agent.commitment = None
                status = IDLE
    elif edge_left > 0:
        edge_left -= 1
        if edge_left == 0:
            agent.node = agent.next_node        # arrival
            if commitment and commitment.phase == PICKUP and agent.node == incident.node:
                incident.first_arrival_tick = incident.first_arrival_tick or t
    else:                                       # at a node, not moving
        if agent.route:
            nxt = agent.route[0]
            ok, reason = SafetyController.check_traversal(graph, agent, agent.node, nxt, t)   # TRUTH
            if not ok:
                if commitment: cancel(commitment, t, "Traversal rejected by SafetyController: {reason}. Contract revoked.")
                continue
            agent.route.pop(0)
            agent.next_node = nxt
            agent.edge_left = travel_ticks(agent, edge(agent.node, nxt))
            status = MOVING
        elif commitment and agent.node == target(commitment):      # target = incident node (PICKUP) or shelter node (DELIVER)
            agent.busy_left = 1
            status = LOADING if commitment.phase == PICKUP else UNLOADING
        elif commitment is None:
            status = IDLE

    # 6. ACCOUNTING
    for each non-FAILED agent: if status == IDLE: idle_ticks += 1.
    record end-of-tick belief vs truth for every edge (section 9.6).
    run invariant check (section 16.4).

    # 7. LEDGER
    write tick entry (section 15.1).
```

**Notes:**
- Arrival at a node takes effect in step 5. The next departure or action starts the following tick, so each intermediate node costs one tick of delay.
- A commitment cancelled in step 5 by a gate rejection does not change the agent's position. The agent stays at its node.

---

## 13. Events (exact schedule)

Apply in this order within a tick.

**Both scenarios:**

| Tick | Type | Detail |
|---|---|---|
| 5 | REPORT | source VOLUNTEER, edge `AB1\|PARKING_1`, state BLOCKED. **False** (truth is open). |
| 7 | REPORT | source DRONE, edge `AB1\|PARKING_1`, state CLEAR. Corrects the false report. |
| 15 | TRUTH | close edge `AB1\|DOME` (bridge closure). |
| 15 | TRUTH | BOAT-1 status = FAILED (applies section 10.5 in step 3a). |
| 15 | TRUTH | DOME.occupied = 300 (shelter overflow). |
| 15 | REPORT | source COMMAND, message "BOAT-1 failed", applied so every dispatcher excludes BOAT-1. |
| 17 | REPORT | source DRONE, edge `AB1\|DOME`, state BLOCKED. Belief learns the closure two ticks late. |

**S2 only (also):**

| Tick | Type | Detail |
|---|---|---|
| 9 | REPORT | source VOLUNTEER, edge `KNOWLEDGE_CENTRE\|DOME`, state BLOCKED. **False** (truth is open). |
| 11 | REPORT | source DRONE, edge `KNOWLEDGE_CENTRE\|DOME`, state CLEAR. Corrects it. |

**Why the lag at 15 to 17:** it gives decentral agents a window in which they can plan through the closed bridge. The gate then rejects the move, which produces real route violations. Without the lag, decentral mode would never violate.

**Why decentral voids everything at 15:** the brief requires decentralised agents to void `COMMITTED` states at Tick 15. Central mode only voids commitments the truth invalidates. This asymmetry is deliberate. Document it in the report.

---

## 14. SafetyController

Checks use **truth**, always.

### 14.1 Traversal

`check_traversal(graph, agent, u, v, tick)`:
1. Edge does not exist: reject, reason `"no such edge"`.
2. `edge.closed`: reject, reason `"edge closed"`.
3. `agent.kind == "land"` and `edge.is_water`: reject, reason `"land team on water edge"`.
4. `agent.kind == "boat"` and not `edge.is_water`: reject, reason `"boat on dry road edge"`.
5. Otherwise accept.

Every rejection appends to `violations`:
`{"tick": t, "type": "traversal", "agent": name, "edge": key, "reason": reason}`

### 14.2 Payload

`check_payload(agent, victims, tick)`:
- `victims > agent.capacity`: reject, reason `"payload over capacity"`. Appends `{"type": "payload", ...}`.

Passengers are capped at capacity during dispatch, so this gate never fires in normal runs. It must still work when called directly (acceptance test).

### 14.3 Metrics

- `route_violations` = count of `type == "traversal"`.
- `payload_violations` = count of `type == "payload"`.

---

## 15. Logging and output

### 15.1 ledger.json

```json
{
  "scenario": "S1",
  "mode": "central",
  "ticks": [
    {
      "tick": 0,
      "events": ["REPORT VOLUNTEER AB1|PARKING_1 BLOCKED"],
      "agents": {
        "LAND-1": {"node": "DOME", "next_node": null, "status": "IDLE", "victims": 0, "commitment": null, "edge_left": 0, "busy_left": 0}
      },
      "incidents": {
        "I1": {"node": "GARDEN", "remaining": 12, "urgency": 3, "origin_zone": "Campus", "claimed": 0, "delivered": 0}
      },
      "shelters": {
        "DOME": {"max": 300, "occupied": 0, "reserved": 0, "remaining": 300}
      },
      "edges": {
        "AB1|PARKING_1": {"truth_closed": false, "belief_closed": true}
      },
      "transitions": [
        {"commitment": "C-0001", "from": "PROPOSED", "to": "COMMITTED", "reason": "CONFIRM_RESERVATION at DOME."}
      ],
      "violations": [
        {"type": "traversal", "agent": "BOAT-1", "edge": "AB1|DOME", "reason": "edge closed"}
      ]
    }
  ],
  "reroute_explanations": [],
  "false_reports": [
    {"tick": 5, "edge": "AB1|PARKING_1", "corrected_tick": 7, "recovery_ticks": 2}
  ]
}
```

Tick entries cover ticks 0 to 30 inclusive, so 31 entries.

### 15.2 Reroute explanations

```json
{
  "tick": 15,
  "mode": "decentral",
  "commitment": "C-0004",
  "agent": "LAND-2",
  "incident": "I1",
  "shelter": "DOME",
  "action": "CANCELLED",
  "reason": "Tick 15 broadcast: commitments voided. Re-bidding.",
  "reassigned_to": "C-0009",
  "new_agent": "LAND-3",
  "new_shelter": "KNOWLEDGE_CENTRE"
}
```

- `reassigned_to`, `new_agent` and `new_shelter` are filled after step 4 of the same tick, from any new commitment on the same incident. They are `null` if none was created.

### 15.3 Exact reason strings

- `"Destination reached max capacity. Contract revoked."`
- `"Route edge closed: {edge}. Contract revoked."`
- `"Agent failed: {name}. Contract revoked."`
- `"Tick 15 broadcast: commitments voided. Re-bidding."`
- `"Traversal rejected by SafetyController: {reason}. Contract revoked."`
- `"No shelter with RemainingCapacity >= Passengers."`
- `"CONFIRM_RESERVATION at {shelter}."`
- `"REJECT at {shelter}: RemainingCapacity={r} < Passengers={p}"` (logged in violations, not a transition)

---

## 16. Metrics

Computed after tick 30 from `ledger.json`, written to `metrics.json`, and printed.

### 16.1 The five required metrics

| Metric | Definition |
|---|---|
| `time_to_service` | Mean of `first_arrival_tick` over the **initial** incidents that were reached. Report the count of reached and unreached. |
| `unmet_requests` | Sum of `remaining` over all incidents at tick 30, plus `victims` of all agents at tick 30. |
| `resource_idle_time` | Sum over non-failed agents of `idle_ticks`. |
| `route_violations` | Count of `type == "traversal"` in violations. |
| `recovery_ticks` | Mean of `recovery_ticks` over false reports that were corrected. Report the count of uncorrected. |

### 16.2 Fairness

For each origin zone `z` that has requested victims:
- `requested_z` = sum of initial incident victims whose `origin_zone == z`.
- `delivered_z` = sum of `delivered` over all incident records (initial and REPOOL) whose `origin_zone == z`.
- `x_z = delivered_z / requested_z`.

Jain's Fairness Index over the `n` zones with `requested_z > 0`:

`J = (sum x_z)^2 / (n * sum x_z^2)`

If `sum x_z^2 == 0`, set `J = 0.0`.

### 16.3 Output

`metrics.json` contains: the five metrics, `J`, each `x_z`, `requested_total`, `delivered_total`, `unmet_total`, `reroute_count`, `shelter_rejects`.

### 16.4 Invariant (check every tick)

`delivered_total + remaining_total + aboard_total == requested_total`

This must hold at the end of every tick. If it fails, the run is wrong. Stop and fix.

### 16.5 Expected sanity checks

- Central mode: `route_violations == 0` in both scenarios.
- Decentral mode: `route_violations >= 0`. Values above 0 are expected at ticks 15 to 17 and are the point.
- `recovery_ticks` is 2 for the tick 5 false report in both scenarios.

---

## 17. Replay UI

`replay.html`, single file, no libraries needed.

- File input to load `ledger.json`.
- An `<input type="range">` slider from 0 to 30.
- At the selected tick, show:
  - Agents: name, node, status, victims, commitment.
  - Incidents: id, node, remaining, claimed, delivered.
  - Shelters: name, occupied, reserved, remaining.
  - Edges where `truth_closed != belief_closed`, highlighted.
  - Events for this tick.
  - Transitions for this tick.
  - Violations for this tick.
  - Reroute explanations for this tick.
- No map. Plain tables.

Optional later: draw a schematic SVG using `pos` from `muj_campus.py`, with straight lines between nodes. Only if everything else is done.

---

## 18. CLI and run commands

```
python sim.py --scenario S1 --mode central       -> out/S1_central/ledger.json
python sim.py --scenario S1 --mode decentral     -> out/S1_decentral/ledger.json
python sim.py --scenario S2 --mode central       -> out/S2_central/ledger.json
python sim.py --scenario S2 --mode decentral     -> out/S2_decentral/ledger.json

python metrics.py out/S1_central                 -> out/S1_central/metrics.json
python metrics.py out/S1_decentral               -> out/S1_decentral/metrics.json
python metrics.py out/S2_central                 -> out/S2_central/metrics.json
python metrics.py out/S2_decentral               -> out/S2_decentral/metrics.json

python compare.py                                -> prints a 4-column table
open replay.html                                 -> load out/S1_decentral/ledger.json
```

Also add `--no-chaos` to `sim.py`. It skips every event at tick 15 and later, for the Phase 2 test.

---

## 19. Acceptance tests

Each phase is done only when its tests pass.

### Phase 1: graph and gates
1. `graph.py` loads `NODES` and `EDGES` from `muj_campus.py`. There are 17 nodes and 23 edges. Every edge has boolean `is_road` and `is_water`.
2. Land team on `PARKING_2|DOME` (flooded) is rejected with `"land team on water edge"`.
3. Boat on `DOME|VIP_ENTRY` is rejected with `"boat on dry road edge"`.
4. Land team on `FLOOD_POND|GHS_HOSTEL` is rejected.
5. Boat with `victims=9` and `capacity=8` is rejected by `check_payload`.
6. Every rejection appears in `violations`.

### Phase 2: agents and central dispatch
7. With `--no-chaos`, a run in central mode produces zero route violations.
8. At tick 0 in S1, at least one commitment reaches COMMITTED.
9. Every COMMITTED commitment has `passengers <= agent.capacity`.
10. Central dispatch never assigns a BOAT to an incident it cannot reach by water (cost is BIG, so no pair is created).

### Phase 3: belief and decentral dispatch
11. `belief("AB1|PARKING_1", 5)` is BLOCKED. `belief("AB1|PARKING_1", 7)` is CLEAR.
12. Decay: a VOLUNTEER claim at tick 5 has `eff = 0.4 * 0.9**(now-5)`. At tick 7, `eff = 0.324`.
13. When DOME `remaining() < passengers`, the handshake logs a `REJECT` and uses the next shelter.
14. The auction assigns at most one agent per incident per round.

### Phase 4: simulation and chaos
15. Each run writes `ledger.json` with exactly 31 tick entries (0 to 30).
16. Each run has at least one reroute explanation at tick 15. If none, the scenario is too quiet. Fix the scenario. Do not loosen the rule.
17. In decentral mode, every COMMITTED commitment at tick 15 is cancelled with the broadcast reason.
18. Central mode: no route violations at any tick, including 15 to 17.
19. The invariant in section 16.4 holds at every tick in all four runs.

### Phase 5: metrics and replay
20. `metrics.json` contains the five metrics, `J`, and `x_z` for each zone.
21. Jain's index unit checks: `x = [1, 1, 1]` gives `J = 1.0`. `x = [1, 0, 0]` gives `J = 1/3`.
22. `compare.py` prints four columns with the same metric rows.
23. `replay.html` slider from 0 to 30 updates all tables.

---

## 20. Build order

Each step produces a file you can open. Finish one before starting the next.

1. **`graph.py`**: import from `muj_campus.py`, add `closed` to edges, implement `SafetyController` (section 14). Tests 1 to 6.
2. **`scenarios.py`**: S1 and S2 as data (section 8, 13).
3. **`sim.py` + `dispatch.py`, central only, no chaos**: tick loop (section 12), central dispatch, handshake, cancel. Tests 7 to 10.
4. **`belief.py`**: claims and decay. Tests 11 and 12.
5. **Decentral dispatch**: auction, decentral mode. Tests 13 and 14.
6. **Chaos at tick 15 and 17**: section 13. Tests 15 to 19.
7. **`metrics.py` and `compare.py`**: section 16. Tests 20 to 22.
8. **`replay.html`**: section 17. Test 23.
9. **`README.md`**: overview, run commands, simplifications (section 21).

---

## 21. Known simplifications (state these in the report)

- Decentralised dispatch is a single-round greedy auction, not full CBBA. No consensus iteration.
- One agent per incident per dispatch round, in both modes.
- Belief models only road closures. Flooding is static map knowledge.
- Load and unload take 1 tick each. Each intermediate node adds 1 tick of delay.
- Edge lengths are abstract. Road edges are length 3 and water-only edges are length 2.
- Everything is deterministic. No randomness.
- Centralised baseline is an oracle with perfect truth. It is an upper bound, not a realistic system.
- Decentral mode voids all commitments at Tick 15 (per brief). Central mode voids only truth-invalidated commitments. This asymmetry is by design.
- The campus is schematic and synthetic. Node positions and the flood pond are invented.
