"""Parse BLS CPI relative-importance tables (December, U.S. city average, CPI-U).

1987-2019 come as fixed-width text inside BLS zip archives; 2020+ as one xlsx per year.
`build_table()` produces config/cpi_ri_december.csv (one row per December, one column
per BLS item name we need). `fetch_year()` lets the pipeline add a new December when
BLS publishes it (each February, with January CPI).
"""
import io
import re
import zipfile

import pandas as pd

RI_BASE = "https://www.bls.gov/cpi/tables/relative-importance/"
LINE = re.compile(r"^(\s*)(.*?)[\s.]*\s(\d*\.\d{3})\s+(\d*\.\d{3})\s*$")

# Older tables label OER "of primary residence" before the "of residences" aggregate existed.
ALIASES = {
    "owners' equivalent rent of residences": ["owners' equivalent rent of primary residence"],
    "energy services": ["gas (piped) and electricity"],
}


def norm(s):
    s = re.sub(r"\(\d+\)", "", str(s))
    s = s.replace("’", "'").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().strip(".").strip().lower()


def parse_text(text):
    """{normalized item name: CPI-U RI}, first occurrence wins (detail beats special aggregates)."""
    out, pending = {}, ""
    for raw in text.splitlines():
        m = LINE.match(raw)
        if m:
            name = norm((pending + " " + m.group(2)) if pending else m.group(2))
            pending = ""
            out.setdefault(name, float(m.group(3)))
        elif raw.strip() and not re.search(r"\d+\.\d{3}", raw) and raw.startswith(" "):
            # Item names that wrap onto a second line carry their numbers on the next line.
            pending = (pending + " " + raw.strip()) if pending else raw.strip()
            if len(pending) > 120:
                pending = ""
        else:
            pending = ""
    return out


def parse_xlsx(content):
    df = pd.read_excel(io.BytesIO(content), header=None)
    out = {}
    for _, r in df.iterrows():
        name, val = r.iloc[1], r.iloc[2]
        if isinstance(name, str) and isinstance(val, (int, float)) and pd.notna(val):
            out.setdefault(norm(name), float(val))
    return out


def lookup(table, ri_name):
    n = norm(ri_name)
    for cand in [n] + ALIASES.get(n, []):
        if cand in table:
            return table[cand]
    return None


def table_for_year(year, session, ua):
    if year >= 2020:
        r = session.get(f"{RI_BASE}{year}.xlsx", headers={"User-Agent": ua}, timeout=60)
        r.raise_for_status()
        return parse_xlsx(r.content)
    decade = (1987, 1989) if year < 1990 else (year // 10 * 10, year // 10 * 10 + 9)
    r = session.get(f"{RI_BASE}ri-archive-{decade[0]}-{decade[1]}.zip", headers={"User-Agent": ua}, timeout=120)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = next(n for n in z.namelist() if n.endswith(f"/{year}.txt"))
    return parse_text(z.read(name).decode("latin-1"))


def row_for_year(year, ri_names, session, ua):
    t = table_for_year(year, session, ua)
    row = {n: lookup(t, n) for n in ri_names}
    row["All items"] = lookup(t, "All items")
    return row
