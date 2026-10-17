# Ticketing Career Radar — collector prototype

This is an **actual scheduled collector framework**, not yet a deployed or live-connected service. It supports public Greenhouse, Lever and Ashby ATS feeds. It does **not** scrape LinkedIn, bypass login, or claim to monitor FIFA/UEFA/F1 automatically.

## Set up

1. Create a GitHub repository and upload these files (including `.github/workflows/refresh.yml`).
2. In `sources.json`, replace the example with **verified** public ATS board tokens. Examples of source format (not verified employers):
   - `{"company":"Company A","adapter":"greenhouse","slug":"company-board-token"}`
   - `{"company":"Company B","adapter":"lever","slug":"company-slug"}`
   - `{"company":"Company C","adapter":"ashby","slug":"company-slug"}`
3. Run `pip install -r requirements.txt && python radar.py`. The output is `data/jobs.json` and `data/jobs.csv`.
4. On GitHub, enable Actions and use **Run workflow** for the first test. The daily schedule is 06:17 UTC (09:17 Qatar), subject to GitHub Actions delays. Commit/push permissions must be enabled.
5. For a static website, publish `index.html` and `data/jobs.json` using GitHub Pages. Browser fetch of the JSON requires both files to share the same origin. The web app does not itself run the collector.

## Limits

- The shipped source token is deliberately a placeholder; until replaced, ingestion will fail visibly rather than fabricate jobs.
- Salary extraction and nationality-specific legal eligibility are **not** implemented; salary is unknown and visa flags are text heuristics only.
- Job status is only marked inactive after a **successful** source fetch, preventing false closures on connection failures.
- Public ATS APIs can change or rate-limit. Only add boards with permission to access their public feeds.
- GitHub Actions schedule is not guaranteed to execute at an exact time; jobs can be delayed.
- Scoring prioritizes ticketing leadership; it is not a probability of hiring.
