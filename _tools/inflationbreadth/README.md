# PCE inflation breadth (source for /inflationbreadth)

The code that builds https://deveshkodnani.com/inflationbreadth/. Jekyll does not publish
folders starting with `_`, so none of this is served on the site.

The workflow `.github/workflows/inflationbreadth.yml` runs on weekdays at 11:50 UTC.
When a PCE release is due, it polls BEA every 30s until the new month lands (up to about
5.5 hours). It then rebuilds the page, encrypts it with `PAGE_PASSWORD`, and commits
`inflationbreadth/index.html` along with `data/raw/meta.json`, the record of the latest
month.

- **Secrets:** `PAGE_PASSWORD` (required) and `BEA_API_KEY` (optional; without it the
  pipeline uses BEA's key-free flat file).
- **Manual rebuild:** Actions → "Update inflation breadth" → Run workflow, with "force" on.
