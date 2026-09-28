#!/usr/bin/env python3
"""Share of PCE categories with price growth at or above a threshold.

Components: the 177 detailed PCE categories used by the Dallas Fed trimmed-mean
PCE (config/components_dallas177.csv) -- the same set as Claudia Sahm's chart.
With net foreign travel (a small net-exports adjustment) they sum to total PCE.

Variants (6 per threshold; the HTML page recomputes for any threshold):
  horizon   : 12m (yoy), 6m (annualized), 3m (annualized)
  weighting : unweighted (share of categories) | weighted (share of nominal spending)
  threshold : any X%, default 3 (comparison is >= X)

Usage:
  python3 pce_breadth.py update            # fetch latest data, rebuild all outputs
  python3 pce_breadth.py build             # rebuild outputs from cached raw data
  python3 pce_breadth.py wait [--minutes N] [--interval S]
                                           # poll BEA until a month newer than the
                                           # cached data appears, then update
  python3 pce_breadth.py auto              # scheduled job: quick check, or poll for
                                           # up to 45 min if a release is due
  python3 pce_breadth.py status            # show cached vs. BEA latest month
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "output"
CHARTS = ROOT / "charts"
COMPONENTS = ROOT / "config" / "components_dallas177.csv"

API = "https://apps.bea.gov/api/data"
FLAT = "https://apps.bea.gov/national/Release/TXT/NipaDataM.txt"
PRICE_TABLE, NOMINAL_TABLE = "U20404", "U20405"

HORIZONS = {"12m": 12, "6m": 6, "3m": 3}
WEIGHTINGS = ("unweighted", "weighted")
DEFAULT_THRESHOLD = 3.0
AVG_WINDOW = ("2000-01", "2019-12")
NO_NEW_DATA = 3  # exit code for wait/auto when BEA has nothing new (errors exit 1)


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def api_key():
    key = os.environ.get("BEA_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("BEA_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("BEA_API_KEY not set (put BEA_API_KEY=... in .env)")
    return key


def nominal_code(price_code):
    """Price-index series code -> matching current-dollar series code."""
    if price_code.endswith("RG"):
        return price_code[:-2] + "RC"
    if price_code.startswith("IA"):
        return "LA" + price_code[2:]
    raise ValueError(f"don't know nominal code for {price_code}")


def load_components():
    c = pd.read_csv(COMPONENTS)
    c["nominal_code"] = c.series_code.map(nominal_code)
    return c


# ---------------------------------------------------------------- fetching

def api_get(table, years, retries=4):
    params = {
        "UserID": api_key(), "method": "GetData", "datasetname": "NIUnderlyingDetail",
        "TableName": table, "Frequency": "M", "Year": years, "ResultFormat": "JSON",
    }
    for attempt in range(retries):
        try:
            r = requests.get(API, params=params, timeout=120)
            r.raise_for_status()
            res = r.json()["BEAAPI"]["Results"]
            if isinstance(res, list):
                res = res[0]
            if "Error" in res:
                raise RuntimeError(res["Error"])
            return res
        except Exception as e:  # network blips, 5xx, throttling
            if attempt == retries - 1:
                raise
            log(f"  {table} {years}: {e!r}; retrying")
            time.sleep(5 * (attempt + 1))


def records_to_frame(data):
    df = pd.DataFrame(data)[["SeriesCode", "TimePeriod", "DataValue"]]
    df.columns = ["code", "period", "value"]
    df["value"] = pd.to_numeric(df.value.str.replace(",", ""), errors="coerce")
    return df


def fetch_table_api(table):
    """Whole monthly history, pulled in ~10-year chunks to keep responses small."""
    frames, last_revised = [], None
    this_year = datetime.now().year
    for start in range(1959, this_year + 1, 10):
        years = ",".join(str(y) for y in range(start, min(start + 10, this_year + 1)))
        res = api_get(table, years)
        frames.append(records_to_frame(res["Data"]))
        for n in res.get("Notes", []):
            if n.get("NoteRef") == table and "LastRevised" in n.get("NoteText", ""):
                last_revised = n["NoteText"].split("LastRevised:")[-1].strip()
    return pd.concat(frames, ignore_index=True), last_revised


def fetch_flatfile(codes):
    log("  falling back to BEA flat file NipaDataM.txt")
    df = pd.read_csv(FLAT, thousands=",")
    df.columns = ["code", "period", "value"]
    return df[df.code.isin(codes)].copy()


def to_wide(df):
    df = df.dropna(subset=["value"])
    df["date"] = pd.to_datetime(df.period.str.replace("M", "-"), format="%Y-%m")
    return df.pivot_table(index="date", columns="code", values="value", aggfunc="last").sort_index()


def fetch_all():
    comps = load_components()
    need_p = set(comps.series_code) | {"DPCERG"}
    need_n = set(comps.nominal_code) | {"DPCERC", "DFORRC"}  # total PCE, net foreign travel
    try:
        p, rev = fetch_table_api(PRICE_TABLE)
        n, _ = fetch_table_api(NOMINAL_TABLE)
        source = f"BEA API (NIUnderlyingDetail), tables {PRICE_TABLE}/{NOMINAL_TABLE}"
    except Exception as e:
        log(f"API fetch failed: {e!r}")
        flat = fetch_flatfile(need_p | need_n)
        p, n, rev = flat[flat.code.isin(need_p)], flat[flat.code.isin(need_n)], None
        source = "BEA flat file NipaDataM.txt"
    prices, nominal = to_wide(p), to_wide(n)
    missing = (need_p - set(prices.columns)) | (need_n - set(nominal.columns))
    if missing:
        raise RuntimeError(f"series missing from BEA data: {sorted(missing)}")
    prices, nominal = prices[sorted(need_p)], nominal[sorted(need_n)]
    validate(prices, nominal, comps)
    prices.to_csv(RAW / "prices_U20404.csv")
    nominal.to_csv(RAW / "nominal_U20405.csv")
    try:
        stamp = flat_last_modified()
    except Exception:
        stamp = None
    meta = {"fetched": datetime.now().isoformat(timespec="seconds"), "source": source,
            "bea_last_revised": rev, "latest_month": prices.index.max().strftime("%Y-%m"),
            "flat_last_modified": stamp}
    (RAW / "meta.json").write_text(json.dumps(meta, indent=2))
    log(f"fetched through {meta['latest_month']} ({source}; BEA last revised {rev})")
    return meta


def validate(prices, nominal, comps):
    """Guard against silent breakage (renamed series, partial month, etc.)."""
    last = prices.index.max()
    lp = prices.loc[last, comps.series_code]
    ln = nominal.loc[last, comps.nominal_code] if last in nominal.index else None
    if lp.isna().any() or ln is None or ln.isna().any():
        raise RuntimeError(f"incomplete component data for {last:%Y-%m}")
    # The 177 categories plus net foreign travel exhaust total PCE.
    cover = (ln.sum() + nominal.loc[last, "DFORRC"]) / nominal.loc[last, "DPCERC"]
    if abs(cover - 1) > 0.001:
        raise RuntimeError(f"components sum to {cover:.3%} of total PCE in {last:%Y-%m}")


def bea_state():
    """(latest month, LastRevised note) on BEA: API first, flat file if the API is down."""
    try:
        res = api_get(PRICE_TABLE, f"{datetime.now().year - 1},{datetime.now().year}", retries=2)
        periods = [d["TimePeriod"] for d in res["Data"] if d["SeriesCode"] == "DPCERG"]
        rev = next((n["NoteText"].split("LastRevised:")[-1].strip() for n in res.get("Notes", [])
                    if n.get("NoteRef") == PRICE_TABLE and "LastRevised" in n.get("NoteText", "")), None)
    except Exception as e:
        # No key needed: a HEAD request on the flat file is cheap, so poll that and only
        # download the ~36MB file when its Last-Modified stamp changes.
        log(f"  API check failed ({e!r}); checking flat file")
        meta = cached_meta()
        stamp = flat_last_modified()
        if meta and stamp == meta.get("flat_last_modified"):
            return meta["latest_month"], meta.get("bea_last_revised")
        periods, rev = fetch_flatfile({"DPCERG"}).period.tolist(), f"flat file {stamp}"
    return max(periods).replace("M", "-"), rev


def flat_last_modified():
    return requests.head(FLAT, timeout=30).headers.get("Last-Modified")


def cached_meta():
    m = RAW / "meta.json"
    return json.loads(m.read_text()) if m.exists() else {}


def is_new(latest, rev, meta):
    if not meta:
        return True
    if latest > meta["latest_month"]:
        return True
    # Same month but BEA revised history (e.g. annual update) -> rebuild too.
    old = meta.get("bea_last_revised")
    if not rev or not old or rev.startswith("flat file") != old.startswith("flat file"):
        return False
    return rev != old


def release_due(meta, today=None):
    """True when a new month is plausibly imminent: PCE for month M lands late in M+1."""
    today = today or datetime.now()
    if not meta:
        return True
    first = pd.Timestamp(today.year, today.month, 1)
    prev = (first - pd.DateOffset(months=1)).strftime("%Y-%m")      # due late this month
    overdue = (first - pd.DateOffset(months=2)).strftime("%Y-%m")   # should already be out
    have = meta["latest_month"]
    return have < overdue or (have < prev and today.day >= 20)


# ---------------------------------------------------------------- compute

def load_raw():
    prices = pd.read_csv(RAW / "prices_U20404.csv", index_col=0, parse_dates=True)
    nominal = pd.read_csv(RAW / "nominal_U20405.csv", index_col=0, parse_dates=True)
    comps = load_components()
    prices = prices[comps.series_code]
    nominal = nominal[comps.nominal_code].set_axis(comps.series_code, axis=1)
    return prices, nominal, comps


def growth(prices, months):
    """Percent change over `months`; annualized for horizons shorter than 12m."""
    return ((prices / prices.shift(months)) ** (12 / months) - 1) * 100


def breadth(prices, nominal, months, threshold, weighted):
    g = growth(prices, months)
    valid = g.notna()
    above = (g >= threshold) & valid
    if weighted:
        # Current-month nominal expenditure shares among categories with valid data.
        w = nominal.where(valid)
        share = (w.where(above, 0).sum(axis=1) / w.sum(axis=1)) * 100
    else:
        share = above.sum(axis=1) / valid.sum(axis=1) * 100
    share[valid.sum(axis=1) == 0] = float("nan")
    return share


def all_variants(prices, nominal, threshold=DEFAULT_THRESHOLD):
    cols = {}
    for h, m in HORIZONS.items():
        for wt in WEIGHTINGS:
            cols[f"{h}_{wt}"] = breadth(prices, nominal, m, threshold, wt == "weighted")
    df = pd.DataFrame(cols)
    df["n_categories"] = growth(prices, 12).notna().sum(axis=1)
    return df.dropna(how="all", subset=list(cols))


# ---------------------------------------------------------------- outputs

def build():
    prices, nominal, comps = load_raw()
    meta = json.loads((RAW / "meta.json").read_text())
    df = all_variants(prices, nominal)
    df.index = df.index.strftime("%Y-%m")
    df.index.name = "month"
    df.round(2).to_csv(OUT / f"pce_breadth_{fmt_thr(DEFAULT_THRESHOLD)}pct.csv")
    import charts
    if os.environ.get("SKIP_PNG") != "1":
        charts.render_pngs(prices, nominal, meta)
    charts.render_html(prices, nominal, comps, meta)
    last = df.iloc[-1]
    log(f"built outputs through {df.index[-1]}: " +
        ", ".join(f"{k}={last[k]:.0f}%" for k in df.columns if k != "n_categories"))


def fmt_thr(t):
    return f"{t:g}".replace(".", "p")


def wait(minutes, interval):
    """Poll BEA until data newer than the cache appears (or time runs out), then update."""
    meta = cached_meta()
    deadline = time.time() + minutes * 60
    log(f"waiting for BEA data newer than {meta.get('latest_month')} (up to {minutes:g} min)")
    while True:
        try:
            latest, rev = bea_state()
            if is_new(latest, rev, meta):
                log(f"new data on BEA: {latest} (last revised {rev})")
                fetch_all()
                build()
                return 0
        except Exception as e:
            log(f"poll error: {e!r}")
        if time.time() > deadline:
            log("no new data; giving up for now")
            return NO_NEW_DATA
        time.sleep(interval)


def auto(minutes, interval):
    """Scheduled entry point: one quick check, or a polling window if a release is due."""
    meta = cached_meta()
    if release_due(meta):
        return wait(minutes, interval)
    return wait(0, interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update")
    sub.add_parser("build")
    sub.add_parser("status")
    for name in ("wait", "auto"):
        w = sub.add_parser(name)
        w.add_argument("--minutes", type=float, default=45)
        w.add_argument("--interval", type=float, default=30)
    a = ap.parse_args()
    for d in (RAW, OUT, CHARTS):
        d.mkdir(parents=True, exist_ok=True)
    if a.cmd == "update":
        fetch_all()
        build()
    elif a.cmd == "build":
        build()
    elif a.cmd == "status":
        latest, rev = bea_state()
        m = cached_meta()
        print(f"cached: {m.get('latest_month')} (rev {m.get('bea_last_revised')})   "
              f"BEA: {latest} (rev {rev})   release due: {release_due(m)}")
    elif a.cmd == "wait":
        sys.exit(wait(a.minutes, a.interval))
    elif a.cmd == "auto":
        sys.exit(auto(a.minutes, a.interval))


if __name__ == "__main__":
    main()
