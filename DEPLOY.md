# Coolify / Hostinger deploy target

- Coolify project: GrokBot (same as social-api / social-db)
- App name: `sector-map`
- Public URL: https://karst.casy.hk/sectors
- Traefik StripPrefix `/sectors`
- Prefer yfinance v1 — no secrets for prices
- Health: `/api/healthz`
- Dockerfile in this folder; listen on PORT (default 8000)
- Env: `SECTOR_MAP_BASE=/sectors`, `DATABASE_URL` (Postgres), optional `SECTOR_MAP_TOKEN`
- Hostinger bot owns Coolify API create/wire; Macro does not maintain Canvas Pages for rotation

Coolify/Hostinger must set `SECTOR_MAP_BASE=/sectors` in the app environment
for the UI to load. This is the public URL prefix used only when rendering HTML:
`<base href="/sectors/">`, `/sectors/static/styles.css`, and
`/sectors/static/app.js`. An empty value is for direct localhost use, and remains
the Docker Compose default.

Traefik StripPrefix removes `/sectors` before forwarding requests. FastAPI must
match the stripped `/`, `/api/*`, and `/static/*` paths with no ASGI prefix.
`SECTOR_MAP_BASE` does not set FastAPI `root_path`; middleware must not re-add the
prefix to ASGI paths, which breaks API and static-file routing.

After deployment, check the HTML at `/sectors/` for the public URLs above, then
check `/sectors/static/styles.css`, `/sectors/static/app.js`, `/sectors/api/groups`,
and `/sectors/api/healthz` return 200. The Dockerfile curl healthcheck continues
to use internal `/api/healthz`.
