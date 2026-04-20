# VIX Term Structure Monitor
### McMillan VX3−VX1 Signal Dashboard

Dark terminal dashboard for monitoring VIX futures term structure and the McMillan backwardation signal.

---

## Quick Start

### 1 — Backend

```bash
pip install fastapi uvicorn yfinance requests python-dotenv anthropic

# Copy and fill in your Anthropic API key
cp .env.example .env
# Edit .env: ANTHROPIC_API_KEY=sk-ant-...

PYTHONUTF8=1 uvicorn main:app --reload --port 8002
# → http://localhost:8002
```

### 2 — Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

Or build and open statically:
```bash
cd frontend
npm run build
# then open frontend/dist/index.html in your browser
```

---

## Architecture

```
VIX Structure App/
├── main.py            ← FastAPI backend
├── .env               ← ANTHROPIC_API_KEY (gitignore this)
├── .env.example
└── frontend/
    ├── src/App.jsx    ← React dashboard
    ├── src/index.css  ← Dark terminal styles (IBM Plex Mono)
    ├── vite.config.js ← Vite dev server + proxy
    └── package.json
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/vix` | Returns VIX spot + VX1–VX6 futures chain (60s cache) |
| `POST` | `/api/vix/refresh` | Force-refresh, bypass cache |
| `POST` | `/api/chat` | Claude analyst with live data in context |

### `GET /api/vix` response shape

```json
{
  "as_of":     "2026-03-28",
  "vix_spot":  18.42,
  "futures": [
    { "label": "VX1", "month": "Apr 2026", "expiry": "2026-04-15", "price": 19.10 },
    { "label": "VX2", "month": "May 2026", "expiry": "2026-05-20", "price": 20.05 },
    ...
  ],
  "spread":    1.2500,
  "structure": "CONTANGO",
  "fetched_at": "2026-03-28T14:30:00Z"
}
```

- `spread` = VX3 price − VX1 price
- `structure` = `"BACKWARDATION"` if spread < 0, else `"CONTANGO"`

---

## Signal Logic

| Spread | Structure | McMillan Signal |
|--------|-----------|-----------------|
| < 0    | BACKWARDATION | **BEARISH** |
| ≥ 0    | CONTANGO      | **NEUTRAL** |

> *"When VX3−VX1 turns negative, it is time to be negative on stocks until this spread returns to a positive status."*
> — Lawrence McMillan

---

## Data Sources

- **Futures settlement**: CBOE public CSV — no API key required
  `https://www.cboe.com/us/futures/market_statistics/settlement/csv/?dt=YYYY-MM-DD`
  Walks back up to 5 trading days to find the most recent settlement.

- **VIX spot**: Yahoo Finance via `yfinance` (`^VIX`)

- **Claude Q&A**: Anthropic API (claude-sonnet-4-6), live prices injected into system prompt

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (for chat) | Your Anthropic API key |
