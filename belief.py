"""belief.py — Claims, belief state, temporal decay."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SOURCE_CONF = {
    "COMMAND":   1.0,
    "DRONE":     0.9,
    "VOLUNTEER": 0.4,
}


@dataclass
class Claim:
    source: str
    edge_key: str       # canonical sorted key e.g. "AB1|PARKING_1"
    state: str          # "BLOCKED" | "CLEAR"
    conf0: float
    tick: int           # tick the claim arrives

    def eff(self, now: int) -> float:
        """Effective confidence at tick `now`."""
        return self.conf0 * (0.9 ** (now - self.tick))


class BeliefState:
    """Maintains all claims and answers belief queries."""

    def __init__(self):
        self._claims: List[Claim] = []

    def add_claim(self, source: str, edge_key: str, state: str, tick: int):
        conf0 = SOURCE_CONF.get(source, 0.5)
        self._claims.append(Claim(source, edge_key, state, conf0, tick))

    def belief(self, edge_key: str, now: int) -> str:
        """
        Return 'BLOCKED' or 'CLEAR' at tick `now`.
        Only considers claims that arrived at or before `now`.
        Highest eff wins; ties go to later tick.
        No claims → 'CLEAR'.
        """
        relevant = [c for c in self._claims
                    if c.edge_key == edge_key and c.tick <= now]
        if not relevant:
            return "CLEAR"
        best = max(relevant, key=lambda c: (c.eff(now), c.tick))
        return best.state

    def belief_closed(self, edge_key: str, now: int) -> bool:
        return self.belief(edge_key, now) == "BLOCKED"

    def all_claims(self) -> List[Claim]:
        return list(self._claims)

    def claims_for(self, edge_key: str) -> List[Claim]:
        return [c for c in self._claims if c.edge_key == edge_key]


# ── False-report analysis (called after sim ends) ─────────────────────────

def analyse_false_reports(belief: BeliefState, truth_log: Dict[str, Dict[int, bool]],
                           max_tick: int = 30) -> List[dict]:
    """
    For each claim where state disagrees with truth at claim.tick,
    find the corrected_tick (first t >= claim.tick where belief == truth).
    truth_log: {edge_key: {tick: closed_bool}}
    """
    results = []
    seen = set()   # avoid duplicate reports for the same (edge, tick, state)

    for c in belief.all_claims():
        if c.state not in ("BLOCKED", "CLEAR"):
            continue
        truth_at_tick = truth_log.get(c.edge_key, {}).get(c.tick, False)
        claim_says_closed = (c.state == "BLOCKED")
        if claim_says_closed == truth_at_tick:
            continue   # not a false report

        key = (c.edge_key, c.tick, c.state)
        if key in seen:
            continue
        seen.add(key)

        corrected_tick = None
        for t in range(c.tick, max_tick + 1):
            truth_t = truth_log.get(c.edge_key, {}).get(t, False)
            belief_blocked = belief.belief_closed(c.edge_key, t)
            if belief_blocked == truth_t:
                corrected_tick = t
                break

        entry = {
            "tick": c.tick,
            "edge": c.edge_key,
            "corrected_tick": corrected_tick,
            "recovery_ticks": (corrected_tick - c.tick) if corrected_tick is not None else None,
        }
        results.append(entry)

    return results
