# MargSetu: Autonomous Multi-Agent Flood Rescue Dispatch

**MargSetu** is an autonomous multi-agent disaster response coordination engine designed for dynamic flood rescue scenarios. As floodwaters rise, routes close, shelters reach capacity, and conflicting citizen reports arrive, static plans fail. MargSetu coordinates rescue teams (boats, land rescue units), shelters, and incident reports as autonomous agents operating with partial information, temporal uncertainty, resource contention, and safety invariants.

The system rigorously compares **Decentralized Negotiation (Auction / Contract-Net)** against a **Centralized Baseline (Oracle Hungarian Dispatch)** across fairness (Jain's index), time-to-service, unmet requests, and resilience to sudden environmental shocks ("Tick 15 chaos").

---

## 🏗️ System Architecture

- **Dual-Layer Graph (`muj_campus.py`, `graph.py`):**
  - Schematic network of 17 nodes and 23 edges with tagged modalities (`is_road`, `is_water`).
  - Graph-level `SafetyController` enforcing strict physical safety invariants (e.g., land vehicles blocked on water edges, boats blocked on dry road edges, payload capacity gates).
- **Belief Engine (`belief.py`):**
  - Manages uncertainty-tagged reports (`source`, `confidence` $\in [0, 1]$, `timestamp`).
  - Implements temporal exponential decay ($e^{-\lambda \Delta t}$) and evidence fusion for road closures.
- **Dispatchers (`dispatch.py`):**
  - **Centralized Dispatcher:** Optimal pairing via minimum-cost matching with global truth.
  - **Decentralized Dispatcher:** Single-round greedy auction mechanism with bilateral shelter capacity handshakes (Contract-Net protocol).
- **Simulation Engine (`sim.py`):**
  - Deterministic 31-tick loop ($t=0 \dots 30$).
  - Injects dynamic environmental events and "Tick 15 Chaos" (simultaneous bridge closures, vehicle failures, shelter saturation).
  - Maintains state machines: `PROPOSED` $\rightarrow$ `COMMITTED` $\rightarrow$ `COMPLETED` / `CANCELLED`.
  - Outputs an exhaustive audit ledger (`ledger.json`) recording agent positions, transitions, violations, and reroute explanations.
- **Evaluation & Metrics (`metrics.py`, `compare.py`):**
  - Computes average Time to Service (TTS), unmet requests, resource idle time, route violations, recovery ticks, and Jain's Fairness Index across campus zones.
- **Visual DVR Replay (`replay.html`):**
  - Interactive HTML5 canvas visualizer with scrubber controls for tick-by-tick inspection of agent movements, edge closures, shelter occupancy, and incident clearance.

---

## 🚀 Quickstart & Run Commands

### 1. Run Simulations
Simulate Scenarios S1 and S2 in both Centralized and Decentralized modes:

```bash
# Scenario 1
python sim.py --scenario S1 --mode central       # -> out/S1_central/ledger.json
python sim.py --scenario S1 --mode decentral     # -> out/S1_decentral/ledger.json

# Scenario 2
python sim.py --scenario S2 --mode central       # -> out/S2_central/ledger.json
python sim.py --scenario S2 --mode decentral     # -> out/S2_decentral/ledger.json
```

*(Use `--no-chaos` flag if running baseline without mid-simulation failures).*

### 2. Compute Metrics
```bash
python metrics.py out/S1_central
python metrics.py out/S1_decentral
python metrics.py out/S2_central
python metrics.py out/S2_decentral
```

### 3. Generate Comparative Table
```bash
python compare.py
```

### 4. Run Acceptance Tests
Verify all 39 acceptance criteria covering graph safety, dispatch validity, belief decay, chaos handling, and metric consistency:
```bash
python acceptance_tests.py
```

### 5. Interactive DVR Replay
Open `replay.html` in any web browser and load any generated `ledger.json` (e.g., from `out/S1_decentral/ledger.json`).

---

## 📊 Benchmark Results

| Metric | S1 Central | S1 Decentral | S2 Central | S2 Decentral |
| :--- | :---: | :---: | :---: | :---: |
| **Time to Service (avg ticks)** | 7.25 | 7.25 | 11.75 | 15.40 |
| **TTS Reached / Unreached** | 4 / 0 | 4 / 0 | 4 / 1 | 5 / 0 |
| **Unmet Requests** | 8 | 8 | 4 | 11 |
| **Delivered Total** | 23 | 23 | 32 | 25 |
| **Resource Idle Time** | 47 | 43 | 47 | 45 |
| **Route Violations** | 0 | 0 | 0 | 0 |
| **Recovery Ticks** | 2.0 | 2.0 | 2.0 | 2.0 |
| **Jain's Fairness Index** | 0.6667 | 0.6667 | 0.9783 | 0.9158 |
| **Reroute Explanations** | 3 | 3 | 3 | 3 |
| **Shelter Rejects** | 0 | 0 | 0 | 0 |

---

## 📁 Repository Structure

```
├── muj_campus.py       # Single source of truth for graph nodes, edges, positions, capacities
├── graph.py            # Graph loader, Agent model, SafetyController
├── scenarios.py        # Scenario definitions (S1, S2) with incidents, overlays, chaos events
├── belief.py           # Claim model, temporal decay, belief state fusion
├── dispatch.py         # Cost calculation, handshake, central & decentral dispatchers
├── sim.py              # Tick execution loop, commitment tracking, ledger output, CLI
├── metrics.py          # Metric calculations & Jain's fairness index
├── compare.py          # 4-run side-by-side terminal comparison table
├── acceptance_tests.py # Full 39-point acceptance test suite
├── replay.html         # Canvas-based DVR replay interface
├── out/                # Simulation run outputs (ledger.json & metrics.json per run)
└── PROJECT_CONTEXT.md  # Detailed specification & problem context
```

---

## ⚖️ Simplifications & Assumptions

- **Decentralized Dispatch:** Operates as a single-round greedy auction with contract-net shelter handshakes rather than multi-round consensus CBBA.
- **Assignment Model:** Up to one agent assigned per incident per dispatch tick.
- **Belief State:** Models dynamic road closures and temporal confidence decay; water levels and topological graph structure are treated as static base knowledge.
- **Deterministic:** All calculations are strictly deterministic to ensure repeatable benchmarks and regression testing.
- **Centralized Baseline:** Operates as an oracle with perfect knowledge of ground truth, serving as a theoretical upper bound.
