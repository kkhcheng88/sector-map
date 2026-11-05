# Build US sector rotation map app

Interactive quadrant chart for Karson. Work ONLY inside /workspace/us-sector-rotation.

## Requirements
1. yfinance prices (Longbridge optional later); recompute every 15 minutes server-side; client can poll.
2. Fix UI crowding (see rotation-ui-crowded.png): labels overlap trails and quadrant titles.
3. Legend checkboxes to show/hide each sector trail.
4. Fade older trail; current point solid/larger.

## Method (fixed)
Benchmark SPY. Adjusted closes.
1. rt = ln(AdjClose_i / AdjClose_SPY)
2. M = r_t − r_{t−h}, h=21
3. T = EMA(M, s), s=5
4. A = T_t − T_{t−s}
5. X,Y = 100 + 4 * tanh((2 * T|A / RMS252) / 4)
Quadrants: 01 Leading X≥100 Y≥100 | 02 Improving X<100 Y≥100 | 03 Lagging X<100 Y<100 | 04 Weakening X≥100 Y<100
Universe: XLK XLF XLE XLY XLP XLV XLI XLB XLU XLRE XLC
Trail: last 10 sessions. Validate vs rotation-trails-latest.json within ~0.05 when same as-of.

## Deliver
- FastAPI `/api/rotation` `/api/healthz` + 15-min scheduler
- Frontend with legend toggles + deconflicted labels
- Dockerfile + docker-compose
- README
- Small fixture test
