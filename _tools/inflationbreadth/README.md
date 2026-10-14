# Inflation breadth (PCE and CPI)

Share of price categories with inflation at or above X%. The live page is
https://deveshkodnani.com/inflationbreadth/ (password-gated). A Measure toggle switches
between PCE and CPI; every other control works the same for both.

| | PCE | CPI |
|---|---|---|
| Categories | 177 Dallas Fed trimmed-mean categories (`config/components_dallas177.csv`) | 45 Cleveland Fed median-CPI components, with owners' equivalent rent (OER) in 4 regions (`config/components_cleveland45.csv`) |
| Prices | BEA Table 2.4.4U (seasonally adjusted) | BLS CPI-U: 12-month changes not seasonally adjusted; 3- and 6-month changes seasonally adjusted |
| Weights | Current-month nominal spending (BEA 2.4.5U) | BLS relative importance: each December's table (`config/cpi_ri_december.csv`), carried forward by relative prices |
| Source | BEA API, falling back to the BEA flat file | BLS bulk file `cu.data.0.Current` |

CPI details:
- **Gaps:** interior gaps are filled by log-linear interpolation. This covers October 2025, which wasn't collected during the shutdown, and months BLS couldn't price, such as leased cars in 2020–24.
- **Regional OER seasonal adjustment:** regional OER borrows the national OER seasonal factors.
- **Regional OER weights:** the regional split comes from the Cleveland Fed's August 2026 shares, carried by regional prices.
- **Validation:** the median CPI recomputed from these components and weights matches the Cleveland Fed's within about 0.15pp on average.

## Updating after a release
```
python3 pce_breadth.py release   # fetch PCE + CPI, rebuild, encrypt, push to the site, wait until live
```
- **If a source is down:** `update` keeps that source's cached data and prints a warning.
- **New weights each February:** when BLS posts a new December relative-importance table (with January CPI), the CPI fetch adds it to `config/cpi_ri_december.csv` automatically.

Other commands:
- `update` fetches and rebuilds locally.
- `build` rebuilds from the cache.
- `publish` pushes the built page.

## Settings in `.env`
| Key | Notes |
|---|---|
| `BEA_API_KEY` | Optional; there's a key-free fallback. |
| `BLS_CONTACT_EMAIL` | BLS requires a contact email for scripted downloads. |
| `PAGE_PASSWORD` | Password for the page gate. |

## Where things live
- `site/` is a local clone of `davekodnani/davekodnani.github.io`.
- `publish` writes the encrypted page to `site/inflationbreadth/index.html` and copies this code to `site/_tools/inflationbreadth/` for reference. Jekyll doesn't serve that folder.
- The GitHub Actions auto-updater (`.github/workflows/inflationbreadth.yml`) is disabled. GitHub's scheduler kept starting runs hours late, so updates are now run on request.
