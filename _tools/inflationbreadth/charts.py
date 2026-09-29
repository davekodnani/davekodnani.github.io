"""Chart rendering: static PNGs (Sahm-style) and a self-contained interactive HTML."""
import json
from pathlib import Path

import pandas as pd

import pce_breadth as pb

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "config" / "fonts"
TEMPLATE = ROOT / "config" / "chart_template.html"

NAVY = "#1f4e79"
GRAY = "#666666"
INK = "#3a3a3a"
GRID = "#b8b8b8"

HORIZON_LABEL = {"12m": "12-month", "6m": "6-month annualized", "3m": "3-month annualized"}


def _font():
    from matplotlib import font_manager
    import matplotlib.pyplot as plt
    for f in FONTS.glob("*.ttf"):
        font_manager.fontManager.addfont(str(f))
    if list(FONTS.glob("Lato*.ttf")):
        plt.rcParams["font.family"] = "Lato"


def render_pngs(prices, nominal, meta, threshold=pb.DEFAULT_THRESHOLD, start="2000-01"):
    import matplotlib
    matplotlib.use("Agg")
    _font()
    thr = pb.fmt_thr(threshold)
    for h, months in pb.HORIZONS.items():
        for wt in pb.WEIGHTINGS:
            s = pb.breadth(prices, nominal, months, threshold, wt == "weighted").dropna()
            avg = s[pb.AVG_WINDOW[0]:pb.AVG_WINDOW[1]].mean()
            title = f"Share of PCE categories with {HORIZON_LABEL[h]} price increases at or above {threshold:g} percent"
            path = pb.CHARTS / f"pce_breadth_{h}_{wt}_{thr}pct.png"
            _plot(s[start:], avg, title, meta, path)
    pb.log(f"wrote {len(pb.HORIZONS) * len(pb.WEIGHTINGS)} PNGs to {pb.CHARTS}")


def _plot(s, avg, title, meta, path):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9.45, 7.95), dpi=100)
    fig.subplots_adjust(left=0.085, right=0.905, top=0.87, bottom=0.12)
    fig.text(0.02, 0.955, title, fontsize=15.5 if len(title) < 75 else 13.5, color=INK, va="top")

    top = 80 if s.max() <= 84 else 20 * -(-s.max() // 20)
    ax.set_ylim(0, top + 5)
    ticks = list(range(0, int(top) + 1, 20))
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{t}%" if t == ticks[-1] else str(t) for t in ticks])
    for t in ticks[1:]:
        ax.axhline(t, color=GRID, lw=0.9, ls=(0, (1, 2.5)), zorder=0)

    x0 = pd.Timestamp(s.index[0]).year
    x0 -= x0 % 4
    x1 = s.index[-1].year + 2
    x1 += (4 - x1 % 4) % 4
    ax.set_xlim(pd.Timestamp(f"{x0}-01-01"), pd.Timestamp(f"{x1}-01-01"))
    ax.set_xticks([pd.Timestamp(f"{y}-01-01") for y in range(x0, x1 + 1, 4)])
    ax.set_xticklabels([str(y) for y in range(x0, x1 + 1, 4)])

    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(INK)
    ax.tick_params(axis="y", length=0, labelsize=14, colors=INK, pad=6)
    ax.tick_params(axis="x", length=6, labelsize=14, colors=INK, color=INK)

    ax.axhline(avg, color=NAVY, lw=1.2, ls=(0, (3, 2)), zorder=1)
    ax.text(s.index[-1] + pd.DateOffset(months=18), avg - 1.2,
            f"{pb.AVG_WINDOW[0][:4]}–{pb.AVG_WINDOW[1][:4]}\naverage: {avg:.0f}%",
            color=NAVY, fontsize=12.5, ha="center", va="top", linespacing=1.0)

    ax.plot(s.index, s.values, color=NAVY, lw=3.2, solid_joinstyle="round", zorder=3)
    ax.text(s.index[-1] + pd.DateOffset(months=5), s.iloc[-1], f"{s.iloc[-1]:.0f}%",
            color=NAVY, fontsize=15, fontweight="bold", va="center")

    through = pd.Timestamp(meta["latest_month"]).strftime("%B %Y")
    fig.text(0.02, 0.025, f"Source: BEA; author's calculations from 177 detailed PCE categories. "
             f"Data through {through}.", fontsize=11.5, color="#555555")
    fig.savefig(path, facecolor="white")
    plt.close(fig)


# NBER recessions (FRED USREC convention: month after peak through trough). Fallback only;
# the build pulls the current list from FRED so new NBER dates flow through.
RECESSIONS_FALLBACK = [
    ["1960-05", "1961-02"], ["1970-01", "1970-11"], ["1973-12", "1975-03"], ["1980-02", "1980-07"],
    ["1981-08", "1982-11"], ["1990-08", "1991-03"], ["2001-04", "2001-11"], ["2008-01", "2009-06"],
    ["2020-03", "2020-04"],
]


def recessions():
    try:
        import io
        import requests
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=USREC", timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "rec"]
        df["m"] = df.date.str[:7]
        out, start, prev = [], None, None
        for m, v in zip(df.m, df.rec):
            if v == 1 and start is None:
                start = m
            if v == 0 and start is not None:
                out.append([start, prev])
                start = None
            prev = m
        if start is not None:
            out.append([start, prev])
        if len(out) >= len(RECESSIONS_FALLBACK):
            return out
    except Exception as e:
        pb.log(f"FRED recession fetch failed ({e!r}); using built-in NBER dates")
    return RECESSIONS_FALLBACK


def render_html(prices, nominal, comps, meta):
    """Embed component-level data so the page can recompute any threshold client-side."""
    dates = [d.strftime("%Y-%m") for d in prices.index]
    def cols(df, nd):
        return [[None if pd.isna(v) else round(float(v), nd) for v in df[c]] for c in comps.series_code]
    payload = {
        "dates": dates,
        "names": comps.name.tolist(),
        "prices": cols(prices, 3),
        "nominal": cols(nominal, 0),
        "latest": meta["latest_month"],
        "fetched": meta["fetched"],
        "avgWindow": list(pb.AVG_WINDOW),
        "recessions": recessions(),
    }
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")))
    out = pb.CHARTS / "pce_breadth.html"
    out.write_text(html)
    pb.log(f"wrote {out}")
