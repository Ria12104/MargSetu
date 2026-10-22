"""compare.py — Print a 4-column comparison table across all runs."""
from __future__ import annotations
import json
from pathlib import Path

RUNS = [
    ("S1", "central"),
    ("S1", "decentral"),
    ("S2", "central"),
    ("S2", "decentral"),
]

METRIC_KEYS = [
    "time_to_service",
    "tts_reached",
    "tts_unreached",
    "unmet_requests",
    "resource_idle_time",
    "route_violations",
    "recovery_ticks",
    "recovery_uncorrected",
    "jains_index",
    "delivered_total",
    "reroute_count",
    "shelter_rejects",
]


def load(scenario, mode):
    p = Path("out") / f"{scenario}_{mode}" / "metrics.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def main():
    data = [(s, m, load(s, m)) for s, m in RUNS]
    headers = [f"{s}_{m}" for s, m, _ in data]
    col_w = max(len(h) for h in headers) + 2

    # Header row
    print(f"{'Metric':<28}" + "".join(f"{h:>{col_w}}" for h in headers))
    print("-" * (28 + col_w * len(headers)))

    for key in METRIC_KEYS:
        row = f"{key:<28}"
        for _, _, m in data:
            val = str(m[key]) if m and key in m else "N/A"
            row += f"{val:>{col_w}}"
        print(row)

    # Zone ratios
    all_zones = set()
    for _, _, m in data:
        if m:
            all_zones |= set(m.get("zone_ratios", {}).keys())
    for z in sorted(all_zones):
        row = f"  zone_ratio[{z}]:<{28-len(z)-15}"
        row = f"  zone_ratio[{z}]"
        row = f"{row:<28}"
        for _, _, m in data:
            val = str(m["zone_ratios"].get(z, "-")) if m else "N/A"
            row += f"{val:>{col_w}}"
        print(row)


if __name__ == "__main__":
    main()
