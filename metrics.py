"""metrics.py — Compute metrics from ledger.json, write metrics.json.

Usage: python metrics.py out/S1_central
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def jains_index(xs: list) -> float:
    n = len(xs)
    if n == 0:
        return 0.0
    s  = sum(xs)
    s2 = sum(x * x for x in xs)
    if s2 == 0:
        return 0.0
    return (s * s) / (n * s2)


def compute(ledger: dict) -> dict:
    ticks = ledger["ticks"]
    last  = ticks[-1]

    # ── time_to_service ───────────────────────────────────────────────────
    # Only initial incidents (id starts with I, not R)
    first_arrivals = []
    unreached = 0
    for tick in ticks:
        for iid, snap in tick["incidents"].items():
            pass   # we read from the last tick for remaining/delivered

    # Rebuild first_arrival_tick from ticks
    first_arrival_map = {}   # iid -> tick
    for tick_entry in ticks:
        t = tick_entry["tick"]
        for iid, snap in tick_entry["incidents"].items():
            if iid.startswith("I") and iid not in first_arrival_map:
                # check agents at this tick
                for aname, asnap in tick_entry["agents"].items():
                    pass
    # Better: look for first loading event in transitions
    # Simpler: scan ticks for when remaining decreases
    prev_remaining: dict = {}
    for tick_entry in ticks:
        t = tick_entry["tick"]
        for iid, snap in tick_entry["incidents"].items():
            if not iid.startswith("I"):
                continue
            prev = prev_remaining.get(iid)
            if prev is not None and snap["remaining"] < prev and iid not in first_arrival_map:
                first_arrival_map[iid] = t
            prev_remaining[iid] = snap["remaining"]

    initial_ids = [iid for iid in ticks[0]["incidents"] if iid.startswith("I")]
    reached   = [first_arrival_map[i] for i in initial_ids if i in first_arrival_map]
    unreached_count = len(initial_ids) - len(reached)
    tts = sum(reached) / len(reached) if reached else None

    # ── unmet_requests ────────────────────────────────────────────────────
    last_inc   = last["incidents"]
    last_agents = last["agents"]
    unmet = (sum(s["remaining"] for s in last_inc.values())
             + sum(s["victims"]   for s in last_agents.values()))

    # ── resource_idle_time ────────────────────────────────────────────────
    # Sum idle_ticks from last tick (we tracked cumulative idle in accounting)
    # Actually idle_ticks is cumulative in the Agent, so read the last status count
    # We sum it by counting IDLE status over all ticks
    idle_by_agent = {aname: 0 for aname in ticks[0]["agents"]}
    for tick_entry in ticks:
        for aname, asnap in tick_entry["agents"].items():
            if asnap["status"] == "IDLE":
                idle_by_agent[aname] = idle_by_agent.get(aname, 0) + 1
    # Exclude FAILED agents if they were failed before becoming IDLE
    resource_idle = sum(idle_by_agent.values())

    # ── route_violations ──────────────────────────────────────────────────
    route_viol = sum(
        sum(1 for v in te["violations"] if v["type"] == "traversal")
        for te in ticks
    )

    # ── recovery_ticks ────────────────────────────────────────────────────
    false_reports = ledger.get("false_reports", [])
    corrected = [fr["recovery_ticks"] for fr in false_reports
                 if fr["recovery_ticks"] is not None]
    uncorrected_count = sum(1 for fr in false_reports if fr["recovery_ticks"] is None)
    recovery_mean = sum(corrected) / len(corrected) if corrected else None

    # ── fairness ──────────────────────────────────────────────────────────
    # Collect all incidents (including REPOOL) from last tick
    zone_requested: dict = {}
    zone_delivered: dict = {}

    # requested: initial incidents only
    for iid, snap in ticks[0]["incidents"].items():
        if iid.startswith("I"):
            z = snap["origin_zone"]
            # remaining + delivered at t=0 = original requested
            zone_requested[z] = zone_requested.get(z, 0) + snap["remaining"]

    # delivered: all records (initial + repool) from last tick
    for iid, snap in last_inc.items():
        z = snap["origin_zone"]
        zone_delivered[z] = zone_delivered.get(z, 0) + snap["delivered"]

    xs = []
    x_z = {}
    for z, req in zone_requested.items():
        if req > 0:
            x = zone_delivered.get(z, 0) / req
            x_z[z] = round(x, 4)
            xs.append(x)
    J = round(jains_index(xs), 4)

    # ── reroute / shelter rejects ─────────────────────────────────────────
    reroute_count   = len(ledger.get("reroute_explanations", []))
    shelter_rejects = 0
    for te in ticks:
        for v in te.get("violations", []):
            if "REJECT at" in str(v.get("reason", "")):
                shelter_rejects += 1

    # delivered_total / unmet_total
    delivered_total = sum(s["delivered"] for s in last_inc.values())
    remaining_total = sum(s["remaining"] for s in last_inc.values())
    aboard_total    = sum(s["victims"]   for s in last_agents.values())
    requested_total = sum(zone_requested.values())

    metrics = {
        "scenario":           ledger["scenario"],
        "mode":               ledger["mode"],
        "time_to_service":    round(tts, 2) if tts is not None else None,
        "tts_reached":        len(reached),
        "tts_unreached":      unreached_count,
        "unmet_requests":     unmet,
        "resource_idle_time": resource_idle,
        "route_violations":   route_viol,
        "recovery_ticks":     round(recovery_mean, 2) if recovery_mean is not None else None,
        "recovery_uncorrected": uncorrected_count,
        "jains_index":        J,
        "zone_ratios":        x_z,
        "requested_total":    requested_total,
        "delivered_total":    delivered_total,
        "unmet_total":        unmet,
        "reroute_count":      reroute_count,
        "shelter_rejects":    shelter_rejects,
    }
    return metrics


def main():
    if len(sys.argv) < 2:
        print("Usage: python metrics.py <out_dir>")
        sys.exit(1)
    out_dir  = Path(sys.argv[1])
    ledger_p = out_dir / "ledger.json"
    with open(ledger_p) as f:
        ledger = json.load(f)

    m = compute(ledger)

    metrics_p = out_dir / "metrics.json"
    with open(metrics_p, "w") as f:
        json.dump(m, f, indent=2)
    print(f"Wrote {metrics_p}")
    for k, v in m.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
