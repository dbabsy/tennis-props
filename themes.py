"""Per-tournament colour, so a page looks like the event it is describing.

The palettes come from the courts themselves rather than from tournament
branding: the US Open's blue deco-turf inside a darker surround, Wimbledon's
green and purple, Roland Garros' crushed brick, the Australian Open's cyan.
Non-slam weeks fall back to the surface, which is the honest generalisation --
a clay event in Umag and a clay event in Bastad look the same from the baseline.

Every palette defines both a light and a dark set. The page follows the
reader's system theme, so a palette that only works in one of them is a bug.
Text colour is deliberately NOT themed: contrast against the tinted background
is the one thing that must not vary by tournament.
"""

import re

# key: (label, light, dark)
#   bg    page background
#   card  raised panel
#   line  hairline borders
#   chip  subtle fill
#   accent  bars and links
#   glow  a wash laid over the background, may be "none"
THEMES = {
    "usopen": ("US Open", {
        "bg": "#eef3fa", "card": "#ffffff", "line": "#d3e0f0",
        "chip": "#e1eaf7", "accent": "#12508f",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#c9dcf3 0%,rgba(201,220,243,0) 70%)",
    }, {
        "bg": "#0b1622", "card": "#12202f", "line": "#213348",
        "chip": "#182838", "accent": "#5fa2dd",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#153353 0%,rgba(21,51,83,0) 70%)",
    }),
    "wimbledon": ("Wimbledon", {
        "bg": "#f0f5ef", "card": "#ffffff", "line": "#d6e4d4",
        "chip": "#e4eee2", "accent": "#00543c",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#cfe3ca 0%,rgba(207,227,202,0) 70%)",
    }, {
        "bg": "#0b1610", "card": "#121f17", "line": "#1f3327",
        "chip": "#16241b", "accent": "#54b489",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#16341f 0%,rgba(22,52,31,0) 70%)",
    }),
    "rolandgarros": ("Roland Garros", {
        "bg": "#fbf1ec", "card": "#ffffff", "line": "#eed8cd",
        "chip": "#f6e5db", "accent": "#a8462a",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#f0cdb9 0%,rgba(240,205,185,0) 70%)",
    }, {
        "bg": "#1a100c", "card": "#241712", "line": "#38231b",
        "chip": "#2a1a14", "accent": "#dd8360",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#43241a 0%,rgba(67,36,26,0) 70%)",
    }),
    "ausopen": ("Australian Open", {
        "bg": "#ebf5fa", "card": "#ffffff", "line": "#cfe5f0",
        "chip": "#dcedf6", "accent": "#00679a",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#c2e2f1 0%,rgba(194,226,241,0) 70%)",
    }, {
        "bg": "#08161d", "card": "#0f2029", "line": "#1c3540",
        "chip": "#142932", "accent": "#3fa9d4",
        "glow": "radial-gradient(1100px 460px at 50% -180px,#0f3648 0%,rgba(15,54,72,0) 70%)",
    }),
    # Surface fallbacks for ordinary tour weeks.
    "hard": ("Hard court", {
        "bg": "#f1f4f8", "card": "#ffffff", "line": "#dde3ec",
        "chip": "#e7ecf3", "accent": "#2f5fd0", "glow": "none",
    }, {
        "bg": "#111519", "card": "#191e24", "line": "#272e37",
        "chip": "#1f252c", "accent": "#7aa2f7", "glow": "none",
    }),
    "clay": ("Clay", {
        "bg": "#faf2ee", "card": "#ffffff", "line": "#ecdad1",
        "chip": "#f4e7e0", "accent": "#b25334", "glow": "none",
    }, {
        "bg": "#17110e", "card": "#211914", "line": "#33251e",
        "chip": "#271c17", "accent": "#d98a68", "glow": "none",
    }),
    "grass": ("Grass", {
        "bg": "#f1f5f0", "card": "#ffffff", "line": "#dae5d7",
        "chip": "#e7efe4", "accent": "#2f7a52", "glow": "none",
    }, {
        "bg": "#0f1611", "card": "#171f19", "line": "#253028",
        "chip": "#1c251f", "accent": "#5cb489", "glow": "none",
    }),
    "indoor": ("Indoor", {
        "bg": "#f4f2f8", "card": "#ffffff", "line": "#e3dfec",
        "chip": "#ebe7f3", "accent": "#5a3f9e", "glow": "none",
    }, {
        "bg": "#141219", "card": "#1c1a23", "line": "#2b2735",
        "chip": "#221f2b", "accent": "#a58ae0", "glow": "none",
    }),
}

# Fragment -> theme key. Order does not matter; the longest match wins.
SLAMS = {
    "us open": "usopen",
    "wimbledon": "wimbledon",
    "roland garros": "rolandgarros",
    "french open": "rolandgarros",
    "australian open": "ausopen",
}


def pick(tourney=None, surface=None, indoor=False):
    """Choose a theme for a slate. Slams get their own; everything else falls
    back to the surface it is played on."""
    n = re.sub(r"[^a-z ]", " ", (tourney or "").lower())
    hits = [(k, v) for k, v in SLAMS.items() if k in n]
    if hits:
        return max(hits, key=lambda kv: len(kv[0]))[1]
    if indoor:
        return "indoor"
    s = (surface or "Hard").lower()
    return s if s in ("clay", "grass") else "hard"


def css(key):
    """CSS variable overrides for one theme, light and dark."""
    label, light, dark = THEMES.get(key, THEMES["hard"])

    def block(t):
        return ("--bg:{bg};--card:{card};--line:{line};--chip:{chip};"
                "--accent:{accent};--glow:{glow}").format(**t)

    return (f":root{{{block(light)}}}"
            f"@media(prefers-color-scheme:dark){{:root{{{block(dark)}}}}}")


def label(key):
    return THEMES.get(key, THEMES["hard"])[0]
