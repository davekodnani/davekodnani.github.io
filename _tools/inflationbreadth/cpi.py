"""CPI side of the inflation-breadth page.

Components: the 45 CPI components behind the Cleveland Fed median CPI
(config/components_cleveland45.csv), with owners' equivalent rent split into the four
Census regions. Data: the BLS bulk file cu.data.0.Current (BLS asks automated
clients to send a contact email in the User-Agent; set BLS_CONTACT_EMAIL in .env).

Prices
  12-month changes use not-seasonally-adjusted indexes (the BLS headline convention).
  3- and 6-month changes use seasonally adjusted indexes, chained from NSA where BLS
  publishes no SA version (items it finds non-seasonal). Regional OER has no SA series,
  so it borrows the national OER seasonal factors.
  Interior gaps are filled by log-linear interpolation: October 2025 (never collected,
  2025 lapse in appropriations) and months BLS could not price (e.g. leased cars).

Weights
  BLS relative importance (RI): each December's published RI, carried through the
  following year by relative price change, as BLS does (config/cpi_ri_december.csv).
  National OER weight is split across regions with the Cleveland Fed's regional shares
  (August 2026), carried back and forward by relative regional OER prices.
"""
import io
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import cpi_weights

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
COMPONENTS = ROOT / "config" / "components_cleveland45.csv"
RI_TABLE = ROOT / "config" / "cpi_ri_december.csv"
BULK = "https://download.bls.gov/pub/time.series/cu/cu.data.0.Current"
START = "1997-01"

# Cleveland Fed normalized relative importance for regional OER, August 2026 table.
OER_REGION_SHARE = {"0100": 5.2, "0200": 4.7, "0300": 9.2, "0400": 7.1}
OER_SHARE_MONTH = "2026-08"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def contact():
    email = os.environ.get("BLS_CONTACT_EMAIL")
    env = ROOT / ".env"
    if not email and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("BLS_CONTACT_EMAIL="):
                email = line.split("=", 1)[1].strip()
    if not email:
        raise SystemExit("BLS_CONTACT_EMAIL not set (BLS requires a contact email for scripted downloads)")
    return f"inflation-tools research script ({email})"


def load_components():
    return pd.read_csv(COMPONENTS, dtype={"area_code": str})


def series_ids(comps):
    nsa = {r.idx: f"CUUR{r.area_code}{r.item_code}" for r in comps.itertuples()}
    sa = {r.idx: f"CUSR{r.area_code}{r.item_code}" for r in comps.itertuples() if r.area_code == "0000"}
    return nsa, sa


# ---------------------------------------------------------------- fetch

def fetch():
    comps = load_components()
    nsa_ids, sa_ids = series_ids(comps)
    extra = ["CUUR0000SA0", "CUUR0000SEHC01", "CUSR0000SEHC01"]
    need = set(nsa_ids.values()) | set(sa_ids.values()) | set(extra)

    r = requests.get(BULK, headers={"User-Agent": contact()}, timeout=300)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="\t", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df.apply(lambda c: c.str.strip())
    df = df[df.series_id.isin(need) & df.period.str.match(r"M(0[1-9]|1[0-2])$")]
    df["date"] = pd.to_datetime(df.year + "-" + df.period.str[1:], format="%Y-%m")
    df["value"] = pd.to_numeric(df.value, errors="coerce")  # "-" (no data) -> NaN
    wide = df.pivot_table(index="date", columns="series_id", values="value", aggfunc="last").sort_index()
    # SA series are optional: BLS publishes none for items it finds non-seasonal.
    missing = (set(nsa_ids.values()) | set(extra)) - set(wide.columns)
    if missing:
        raise RuntimeError(f"series missing from BLS file: {sorted(missing)}")

    last = wide["CUUR0000SA0"].last_valid_index()
    absent = [s for s in nsa_ids.values() if pd.isna(wide.loc[last, s])]
    if absent:
        raise RuntimeError(f"incomplete component data for {last:%Y-%m}: {absent}")

    wide.to_csv(RAW / "cpi_bls.csv")
    meta = {"fetched": datetime.now().isoformat(timespec="seconds"),
            "source": "BLS cu.data.0.Current", "latest_month": last.strftime("%Y-%m"),
            "bls_last_modified": r.headers.get("Last-Modified")}
    (RAW / "cpi_meta.json").write_text(json.dumps(meta, indent=2))
    update_ri_table(last.year)
    log(f"CPI fetched through {meta['latest_month']} (BLS file modified {meta['bls_last_modified']})")
    return meta


def update_ri_table(latest_year):
    """Add any newly published December RI tables (BLS posts December Y in February Y+1)."""
    ri = pd.read_csv(RI_TABLE, index_col=0)
    names = list(dict.fromkeys(load_components().ri_name))
    session = requests.Session()
    for year in range(int(ri.index.max()) + 1, latest_year):
        try:
            row = cpi_weights.row_for_year(year, names, session, contact())
        except Exception as e:
            log(f"  December {year} relative importance not available yet ({e!r}); "
                f"carrying December {int(ri.index.max())} forward")
            break
        if any(row[n] is None for n in names):
            log(f"  December {year} RI table missing items; skipping")
            break
        ri.loc[year] = pd.Series(row)
        ri.to_csv(RI_TABLE)
        log(f"  added December {year} relative importance")


# ---------------------------------------------------------------- build

def fill_gaps(df):
    """Log-linear interpolation of interior gaps in each index (never extrapolates).

    Covers October 2025 (not collected during the shutdown; this is the geometric
    midpoint of September and November) and months BLS could not price, e.g. leased
    cars and trucks in 2020-24.
    """
    return np.exp(np.log(df).interpolate(limit_area="inside"))


def chain(sa, nsa):
    """Index whose month-to-month changes are SA where both months exist, else NSA."""
    r_sa, r_nsa = sa / sa.shift(1), nsa / nsa.shift(1)
    ratio = r_sa.where(r_sa.notna(), r_nsa)
    first = nsa.first_valid_index()
    if first is None:
        return nsa * np.nan
    ratio[ratio.index < first] = np.nan
    ratio[first] = 1.0
    return ratio.cumprod() * 100  # NaN before the series starts


def load():
    """(p12, pshort, weights, comps): DataFrames indexed by month, columns = component idx."""
    comps = load_components()
    nsa_ids, sa_ids = series_ids(comps)
    raw = pd.read_csv(RAW / "cpi_bls.csv", index_col=0, parse_dates=True)
    raw = raw[raw.index >= pd.Timestamp(START)]
    raw = fill_gaps(raw)

    p12 = pd.DataFrame({i: raw[s] for i, s in nsa_ids.items()})
    nat_factor = raw["CUSR0000SEHC01"] / raw["CUUR0000SEHC01"]
    pshort = {}
    for r in comps.itertuples():
        nsa = raw[nsa_ids[r.idx]]
        if r.idx in sa_ids:
            sa = raw[sa_ids[r.idx]] if sa_ids[r.idx] in raw else nsa
        else:  # regional OER: borrow national OER seasonal factors
            sa = nsa * nat_factor
        pshort[r.idx] = chain(sa, nsa)
    pshort = pd.DataFrame(pshort)
    weights = relative_importance(p12, comps)
    return p12, pshort, weights, comps


def relative_importance(p12, comps):
    ri = pd.read_csv(RI_TABLE, index_col=0)
    ri.index = ri.index.astype(int)

    # Regional OER split, carried by relative regional OER prices from the anchor month.
    oer = comps[comps.area_code != "0000"]
    anchor = pd.Timestamp(OER_SHARE_MONTH)
    if anchor not in p12.index:
        anchor = p12.index[-1]
    split = pd.DataFrame({r.idx: OER_REGION_SHARE[r.area_code] * p12[r.idx] / p12.loc[anchor, r.idx]
                          for r in oer.itertuples()})
    split = split.div(split.sum(axis=1), axis=0)

    w = pd.DataFrame(index=p12.index, columns=comps.idx, dtype=float)
    for t in p12.index:
        base_year = min(t.year - 1, int(ri.index.max()))
        if base_year not in ri.index:
            continue
        base = pd.Timestamp(f"{base_year}-12-01")
        if base not in p12.index:
            continue
        for r in comps.itertuples():
            b = ri.loc[base_year, r.ri_name]
            if r.area_code != "0000":
                b = b * split.loc[base, r.idx]
            w.loc[t, r.idx] = b * p12.loc[t, r.idx] / p12.loc[base, r.idx]
    return w.astype(float)


def variants(p12, pshort, weights, threshold=3.0):
    """The 6 breadth series at one threshold, same layout as the PCE CSV."""
    import pce_breadth as pb
    cols = {}
    for h, m in pb.HORIZONS.items():
        prices = p12 if m == 12 else pshort
        for wt in pb.WEIGHTINGS:
            cols[f"{h}_{wt}"] = pb.breadth(prices, weights, m, threshold, wt == "weighted")
    df = pd.DataFrame(cols)
    df["n_components"] = pb.growth(p12, 12).notna().sum(axis=1)
    return df.dropna(how="all", subset=list(cols))
