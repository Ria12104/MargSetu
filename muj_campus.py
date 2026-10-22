"""MUJ synthetic campus for MargSetu.

Positions are (x, y) pixel estimates on the satellite image (roughly 776 x 735).
Edges are (u, v, is_road, is_water):
  road only   -> land teams only
  water only  -> boats only (FLOOD_POND is synthetic: the real map has no lake)
  road+water  -> flooded road: boats only

Run this file to draw the graph over the satellite image:
  python muj_campus.py satellite.png
"""

import sys
import math
import networkx as nx

NODES = {
    # Academic blocks — main rectangular building cluster, centre-left of image
    "AB1":              {"zone": "Academic", "pos": (255, 390)},
    "AB2":              {"zone": "Academic", "pos": (370, 320)},
    "AB3":              {"zone": "Academic", "pos": (220, 280)},
    "LHC":              {"zone": "Academic", "pos": (270, 240)},
    "KNOWLEDGE_CENTRE": {"zone": "Academic", "pos": (390, 385), "max_capacity": 150},

    # Admin — Dome / main admin building and VIP gate south of campus
    "DOME":             {"zone": "Admin",   "pos": (320, 460), "max_capacity": 300},
    "VIP_ENTRY":        {"zone": "Admin",   "pos": (355, 545)},

    # Campus facilities
    "OLD_MESS":         {"zone": "Campus",  "pos": (315, 355)},
    "PARKING_1":        {"zone": "Campus",  "pos": (175, 430)},
    "PARKING_2":        {"zone": "Campus",  "pos": (250, 500)},
    "CIVIL_WS":         {"zone": "Campus",  "pos": (460, 240)},
    "GARDEN":           {"zone": "Campus",  "pos": (290, 565)},

    # Sports — cricket ground (round green) is visible top-centre of campus
    "FOOTBALL":         {"zone": "Sports",  "pos": (340, 195)},
    "CRICKET":          {"zone": "Sports",  "pos": (295, 145)},  # the green circle
    "BASKETBALL":       {"zone": "Sports",  "pos": (430, 135)},

    # Hostels — GHS Hostel visible on left side
    "GHS_HOSTEL":       {"zone": "Hostels", "pos": (165, 375), "max_capacity": 120},

    # Synthetic flood zone — low ground south-west of garden
    "FLOOD_POND":       {"zone": "Water",   "pos": (195, 560)},
}

R, W = True, True
EDGES = [
    # ── Roads ──────────────────────────────────────────────────────────────
    ("GHS_HOSTEL",       "PARKING_1",        True,  False),
    ("PARKING_1",        "AB1",              True,  False),
    ("PARKING_1",        "AB3",              True,  False),
    ("AB3",              "LHC",              True,  False),
    ("LHC",              "CRICKET",          True,  False),
    ("LHC",              "FOOTBALL",         True,  False),
    ("CRICKET",          "BASKETBALL",       True,  False),
    ("BASKETBALL",       "CIVIL_WS",         True,  False),
    ("CIVIL_WS",         "AB2",              True,  False),
    ("AB2",              "KNOWLEDGE_CENTRE", True,  False),
    ("KNOWLEDGE_CENTRE", "OLD_MESS",         True,  False),
    ("OLD_MESS",         "FOOTBALL",         True,  False),
    ("OLD_MESS",         "LHC",              True,  False),
    ("OLD_MESS",         "AB1",              True,  False),
    ("AB1",              "DOME",             True,  False),
    ("KNOWLEDGE_CENTRE", "DOME",             True,  False),
    ("DOME",             "VIP_ENTRY",        True,  False),
    ("PARKING_2",        "GARDEN",           True,  False),
    ("GARDEN",           "GHS_HOSTEL",       True,  False),

    # ── Flooded road: southern route from Parking 2 to Dome ───────────────
    ("PARKING_2",        "DOME",             True,  True),

    # ── Water / boat-only ─────────────────────────────────────────────────
    ("GHS_HOSTEL",       "FLOOD_POND",       False, True),
    ("GARDEN",           "FLOOD_POND",       False, True),
    ("PARKING_2",        "FLOOD_POND",       False, True),
]


# ── Pixel distance helper ──────────────────────────────────────────────────

def _pixel_dist(a: str, b: str) -> float:
    ax, ay = NODES[a]["pos"]
    bx, by = NODES[b]["pos"]
    return math.hypot(bx - ax, by - ay)


# ── Graph builder (used by main.py) ───────────────────────────────────────

def build_campus_graph() -> nx.Graph:
    """
    Return a NetworkX Graph with:
      node attrs : zone, pos, max_capacity (if shelter)
      edge attrs : road (bool), water (bool),
                   weight (pixel distance, proxy for travel cost),
                   status ('open' | 'flooded'), confidence (float)
    """
    G = nx.Graph()

    for name, info in NODES.items():
        G.add_node(name, **info)

    for u, v, road, water in EDGES:
        dist = _pixel_dist(u, v)
        status = "flooded" if (road and water) else ("water" if water else "open")
        G.add_edge(u, v,
                   road=road,
                   water=water,
                   weight=round(dist),          # pixel distance ≈ travel cost
                   status=status,
                   confidence=1.0)

    return G


# ── Landmark helpers (mirrors old muj_graph.py API) ───────────────────────

def get_landmark_nodes(G: nx.Graph) -> dict:
    """Return a dict of landmark_name -> node_name for shelter & admin nodes."""
    return {
        n: n for n, d in G.nodes(data=True)
        if d.get("zone") in ("Admin", "Hostels", "Academic")
    }


# ── Overlay visualiser ────────────────────────────────────────────────────

def draw_overlay(image_path: str, out_path: str = "satellite_marked.png") -> str:
    """Draw the graph on top of the satellite image and save."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ImportError("Pillow is required: pip install Pillow")

    img = Image.open(image_path).convert("RGB")
    d   = ImageDraw.Draw(img)

    # Try a slightly larger font; fall back to default if unavailable
    try:
        font = ImageFont.truetype("arial.ttf", 11)
    except Exception:
        font = ImageFont.load_default()

    # Edges
    for u, v, road, water in EDGES:
        if road and water:
            color, lw = (255, 60,  60),  4   # flooded road — red
        elif water:
            color, lw = ( 30, 160, 255), 3   # water / boat-only — blue
        else:
            color, lw = (255, 220,  0),  3   # passable road — yellow
        d.line([NODES[u]["pos"], NODES[v]["pos"]], fill=color, width=lw)

    # Nodes
    ZONE_COLORS = {
        "Academic": (100, 200, 255),
        "Admin":    (255, 140,   0),
        "Campus":   (200, 200, 200),
        "Sports":   ( 80, 220,  80),
        "Hostels":  (220, 100, 220),
        "Water":    ( 30, 100, 255),
    }
    for name, info in NODES.items():
        x, y = info["pos"]
        zone  = info.get("zone", "Campus")
        dot   = ZONE_COLORS.get(zone, (255, 255, 255))
        r     = 7 if "max_capacity" in info else 5
        d.ellipse([x - r, y - r, x + r, y + r], fill=dot, outline=(0, 0, 0), width=1)
        d.text((x + 9, y - 7), name, fill=(255, 255, 255), font=font)

    # Legend
    legend = [
        ((255, 220,  0),   "Road (passable)"),
        ((255,  60, 60),   "Flooded road (boat)"),
        (( 30, 160, 255),  "Water / boat-only"),
    ]
    lx, ly = 6, 6
    for color, label in legend:
        d.rectangle([lx, ly, lx + 18, ly + 10], fill=color)
        d.text((lx + 22, ly), label, fill=(255, 255, 255), font=font)
        ly += 16

    img.save(out_path)
    print(f"Saved → {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) > 1:
        draw_overlay(sys.argv[1])
    else:
        G = build_campus_graph()
        print(f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        for n, d in G.nodes(data=True):
            cap = f"  [capacity={d['max_capacity']}]" if "max_capacity" in d else ""
            print(f"  {n:20} zone={d['zone']:10} pos={d['pos']}{cap}")
