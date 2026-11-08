"""Generate the pixel-art backgrounds for Keyboard Warrior: a sky, a skyline and a street, in a dusk and a day version.
scene-*.svg is the header band (sky, moon or sun, skyline, street); city-*.svg is the skyline and street only, for the page
background. Both tile sideways. Plain rectangles on a 160-unit grid, so they stay crisp at any size."""
import os
import random

OUT = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)

THEMES = {
    "arcade": dict(sky=["#141a3a", "#19214a", "#1f2a58", "#273463"], star="#aeb6e0", orb="#e6dfc4", orb_notch="#19214a",
                   far="#2b3566", near="#1a2146", lit="#c9a24e", unlit="#2c3669", walk="#10142b", road="#0c1024", dash="#3a4580", cloud=None),
    "light": dict(sky=["#b6d6ec", "#c6e1f2", "#d6eaf6", "#e4f2fa"], star=None, orb="#fff0b0", orb_notch=None,
                  far="#a7bdd5", near="#8ba3c2", lit="#e9f1f8", unlit="#a3b8d0", walk="#6f7f98", road="#5a6980", dash="#e5ebf3", cloud="#f6fafd"),
}
FAR = [(4, 14, 18), (20, 10, 24), (32, 16, 14), (50, 12, 22), (64, 18, 16), (84, 10, 25), (96, 16, 19), (114, 12, 14), (128, 18, 23), (148, 10, 17)]
NEAR = [(0, 12, 12), (14, 14, 16), (30, 10, 11), (42, 16, 18), (60, 12, 12), (74, 14, 17), (90, 10, 12), (102, 18, 15), (122, 12, 18), (136, 12, 11), (150, 10, 14)]
STARS = [(10, 4), (24, 9), (41, 3), (57, 11), (70, 5), (88, 8), (103, 3), (141, 10), (152, 5), (33, 13)]
ORB = [(2, 4), (1, 6), (0, 8), (0, 8), (0, 8), (0, 8), (1, 6), (2, 4)]  # (indent, width) per row of an 8x8 disc


def rect(x, y, w, h, fill):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"/>'


def build(theme, kind):
    t = THEMES[theme]
    ground = 12
    sky_h = 40 if kind == "scene" else 28
    H = sky_h + ground
    top_ground = H - ground
    rng = random.Random(7)
    parts = []
    if kind == "scene":
        bands = [(0, 14), (14, 10), (24, 8), (32, 8)]
        for (y, h), color in zip(bands, t["sky"]):
            parts.append(rect(0, y, 160, h, color))
        for x, y in STARS:
            if t["star"]:
                parts.append(rect(x, y, 1, 1, t["star"]))
        ox, oy = 126, 5
        for row, (indent, width) in enumerate(ORB):
            parts.append(rect(ox + indent, oy + row, width, 1, t["orb"]))
        if t["orb_notch"]:  # a crescent
            for row, (indent, width) in enumerate(ORB):
                parts.append(rect(ox + indent + 2, oy + row, max(0, width - 2), 1, t["sky"][0]))
        if t["cloud"]:
            for cx, cy in ((18, 8), (74, 5), (108, 14)):
                parts += [rect(cx, cy, 10, 2, t["cloud"]), rect(cx + 2, cy - 1, 6, 1, t["cloud"])]
    for layer, color in ((FAR, t["far"]), (NEAR, t["near"])):
        for x, w, h in layer:
            parts.append(rect(x, top_ground - h, w, h, color))
    for x, w, h in NEAR:
        for wx in range(x + 2, x + w - 2, 4):
            for wy in range(top_ground - h + 3, top_ground - 3, 4):
                parts.append(rect(wx, wy, 2, 2, t["lit"] if rng.random() < 0.2 else t["unlit"]))
    parts.append(rect(0, top_ground, 160, 4, t["walk"]))
    parts.append(rect(0, top_ground + 4, 160, ground - 4, t["road"]))
    for x in range(2, 160, 16):
        parts.append(rect(x, top_ground + 8, 8, 1, t["dash"]))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 {H}" width="160" height="{H}" shape-rendering="crispEdges">'
            + "".join(parts) + "</svg>\n")


for theme in THEMES:
    for kind in ("scene", "city"):
        path = os.path.join(OUT, f"{kind}-{theme}.svg")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(build(theme, kind))
        print(path, os.path.getsize(path), "bytes")
