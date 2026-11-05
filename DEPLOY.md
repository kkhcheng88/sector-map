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
