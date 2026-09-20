#!/usr/bin/env python3
"""Draw the four README pictures, light and dark, and render a PNG copy of each.

Run from the repository root with `make readme-assets`, or directly:

    python3 docs/assets/build_assets.py            # SVGs only
    python3 docs/assets/build_assets.py --png      # SVGs and PNG copies

Every figure is written twice, once per theme, so the README can hand GitHub a
<picture> element and the drawing blends into the page on both themes. The two files
of a pair differ only in the palette block at the top, which is why they are generated
from one description rather than edited by hand.

Text is drawn in Schibsted Grotesk and IBM Plex Mono. An SVG loaded through an <img>
tag may not fetch a webfont, so a subset of each face is embedded in the file as a
base64 woff2. Both faces are SIL Open Font License 1.1; the notice is in
docs/assets/FONT-LICENSE.txt and the subsets live in docs/assets/fonts.

The PNG copies are rendered by headless Chrome through Playwright, which is the only
renderer here that honours the embedded fonts. Without it the SVGs are still written
and the PNG step is skipped with a message.
"""
from __future__ import annotations

import argparse
import base64
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FONT_DIR = HERE / "fonts"

# --------------------------------------------------------------------------- palette

THEMES = {
    "light": {
        "bg": "#ffffff",
        "card": "#ffffff",
        "soft": "#f4f5f8",
        "edge": "#d0d7de",
        "edge2": "#e4e7ec",
        "ink": "#0e1016",
        "ink2": "#5d6472",
        "accent": "#5b3df0",
        "accentSoft": "#f1eeff",
        "sure": "#15803d",
        "unsure": "#b45309",
        "rail": "#f0f1f4",
        "wire": "#b6bec8",
    },
    "dark": {
        "bg": "#0d1117",
        "card": "#12161d",
        "soft": "#191e27",
        "edge": "#30363d",
        "edge2": "#262b33",
        "ink": "#f0f2f6",
        "ink2": "#9aa2b2",
        "accent": "#9b85ff",
        "accentSoft": "#1d1b3a",
        "sure": "#4ade80",
        "unsure": "#fbbf24",
        "rail": "#161b23",
        "wire": "#454c56",
    },
}

SANS = "'Schibsted Grotesk', system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

TRACK_TIGHT = -0.035   # em, headline
TRACK_SNUG = -0.018    # em, section headings
TRACK_TEXT = -0.008    # em, body

# --------------------------------------------------------------------- text measuring

_sg_cache: dict[int, object] = {}
_WARNED: list[bool] = []


def _sg(weight: int):
    """The variable face instanced at one weight, for advance widths."""
    if weight not in _sg_cache:
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer

        font = TTFont(FONT_DIR / "sg-subset.woff2")
        instancer.instantiateVariableFont(font, {"wght": weight}, inplace=True)
        _sg_cache[weight] = font
    return _sg_cache[weight]


def sans_w(text: str, size: float, weight: int = 400, track: float = 0.0) -> float:
    """Width of `text` in Schibsted Grotesk, in user units."""
    try:
        font = _sg(weight)
    except Exception:              # no fonttools, fall back to a rough average
        if not _WARNED:
            _WARNED.append(True)
            print("fonttools is not installed, so box widths are estimated and text may "
                  "sit badly; pip install fonttools brotli", file=sys.stderr)
        return len(text) * size * 0.52 + track * size * len(text)
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    total = 0
    for ch in text:
        name = cmap.get(ord(ch))
        total += hmtx[name][0] if name else hmtx[".notdef"][0]
    return total / upem * size + track * size * len(text)


def mono_w(text: str, size: float) -> float:
    """IBM Plex Mono advances 600/1000 of an em for every glyph."""
    return len(text) * size * 0.6


# ------------------------------------------------------------------------- primitives


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def font_face_css() -> str:
    faces = []
    for name, file in (("Schibsted Grotesk", "sg-subset.woff2"), ("IBM Plex Mono", "pm-subset.woff2")):
        path = FONT_DIR / file
        if not path.exists():
            continue
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        weights = "400 700" if "sg" in file else "400"
        faces.append(
            f'@font-face{{font-family:"{name}";font-style:normal;font-weight:{weights};'
            f'font-display:block;src:url(data:font/woff2;base64,{b64}) format("woff2");}}'
        )
    return "".join(faces)


def card(x, y, w, h, t, r=12, fill=None, stroke=None, cls="") -> str:
    fill = fill or t["card"]
    stroke = stroke or t["edge"]
    c = f' class="{cls}"' if cls else ""
    return (
        f'<rect{c} x="{x + 0.5:g}" y="{y + 0.5:g}" width="{w - 1:g}" height="{h - 1:g}" '
        f'rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
    )


def text(x, y, s, size, fill, *, weight=400, mono=False, anchor="start", track=None, cls=""):
    fam = MONO if mono else SANS
    if track is None:
        track = 0.0
    ls = f' letter-spacing="{track:g}em"' if track else ""
    w = f' font-weight="{weight}"' if weight != 400 else ""
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    c = f' class="{cls}"' if cls else ""
    return (
        f'<text{c} x="{x:g}" y="{y:g}" font-family="{fam}" font-size="{size:g}"{w}{a}{ls} '
        f'fill="{fill}">{esc(s)}</text>'
    )


def chip_icon(x, y, size, colour, animate=False) -> str:
    """The one chip mark used by the hero and the board cards, drawn on a 44 unit grid."""
    s = size / 44.0
    pin_cls = ' class="pin"' if animate else ""
    return (
        f'<g transform="translate({x:g},{y:g}) scale({s:.4f})" stroke="{colour}" fill="none" '
        f'stroke-width="2.4">'
        f'<rect x="9" y="9" width="26" height="26" rx="6"/>'
        f'<circle{pin_cls} cx="22" cy="22" r="3.4" fill="{colour}" stroke="none"/>'
        f'<g stroke-linecap="round">'
        f'<path d="M16 3.5v5.5M28 3.5v5.5M16 35v5.5M28 35v5.5M3.5 16h5.5M3.5 28h5.5M35 16h5.5M35 28h5.5"/>'
        f'</g></g>'
    )


def svg_document(name, w, h, theme_name, title, desc, body, extra_css="") -> str:
    t = THEMES[theme_name]
    slug = f"{name}-{theme_name}"
    palette = "\n".join(f"      --{k}: {v};" for k, v in t.items())
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-labelledby="{slug}-title {slug}-desc">
  <title id="{slug}-title">{esc(title)}</title>
  <desc id="{slug}-desc">{esc(desc)}</desc>
  <style>
    /* One palette block. The {theme_name} half of the pair; docs/assets/build_assets.py
       writes both halves from the same drawing. */
    svg {{
{palette}
    }}
    .tnum {{ font-variant-numeric: tabular-nums; }}
{extra_css}
    @media (prefers-reduced-motion: reduce) {{
      .pin, .pipe, .travel {{ animation: none !important; }}
    }}
  </style>
  <defs>{font_face_css()}</defs>
{body}
</svg>
"""


# ------------------------------------------------------------------------------- hero

HERO_W, HERO_H = 960, 282
PITCH_1 = "A sentence goes in, a command with its slot values comes"
PITCH_2 = "out, on a microcontroller."
HERO_CHIPS = ["~250 KB model", "~5 ms on an ESP32", "fully offline", "says unsure instead of guessing"]

HERO_CSS = """    .pin { animation: pinPulse 3.4s ease-in-out infinite; transform-box: fill-box;
           transform-origin: center; }
    @keyframes pinPulse { 0%, 100% { opacity: .5; transform: scale(1); }
                          50% { opacity: 1; transform: scale(1.35); } }
    .pipe { stroke-dasharray: 24; stroke-dashoffset: 0;
            animation: pipeDraw 5.2s ease-in-out infinite; }
    .pipe.late { animation-delay: .55s; }
    @keyframes pipeDraw { 0% { stroke-dashoffset: 24; } 34% { stroke-dashoffset: 0; }
                          86% { stroke-dashoffset: 0; } 100% { stroke-dashoffset: 24; } }
"""


def hero(theme_name: str) -> str:
    t = THEMES[theme_name]
    p = []

    # left column
    p.append(text(0, 88, "tinycue", 62, t["ink"], weight=700, track=TRACK_TIGHT))
    p.append(text(0, 132, PITCH_1, 19, t["ink2"], track=TRACK_TEXT))
    p.append(text(0, 159, PITCH_2, 19, t["ink2"], track=TRACK_TEXT))

    # motif card, right
    mx, mw = 600, 360
    my, mh = 14, 178
    p.append(card(mx, my, mw, mh, t, r=14))
    inner = mx + 18

    # sentence in
    p.append(f'<circle cx="{inner + 4}" cy="{my + 26}" r="4.5" fill="{t["accent"]}"/>')
    p.append(text(inner + 18, my + 31, "turn on the bedroom light", 14.5, t["ink"], track=TRACK_TEXT))

    pipe_x = mx + mw / 2
    p.append(
        f'<line class="pipe" x1="{pipe_x}" y1="{my + 36}" x2="{pipe_x}" y2="{my + 54}" '
        f'stroke="{t["edge"]}" stroke-width="1.4"/>'
    )

    # the chip
    cy, ch = my + 54, 54
    p.append(card(inner, cy, mw - 36, ch, t, r=10, fill=t["bg"], stroke=t["edge"]))
    p.append(chip_icon(inner + 12, cy + 12, 30, t["accent"], animate=True))
    p.append(text(inner + 54, cy + 25, "on the chip", 14, t["ink"], weight=700, track=TRACK_SNUG))
    p.append(text(inner + 54, cy + 43, "5.1 ms, no network", 12.5, t["ink2"]))

    p.append(
        f'<line class="pipe late" x1="{pipe_x}" y1="{cy + ch}" x2="{pipe_x}" y2="{cy + ch + 18}" '
        f'stroke="{t["edge"]}" stroke-width="1.4"/>'
    )

    # command out
    oy = cy + ch + 18
    p.append(card(inner, oy, mw - 36, 36, t, r=9, fill=t["soft"], stroke=t["soft"]))
    p.append(text(inner + 13, oy + 23, "set_light(room=bedroom, state=on)", 12.5, t["ink"], mono=True))

    # stat chips, full width
    cx, cyy, chh = 0.0, 228.0, 36.0
    for i, label in enumerate(HERO_CHIPS):
        w = sans_w(label, 15, 400, TRACK_TEXT) + 26
        first = i == 0
        p.append(
            card(cx, cyy, w, chh, t, r=9,
                 fill=t["accentSoft"] if first else t["card"],
                 stroke=t["accent"] if first else t["edge"])
        )
        p.append(text(cx + 13, cyy + 23.5, label, 15,
                      t["accent"] if first else t["ink2"], track=TRACK_TEXT))
        cx += w + 9
    if cx > HERO_W + 1:
        print(f"  warning: hero chips run to {cx:.0f}, wider than {HERO_W}", file=sys.stderr)

    return svg_document(
        "hero", HERO_W, HERO_H, theme_name,
        "tinycue: a sentence goes in, a command comes out, on a microcontroller",
        "Banner for tinycue. A card shows the sentence \"turn on the bedroom light\" going into a "
        "chip that answers in 5.1 ms with no network, and coming out as set_light with room "
        "bedroom and state on. Four labels read: around 250 KB model, about 5 ms on an ESP32, "
        "fully offline, and says unsure instead of guessing.",
        "\n".join("  " + s for s in p),
        HERO_CSS,
    )


# ------------------------------------------------------------------------------- flow

FLOW_W, FLOW_H = 960, 492
COL_W = 410
LCOL, RCOL = 0, 550
ROW_Y = [72, 176, 280, 400]
ROW_H = 78

FLOW_CSS = """    .travel { opacity: 0; stroke-dasharray: 16 100; stroke-dashoffset: 16;
              animation: travel 9s linear infinite; }
    @keyframes travel { 0% { stroke-dashoffset: 16; opacity: 0; }
                        1.5% { opacity: 1; }
                        9% { opacity: 1; }
                        10.5% { stroke-dashoffset: -100; opacity: 0; }
                        100% { stroke-dashoffset: -100; opacity: 0; } }
"""


def step(x, y, w, h, t, title, sub, *, tone="plain") -> str:
    fill, stroke = t["card"], t["edge"]
    if tone == "soft":
        fill, stroke = t["soft"], t["edge2"]
    if tone == "accent":
        fill, stroke = t["accentSoft"], t["accent"]
    out = [card(x, y, w, h, t, r=12, fill=fill, stroke=stroke)]
    out.append(text(x + 18, y + 33, title, 16.5, t["ink"], weight=700, track=TRACK_SNUG))
    out.append(text(x + 18, y + 56, sub, 12.5, t["ink2"], track=TRACK_TEXT))
    return "".join(out)


def connector(d, t, delay=0.0) -> str:
    """A hairline path plus an accent dash that travels along it once per cycle."""
    base = (f'<path d="{d}" fill="none" stroke="{t["wire"]}" stroke-width="1.3" '
            f'marker-end="url(#flow-tip)"/>')
    trace = (f'<path class="travel" d="{d}" pathLength="100" fill="none" '
             f'stroke="{t["accent"]}" stroke-width="2" stroke-linecap="round" '
             f'style="animation-delay:{delay:g}s"/>')
    return base + trace


def flow(theme_name: str) -> str:
    t = THEMES[theme_name]
    p = [
        f'<defs><marker id="flow-tip" markerWidth="7" markerHeight="6" refX="6.2" refY="3" '
        f'orient="auto"><path d="M0 0.4 L6.4 3 L0 5.6 Z" fill="{t["wire"]}"/></marker></defs>'
    ]

    # column headings
    p.append(text(LCOL, 22, "On your laptop", 17, t["ink"], weight=700, track=TRACK_SNUG))
    p.append(text(LCOL, 44, "build time", 13, t["ink2"], track=TRACK_TEXT))
    p.append(text(RCOL, 22, "On the device", 17, t["ink"], weight=700, track=TRACK_SNUG))
    p.append(text(RCOL, 44, "run time", 13, t["ink2"], track=TRACK_TEXT))

    left = [
        ("Commands file", "a handful of example sentences", "soft"),
        ("Train", "fits the intent model and the slot tagger", "plain"),
        ("Doctor", "names what to write next", "plain"),
        ("Export", "two C files and a 249 KB blob", "plain"),
    ]
    for (title, sub, tone), y in zip(left, ROW_Y):
        p.append(step(LCOL, y, COL_W, ROW_H, t, title, sub, tone=tone))

    right = [
        ("Text in", "typed, or from a speech recogniser", "soft"),
        ("Intent model and slot tagger", "the command, and its slot values", "plain"),
        ("Confidence check", "is the answer above the cut-off?", "accent"),
    ]
    for (title, sub, tone), y in zip(right, ROW_Y[:3]):
        p.append(step(RCOL, y, COL_W, ROW_H, t, title, sub, tone=tone))

    # the two answers, equal halves of the last row
    half = (COL_W - 20) / 2
    act_x = RCOL
    ask_x = RCOL + half + 20
    p.append(card(act_x, ROW_Y[3], half, ROW_H, t, r=12))
    p.append(text(act_x + 18, ROW_Y[3] + 33, "Act", 16.5, t["ink"], weight=700, track=TRACK_SNUG))
    p.append(text(act_x + 18, ROW_Y[3] + 56, "run the command", 12.5, t["ink2"], track=TRACK_TEXT))
    p.append(card(ask_x, ROW_Y[3], half, ROW_H, t, r=12))
    p.append(text(ask_x + 18, ROW_Y[3] + 33, "Ask again", 16.5, t["ink"], weight=700, track=TRACK_SNUG))
    p.append(text(ask_x + 18, ROW_Y[3] + 56, "do not guess", 12.5, t["ink2"], track=TRACK_TEXT))

    # straight connectors down each column
    lx, rx = LCOL + COL_W / 2, RCOL + COL_W / 2
    for i in range(3):
        y0, y1 = ROW_Y[i] + ROW_H, ROW_Y[i + 1]
        p.append(connector(f"M{lx} {y0} V{y1}", t, delay=i * 0.55))
    for i in range(2):
        y0, y1 = ROW_Y[i] + ROW_H, ROW_Y[i + 1]
        p.append(connector(f"M{rx} {y0} V{y1}", t, delay=2.4 + i * 0.55))

    # the branch, symmetric about the confidence card
    by = ROW_Y[2] + ROW_H
    mid = by + 21
    act_c, ask_c = act_x + half / 2, ask_x + half / 2
    p.append(connector(
        f"M{rx} {by} V{mid - 10} Q{rx} {mid} {rx - 10} {mid} "
        f"H{act_c + 10} Q{act_c} {mid} {act_c} {mid + 10} V{ROW_Y[3]}", t, delay=3.9))
    p.append(connector(
        f"M{rx} {by} V{mid - 10} Q{rx} {mid} {rx + 10} {mid} "
        f"H{ask_c - 10} Q{ask_c} {mid} {ask_c} {mid + 10} V{ROW_Y[3]}", t, delay=4.35))
    p.append(text(rx - 14, mid - 8, "sure", 11.5, t["sure"], weight=700, anchor="end",
                  track=TRACK_TEXT))
    p.append(text(rx + 14, mid - 8, "unsure", 11.5, t["unsure"], weight=700, track=TRACK_TEXT))

    # the blob crossing from the laptop to the device
    gx = (LCOL + COL_W + RCOL) / 2
    d = (f"M{LCOL + COL_W} {ROW_Y[3] + ROW_H / 2} H{gx - 20} Q{gx} {ROW_Y[3] + ROW_H / 2} "
         f"{gx} {ROW_Y[3] + ROW_H / 2 - 20} V{ROW_Y[1] + ROW_H / 2 + 20} "
         f"Q{gx} {ROW_Y[1] + ROW_H / 2} {gx + 20} {ROW_Y[1] + ROW_H / 2} H{RCOL}")
    p.append(
        f'<path d="{d}" fill="none" stroke="{t["accent"]}" stroke-width="1.3" '
        f'stroke-dasharray="4 4" opacity="0.8" marker-end="url(#flow-tip)"/>'
    )
    label = "the 249 KB blob"
    lw = mono_w(label, 10.5) + 22
    ly = (ROW_Y[1] + ROW_H / 2 + ROW_Y[3] + ROW_H / 2) / 2
    p.append(card(gx - lw / 2, ly - 11, lw, 22, t, r=11, fill=t["card"], stroke=t["edge"]))
    p.append(text(gx, ly + 4, label, 10.5, t["accent"], mono=True, anchor="middle"))

    return svg_document(
        "flow", FLOW_W, FLOW_H, theme_name,
        "How tinycue turns example sentences into offline commands",
        "Two columns. On your laptop, at build time: a commands file of example sentences, a "
        "training run that fits the intent model and the slot tagger, a doctor pass that names "
        "what to write next, and an export that writes two C files and a 249 KB blob. That blob "
        "crosses to the device. On the device, at run time: text comes in typed or from a speech "
        "recogniser, the intent model and slot tagger return the command and its slot values, a "
        "confidence check asks whether the answer is above the cut-off, and the device either "
        "acts or asks again.",
        "\n".join("  " + s for s in p),
        FLOW_CSS,
    )


# ----------------------------------------------------------------------------- unsure

UNSURE_W, UNSURE_H = 960, 180
LO, HI = 0.60, 1.00
CUTOFF = 0.849
RAIL_Y, RAIL_H = 36, 16


def at(conf: float) -> float:
    return (conf - LO) / (HI - LO) * UNSURE_W


def unsure(theme_name: str) -> str:
    t = THEMES[theme_name]
    p = []
    cut = at(CUTOFF)

    # the rail, split at the cut-off
    p.append(f'<rect x="0" y="{RAIL_Y}" width="{UNSURE_W}" height="{RAIL_H}" rx="8" '
             f'fill="{t["rail"]}"/>')
    p.append(f'<path d="M{cut:.1f} {RAIL_Y} H{UNSURE_W - 8} a8 8 0 0 1 8 8 a8 8 0 0 1 -8 8 '
             f'H{cut:.1f} Z" fill="{t["accentSoft"]}"/>')
    p.append(f'<rect x="0.5" y="{RAIL_Y + 0.5}" width="{UNSURE_W - 1}" height="{RAIL_H - 1}" '
             f'rx="7.5" fill="none" stroke="{t["edge"]}" stroke-width="1"/>')

    # the cut-off, and its pill
    p.append(f'<line x1="{cut:.1f}" y1="{RAIL_Y - 8}" x2="{cut:.1f}" y2="{RAIL_Y + RAIL_H + 8}" '
             f'stroke="{t["ink"]}" stroke-width="2"/>')
    pill = "cut-off 0.85"
    pw = sans_w(pill, 12, 700, 0.01) + 22
    p.append(f'<rect x="{cut - pw / 2:.1f}" y="0" width="{pw:.1f}" height="23" rx="11.5" '
             f'fill="{t["ink"]}"/>')
    p.append(text(cut, 15.5, pill, 12, t["bg"], weight=700, anchor="middle", track=0.01))

    # the two real answers, as dots on the rail
    for conf, colour in ((0.668, t["unsure"]), (0.922, t["sure"])):
        x = at(conf)
        p.append(f'<circle cx="{x:.1f}" cy="{RAIL_Y + RAIL_H / 2}" r="9" fill="{t["bg"]}"/>')
        p.append(f'<circle cx="{x:.1f}" cy="{RAIL_Y + RAIL_H / 2}" r="6.5" fill="{colour}"/>')

    # scale
    for conf in (0.60, 0.70, 0.80, 0.90, 1.00):
        x = at(conf)
        anchor = "start" if conf == LO else "end" if conf == HI else "middle"
        p.append(text(x, 76, f"{conf:.2f}", 12, t["ink2"], mono=True, anchor=anchor,
                      cls="tnum"))

    p.append(text(0, 100, "ask again", 13.5, t["ink"], weight=700, track=TRACK_TEXT))
    p.append(text(UNSURE_W, 100, "act", 13.5, t["ink"], weight=700, anchor="end", track=TRACK_TEXT))

    # the two sentences, one either side
    p.append(text(0, 142, "“just the time thanks”", 16, t["ink"], track=TRACK_TEXT))
    p.append(f'<text x="0" y="166" font-family="{SANS}" font-size="13" fill="{t["ink2"]}" '
             f'letter-spacing="{TRACK_TEXT}em">'
             f'<tspan fill="{t["unsure"]}" font-weight="700">0.668</tspan>'
             f'{esc(", under the line, so the device asks again")}</text>')
    p.append(text(UNSURE_W, 142, "“whats the temp reading”", 16, t["ink"],
                  anchor="end", track=TRACK_TEXT))
    p.append(f'<text x="{UNSURE_W}" y="166" font-family="{SANS}" font-size="13" '
             f'fill="{t["ink2"]}" text-anchor="end" letter-spacing="{TRACK_TEXT}em">'
             f'<tspan fill="{t["sure"]}" font-weight="700">0.922</tspan>'
             f'{esc(", over the line, so show(what=temperature) runs")}</text>')

    return svg_document(
        "unsure", UNSURE_W, UNSURE_H, theme_name,
        "The unsure cut-off, and two real sentences either side of it",
        "A confidence rail from 0.60 to 1.00 with the smart home model's fitted cut-off at 0.849, "
        "labelled 0.85. Below the cut-off the device asks again, above it the device acts. The "
        "sentence \"just the time thanks\" lands at 0.668, under the line, so the device asks "
        "again. The sentence \"whats the temp reading\" lands at 0.922, over the line, so "
        "show with what set to temperature runs. Both figures were measured on a classic ESP32.",
        "\n".join("  " + s for s in p),
    )


# ----------------------------------------------------------------------------- boards

BOARDS_W, BOARDS_H = 960, 214
BOARD_CARDS = [
    ("classic ESP32", "Xtensa LX6, 240 MHz",
     [("flash", "345 KB"), ("ram", "34 KB"), ("per sentence", "5.1 ms")]),
    ("NXP FRDM-MCXN236", "Arm Cortex-M33, 150 MHz",
     [("flash", "289 KB"), ("ram", "21 KB"), ("per sentence", "5.1 ms")]),
]


def boards(theme_name: str) -> str:
    t = THEMES[theme_name]
    p = []
    gap = 24
    w = (BOARDS_W - gap) / 2
    for i, (name, sub, rows) in enumerate(BOARD_CARDS):
        x = i * (w + gap)
        p.append(card(x, 0, w, 170, t, r=12))
        p.append(chip_icon(x + 20, 23, 30, t["accent"]))
        p.append(text(x + 62, 36, name, 17, t["ink"], weight=700, track=TRACK_SNUG))
        p.append(text(x + 62, 55, sub, 12.5, t["ink2"], track=TRACK_TEXT))
        p.append(f'<line x1="{x + 20}" y1="72" x2="{x + w - 20}" y2="72" '
                 f'stroke="{t["edge2"]}" stroke-width="1"/>')
        for j, (label, value) in enumerate(rows):
            y = 100 + j * 28
            p.append(text(x + 20, y, label, 13, t["ink2"], track=TRACK_TEXT))
            p.append(text(x + w - 20, y, value, 14, t["ink"], weight=700, anchor="end",
                          track=TRACK_TEXT, cls="tnum"))

    p.append(text(0, 204, "Answers identical to the desktop build, 31 of 31.", 13.5,
                  t["ink2"], track=TRACK_TEXT))
    return svg_document(
        "boards", BOARDS_W, BOARDS_H, theme_name,
        "tinycue measured on two microcontrollers",
        "Two cards. A classic ESP32, Xtensa LX6 at 240 MHz: 345 KB of flash for the whole image, "
        "34 KB of static RAM, 5.1 ms per sentence. An NXP FRDM-MCXN236, Arm Cortex-M33 at 150 "
        "MHz: 289 KB of flash for the whole image, 21 KB of static RAM, 5.1 ms per sentence. "
        "Same runtime, same 249 KB blob, and both boards answered all 31 test sentences the same "
        "way as the desktop build.",
        "\n".join("  " + s for s in p),
    )


# --------------------------------------------------------------------- social preview

SOCIAL_W, SOCIAL_H = 1280, 640


def social(theme_name: str = "light") -> str:
    t = THEMES[theme_name]
    p = [f'<rect x="0" y="0" width="{SOCIAL_W}" height="{SOCIAL_H}" fill="{t["bg"]}"/>']
    x0, y0 = 96, 0
    p.append(text(x0, 258, "tinycue", 96, t["ink"], weight=700, track=TRACK_TIGHT))
    p.append(text(x0, 312, "A sentence goes in, a command with its slot values comes out,",
                  25, t["ink2"], track=TRACK_TEXT))
    p.append(text(x0, 348, "on a microcontroller.", 25, t["ink2"], track=TRACK_TEXT))

    cx = x0
    for i, label in enumerate(HERO_CHIPS):
        w = sans_w(label, 17, 400, TRACK_TEXT) + 30
        first = i == 0
        p.append(card(cx, 404, w, 42, t, r=10,
                      fill=t["accentSoft"] if first else t["card"],
                      stroke=t["accent"] if first else t["edge"]))
        p.append(text(cx + 15, 431, label, 17, t["accent"] if first else t["ink2"],
                      track=TRACK_TEXT))
        cx += w + 11

    p.append(chip_icon(1040, 250, 120, t["accent"]))
    p.append(text(1100, 406, "5.1 ms, no network", 19, t["ink2"], mono=True, anchor="middle"))
    _ = y0
    return svg_document(
        "social", SOCIAL_W, SOCIAL_H, theme_name,
        "tinycue",
        "Social preview card for tinycue: a sentence goes in, a command with its slot values "
        "comes out, on a microcontroller. Around 250 KB model, about 5 ms on an ESP32, fully "
        "offline, and says unsure instead of guessing.",
        "\n".join("  " + s for s in p),
    )


# ------------------------------------------------------------------------------ output

FIGURES = {"hero": hero, "flow": flow, "unsure": unsure, "boards": boards}


def write_svgs() -> list[pathlib.Path]:
    written = []
    for name, draw in FIGURES.items():
        for theme_name in ("light", "dark"):
            path = HERE / f"{name}-{theme_name}.svg"
            path.write_text(draw(theme_name), encoding="utf-8")
            written.append(path)
    path = HERE / "social-preview.svg"
    path.write_text(social("light"), encoding="utf-8")
    written.append(path)
    return written


def write_pngs(paths, scale=2):
    """Screenshot each SVG through an <img> tag, which is how the README shows it."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed, so the PNG copies were not rebuilt", file=sys.stderr)
        return []
    import re
    import tempfile

    made = []
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        for svg_path in paths:
            source = svg_path.read_text(encoding="utf-8")
            m = re.search(r'viewBox="0 0 (\d+) (\d+)"', source)
            w, h = int(m.group(1)), int(m.group(2))
            theme_name = "dark" if svg_path.stem.endswith("-dark") else "light"
            bg = THEMES[theme_name]["bg"]
            wrapper = pathlib.Path(tmp) / f"{svg_path.stem}.html"
            wrapper.write_text(
                '<!doctype html><meta charset="utf-8">'
                f'<body style="margin:0;background:{bg}">'
                f'<img src="file://{svg_path}" width="{w}" height="{h}" '
                'style="display:block"></body>',
                encoding="utf-8",
            )
            page = browser.new_page(viewport={"width": w, "height": h},
                                    device_scale_factor=scale)
            page.goto(f"file://{wrapper}")
            page.wait_for_timeout(600)
            png = svg_path.with_suffix(".png")
            page.screenshot(path=png)
            page.close()
            made.append(png)
        browser.close()
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--png", action="store_true", help="also rebuild the PNG copies")
    args = ap.parse_args()

    paths = write_svgs()
    for p in paths:
        print(f"{p.name}: {p.stat().st_size / 1024:.1f} KB")
    if args.png:
        for p in write_pngs(paths):
            print(f"{p.name}: {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
