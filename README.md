# US sector rotation

The FastAPI app serves a professional interactive rotation diagram against SPY, editable ticker groups, saved templates, a deterministic Cantonese Traditional Chinese summary, and long-trail pattern notes for a Macro brief. Groups, tickers, the shared active group, and templates live in PostgreSQL. Only downloaded price snapshots are cached in memory.

The first startup creates **iShare Sector** (`XLK XLF XLE XLY XLP XLV XLI XLB XLU XLRE XLC`) and an empty **Sector ETF** group. Manage groups lets you create, rename, delete, activate, and add/remove any Yahoo-supported US stock or ETF symbol, including `MAGS`, `AIPO`, `DRAM`, `AAPL`, and `BRK-B`. There is no XL-only list. Symbols are uppercased; use Yahoo's share-class spelling such as `BRK-B`. Up to 20 groups and 50 tickers per group. Keep at least one group; empty groups are valid.

Changes in the editor take effect on **Save changes**. The active group is shared across browsers. Conflicting saves from another tab return 409 and ask you to reload. Templates capture **all saved groups, their tickers, and the active group**. Load replaces that whole setup; overwrite replaces the selected template with the current saved setup. Deleting a template leaves groups intact. Legend visibility and the selected trail window are local browser preferences and are not part of a template. Templates continue to capture groups, tickers, and the active group; no database migration is needed for the new chart controls.

Labels occupy spaced side gutters, bilingual quadrant titles sit outside the plot, and the chart grows vertically for large groups. Older trail segments fade; hollow dots mark the start, larger solid dots mark the latest close, and small arrowheads indicate travel direction. The design uses ordinary diagram controls and a dark professional palette.

- Hover a current point or label for its date, quadrant, and X/Y coordinates. Hover a trail to inspect its nearest historical closing point.
- Click a ticker label, point, trail, legend ticker button, or pattern-table ticker to highlight that trail and dim the others. Clicking the same ticker again, **Clear focus**, or Escape inside the chart clears focus. Ticker labels and points also respond to Enter / Space.
- Legend checkboxes and Show/Hide all control visibility independently of focus. Selecting a hidden ticker from its legend button or pattern row shows it again. Hidden trails still contribute to summary and pattern notes.
- Choose **10 / 63 / 126 / 252 / 504 sessions** (roughly two weeks through two years). The server slices one cached coordinate history: changing windows never downloads prices or changes indicator warmup. The latest coordinates stay identical across windows.
- On narrow screens the chart and pattern table scroll horizontally within their cards. Controls and the rest of the page wrap to fit.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DATABASE_URL='postgresql://sector_map:URL_ENCODED_PASSWORD@social-db:5432/social'
# Substitute the actual database name, role, and internal Hostinger/Coolify hostname.
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Open http://localhost:8000. Alternatively, set `DATABASE_URL` in your shell or a local `.env` file and run `docker compose up --build -d`. Compose requires a URL for the existing Postgres service; it does not create a database or config volume. For direct local use, leave `SECTOR_MAP_BASE=` empty (the Compose default). Behind the Coolify proxy described below, set it to `/sectors`.

`DATABASE_URL` accepts `postgresql://` or `postgres://` and optional libpq parameters such as `?sslmode=require`. Alternatively, omit the URL and supply `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, optionally `PGPORT` (5432) and `PGSSLMODE`. Dockerfile deployments accept either form; the supplied Compose file uses `DATABASE_URL`. Credentials belong in Coolify secrets, never the repository. URL-encode special characters in URL passwords.

Startup creates four namespaced tables with `CREATE TABLE IF NOT EXISTS`: `sector_map_config`, `sector_map_groups`, `sector_map_tickers`, and `sector_map_templates`. The role needs schema CREATE and table read/write privileges on first startup. Existing `social-db` tables are untouched. All edits/template loads are transactional; group saves also check the configuration revision. Restarting never reseeds an existing setup. Database absence or connection failure prevents startup; there is no production SQLite fallback. SQLite is available only through explicit unittest dependency injection. An earlier local SQLite configuration is not imported automatically; recreate the setup in Postgres and save a template.

## Summary

Click **Generate summary**, then **Copy Markdown**, or call:

```sh
curl http://localhost:8000/api/summary
curl "http://localhost:8000/api/summary?trail_length=504"
# Public deployment: https://karst.casy.hk/sectors/api/summary
```

JSON includes `text` (pasteable Markdown), `format`, `as_of`, `group`, `config_revision`, `benchmark`, `last_success`, `stale`, `snapshot_id`, and the four `quadrants`. The snapshot ID binds the brief to the exact map, including same-date corrections. No LLM or API key is used. It uses the same cached snapshot as `/api/rotation`, including trails hidden in the legend. With no data yet it returns 503; an empty active group returns a short no-data summary.

The summary lists up to three representatives per quadrant, favors stronger X+Y in Leading/Improving and weaker X+Y in Lagging/Weakening, compares the latest point with the **immediately previous trail point**, prioritizes quadrant crossings and large moves, and finishes with exactly one 👉 observation. Ties break by ticker, so the result is deterministic. Excluded tickers and stale data are labeled explicitly; indicator movements are not described as percentage returns. Regenerate when the map changes. The UI clears an old summary when the trail window, group, or snapshot changes and checks snapshot IDs before showing a response.

## Long-trail patterns

The **Long-trail patterns** panel updates automatically for the selected window. It shows each eligible ticker's actual coverage, clockwise/counterclockwise or mixed/unclear direction, sessions in each quadrant, X/Y axis crossings, and expanding/tightening/steady distance from the centre. Expand **廣東話 ELI5** or click **Copy Markdown** for pasteable Traditional Cantonese notes, including dates, coverage, stale/excluded data, and the meaning of the indicators. Pattern-row ticker buttons also focus the chart.

```sh
curl "http://localhost:8000/api/patterns?trail_length=504"
```

The endpoint returns `patterns` keyed by ticker, `text`, `format`, `trail_length`, `method`, and the same snapshot metadata as the rotation map. For the same window and prices, rotation, summary, and patterns have the same `snapshot_id`. No LLM, external inference service, API key, or random selection is used. Output order is stable by ticker.

Rules (also explained in the UI and returned in `method`):

- **Direction:** sum signed daily angles around (100, 100); positive is counterclockwise. A direction needs at least 0.125 absolute net turns and coherence ≥0.60, where coherence is absolute net angle / total absolute angular travel. Travel below 0.125 turns is unclear; otherwise a trail without a dominant direction is mixed. Segments touching or passing within radius 0.1 of the centre are ignored for direction because their angle is ambiguous; the ignored count is reported.
- **Quadrant time:** count actual daily closing points in each quadrant using the existing ≥100 boundary rules, with percentages. This does not infer intraday time or interpolate missing days.
- **Axis crosses:** count opposite nonzero sides of X=100 or Y=100. Touching an axis and returning to the same side is not a crossing; a run on the axis followed by emerging on the opposite side counts once. Axis equality uses tolerance 0.000001.
- **Expand/tighten:** compare median indicator distance from (100, 100) in the last third and first third of the trail (floor N/3 points each). A change must reach both 15% of the first-third median and 0.1 indicator units. Fewer than six points is insufficient history. This measures indicator distance, not price volatility or percentage returns.

A newer listing, or a ticker with gaps, can have less than the requested history. Only its continuous eligible suffix ending on the latest SPY session is used; it remains eligible if all latest ten sessions are present. Coverage is labelled, for example **80/504 sessions · partial**. We never stretch a shorter history to two years. Long-trail direction describes the full selected window, so it can differ from the most recent movement described by the short summary.

## Refresh and price history

The server downloads five years of daily adjusted closes at startup and every **900 seconds**. Group/universe changes and manual refresh wake it immediately. The browser polls every minute and retries initial downloads sooner. Use **one Uvicorn worker / one replica** to avoid duplicate downloads and independent caches. A download started for a previous group cannot publish into the new group.

Network access to Yahoo Finance is required. Upstream failures retain the last successful snapshot for the same active universe, mark it stale, and retry. Restarting clears price caches, not configuration. Invalid symbols, new listings, missing recent sessions, and insufficient histories appear under **Unavailable tickers**; they do not block eligible tickers or get classified as laggards. Roughly **291 valid daily sessions** are needed for a ten-point normalized trail, and about **785** for a full 504-point trail, so a newly listed AIPO or DRAM may be saved but not charted yet. Eligible shorter histories are shown with explicit partial coverage for longer selected windows.

`GET /api/healthz` reports database-backed process health, storage backend, chart readiness, refresh state, timestamps, and provider errors. Yahoo failures keep health at 200 to avoid restart loops. Database failure returns 503. `/api/rotation`, `/api/summary`, and `/api/patterns` return 503 until a matching nonempty-group price snapshot exists.

## API and token

All GET routes are public. Set optional `SECTOR_MAP_TOKEN` to require `Authorization: Bearer <token>` on **every mutation**. With no token configured, mutations are open. The UI reveals password fields when a token is required; the value stays in page memory and is never stored in localStorage.

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/api/groups` | Groups, `active_id`, revision, `auth_required` |
| PUT | `/api/groups` | Save `{revision, active_id, groups:[{id,name,tickers}]}`; 409 on stale revision |
| GET | `/api/templates` | Named template IDs, names, update times |
| POST | `/api/templates` | Save current setup with `{name}`; 409 on duplicate name |
| PUT | `/api/templates/{id}` | Overwrite that template with current setup |
| POST | `/api/templates/{id}/load` | Restore all groups, tickers, active selection; increment revision |
| DELETE | `/api/templates/{id}` | Delete template; 204 response |
| GET | `/api/rotation?trail_length=504` | Active-group map, requested window, available history, excluded tickers |
| GET | `/api/summary?trail_length=504` | Cantonese Markdown summary from that map |
| GET | `/api/patterns?trail_length=504` | Deterministic trail metrics and Cantonese Markdown notes |
| POST | `/api/refresh` | Queue a refresh; 202 response |
| GET | `/api/healthz` | Health/readiness |

All three map/summary/pattern endpoints default to `trail_length=10` and accept only `10`, `63`, `126`, `252`, or `504`; other values return 422. They share the existing price cache and refresh schedule.

## Method and tests

Download daily `Adj Close` with `auto_adjust=False`; discard today's US session until 16:15 New York time. Each ticker is aligned with SPY on positive, finite adjusted closes. Warmup is independent per ticker; every returned trail must cover the same ten latest valid SPY sessions. The production fetcher retains up to 504 eligible points per ticker; API selection slices this history without recomputing the indicators. The calculation helper keeps a default ten-session window for existing fixture callers.

- `r = ln(ticker / SPY)`; `M = r.diff(21)`.
- `T = M.ewm(span=5, adjust=False, min_periods=5).mean()`.
- `A = T.diff(5)`.
- Normalize T and A independently with trailing 252-session RMS including the current session: `100 + 4*tanh(value / (2*RMS))`. Zero RMS maps to 100.
- X is normalized T, Y normalized A. Leading: X≥100/Y≥100; Improving: X<100/Y≥100; Lagging: X<100/Y<100; Weakening: X≥100/Y<100. Coordinates lie within 96–104.

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check app/static/app.js
# Optional, on a machine permitting localhost sockets with Chrome/Chromium installed:
.venv/bin/python tests/browser_smoke.py
```

The optional browser smoke test exercises the actual page with fixture data (label spacing, mouse/keyboard focus, historical hover, all five windows, partial histories, pattern notes, both Markdown copy actions, summary, arbitrary tickers, templates, toggles, mobile layout, and `/sectors`) and writes desktop, focused long-trail, and mobile screenshots under `artifacts/`. It must run in an environment that permits Chrome and local sockets; restricted sandboxes may prevent it from launching. It requires Chrome/Chromium and the Linux standard Uvicorn extras (`uvloop`, `websockets`).

The local suite uses temporary SQLite databases only for unittest, injected price fixtures, and direct ASGI requests without Yahoo or additional HTTP-client packages. It covers persistence/restarts, template round-trips, conflicting edits, all mutation auth, free tickers, empty/missing/short histories, stale caches, in-flight group changes, prefix URLs, quadrant boundaries, and deterministic summaries. Scalar EMA/RMS checks independently verify the financial calculation. Long-window checks verify unchanged endpoints and warmup, short-listing coverage, and contiguous history after gaps. Synthetic loops, retraced paths, spirals, exact axis touches, flat/centre cases, and boundary fixtures verify the pattern rules. API tests verify shared snapshot IDs across all windows and reject unsupported lengths.

To exercise the same persistence tests against real PostgreSQL, install runtime requirements and set **a test database** with CREATE SCHEMA rights:

```sh
export SECTOR_MAP_TEST_DATABASE_URL='postgresql://test_role:password@localhost:5432/sector_map_test'
.venv/bin/python -m unittest discover -s tests -p test_postgres.py -v
```

These tests create an isolated random schema and remove it afterward; they never use deployment `DATABASE_URL`. They are skipped when the test URL is absent. A passing local SQLite suite alone does not verify connectivity to Hostinger.

For reference-coordinate comparison:

```sh
curl http://localhost:8000/api/rotation > rotation-snapshot.json
.venv/bin/python scripts/compare_reference.py rotation-snapshot.json
```

The supplied Longbridge reference contains coordinates without its adjusted-close input fixture. The comparator requires the same as-of date and checks each coordinate within 0.05. Exact parity is not asserted without matching source prices and adjustment/EMA history.

## Coolify / Hostinger

Create app **sector-map** in project **GrokBot**, alongside the existing `social-db` Postgres service. Build this Dockerfile and connect the app to the network where that database is reachable. Use the actual existing database/service details; no database deployment or shared-table changes are needed.

Set these Coolify runtime variables:

```text
PORT=8000
SECTOR_MAP_BASE=/sectors
DATABASE_URL=postgresql://ROLE:URL_ENCODED_PASSWORD@INTERNAL_POSTGRES_HOST:5432/DATABASE
SECTOR_MAP_TOKEN=OPTIONAL_MANAGEMENT_SECRET
```

Use the internal connection URL from Coolify's Postgres resource; add SSL settings if required by your Hostinger connection. Check **both** `/sectors/api/healthz` (storage must be `postgresql`) and `/sectors/api/groups` after deployment. Verify edits and templates survive an application restart.

Route `https://karst.casy.hk/sectors` through Traefik **StripPrefix `/sectors`** to port 8000. **Coolify/Hostinger must set `SECTOR_MAP_BASE=/sectors` for correct UI URLs.** It is a public URL prefix used only to render HTML: `<base href="/sectors/">`, `/sectors/static/styles.css`, and `/sectors/static/app.js`. Leaving it empty behind this proxy makes the browser request assets at the domain root.

Traefik strips `/sectors` before forwarding, so the app matches internal `/`, `/api/*`, and `/static/*` with no ASGI prefix. `SECTOR_MAP_BASE` does not set FastAPI `root_path`. Middleware must not re-add the prefix to ASGI paths after StripPrefix, which breaks API and static-file routing. Both public `/sectors` and `/sectors/` load with these settings, with no external frontend assets. Check that the page emits the public URLs above and that both `/sectors/static/styles.css` and `/sectors/static/app.js` return 200. Docker health checks use internal `/api/healthz`; the public URL is `/sectors/api/healthz`. No SQLite configuration volume is mounted. Back up the Postgres tables with the existing GrokBot database backups.

Longbridge integration is deferred; no `LONGBRIDGE_*` variables are consumed.
