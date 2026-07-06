---
name: verify
description: Build, launch, and drive this app end-to-end to verify a change by observing real behavior in the browser and at the API. Use before committing any nontrivial change.
---

# Verifying changes in this repo

Verification here means driving the running app, not running the test suite.
Both servers must be up; then exercise the changed surface in a real browser.

## Launch

```bash
# Backend (FastAPI, port 8001) — from server/
uv run python main.py

# Frontend (Vite, port 3000) — from client/
npm run dev
```

Or `./scripts/start.sh` for both. Health checks:
`http://localhost:8001/docs` and `http://localhost:3000`.

**The backend does NOT hot-reload.** `main.py` runs uvicorn without
`reload=True`, so any change to `server/*.py` or `server/data/*.json`
requires killing and restarting the backend before it is observable.
The Vite frontend hot-reloads on its own.

## Drive

Use the Playwright MCP tools (`mcp__playwright__browser_*`) against
`http://localhost:3000`, per this repo's CLAUDE.md. If the MCP reports no
browser installed, run `npx playwright install chrome` once first.

If the Playwright MCP is unavailable, a plain Playwright Node script works
identically: `chromium.launch({ channel: 'chrome', headless: true })`.

Surfaces worth driving per area:
- **A view/component change** — navigate to its route, interact, screenshot.
  Routes: `/` `/inventory` `/orders` `/restocking` `/spending` `/demand` `/reports`.
- **A filter change** — drive the real `FilterBar` selects (identified by
  their `<label>` text: Time Period, Location, Category, Order Status), not
  the API. Every view except Reports and Restocking reacts to them.
- **An API change** — hit the endpoint with curl AND drive whichever view
  consumes it; both, not just curl.
- **A data (`server/data/*.json`) change** — restart the backend, then load
  the views that render that dataset.
- **i18n** — `localStorage.setItem('app-locale', 'ja')` then reload switches
  the whole app (labels, currency symbol) to Japanese.

## Gotchas that will produce false failures

- **`.badge` text renders UPPERCASE.** The global `.badge` class applies
  `text-transform: uppercase`, so `innerText` on a status/trend/coverage
  badge returns `SUBMITTED`, not `Submitted`. Assert case-insensitively, or
  match against DOM text (`:text-is(...)`) which ignores CSS transforms.
- **`GET /api/tasks` 404s on every page load.** `App.vue` calls a tasks
  endpoint that has never existed on the server. The resulting console error
  and 404 are pre-existing background noise, not evidence that your change
  broke something. Any *new* console error or non-2xx `/api/*` response is.
- **Restocking orders are in-memory only.** They reset on every backend
  restart; start a verification from a restart if you need an empty order
  book.
