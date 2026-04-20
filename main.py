import os
import re
import csv
import io
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, date, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

import notifier

load_dotenv()

ET = ZoneInfo("America/New_York")
_scheduler = AsyncIOScheduler(timezone="America/New_York")


async def _daily_check_job():
    print(f"[monitor] Daily check running ({datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')})")
    try:
        data  = get_data(force=True)
        alert = notifier.check_and_notify(data)
        if alert:
            print(f"[monitor] Alert fired: {alert['type']}")
    except Exception as e:
        print(f"[monitor] Check error: {e}")


def _next_check_iso() -> str:
    hour   = int(os.getenv("MONITOR_HOUR", "17"))
    minute = int(os.getenv("MONITOR_MINUTE", "5"))
    now    = datetime.now(ET)
    today  = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < today and now.weekday() < 5:
        nxt = today
    else:
        days = 1
        while True:
            candidate = now + timedelta(days=days)
            if candidate.weekday() < 5:
                nxt = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
                break
            days += 1
    return nxt.astimezone(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    hour   = int(os.getenv("MONITOR_HOUR", "17"))
    minute = int(os.getenv("MONITOR_MINUTE", "5"))
    _scheduler.add_job(
        _daily_check_job,
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
        id="daily_vix_check",
        replace_existing=True,
    )
    _scheduler.start()
    print(f"[monitor] Scheduler live — daily check at {hour:02d}:{minute:02d} ET (Mon-Fri)")
    # Establish baseline state on startup (no alert fired)
    try:
        notifier.init_state(get_data())
    except Exception as e:
        print(f"[monitor] Startup init warning: {e}")
    yield
    _scheduler.shutdown()


app = FastAPI(title="VIX Term Structure Monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CBOE_URL = "https://www.cboe.com/us/futures/market_statistics/settlement/csv/?dt={}"

# CBOE futures month codes → (month_name, month_number)
MONTH_CODES = {
    'F': ('Jan', 1),  'G': ('Feb', 2),  'H': ('Mar', 3),  'J': ('Apr', 4),
    'K': ('May', 5),  'M': ('Jun', 6),  'N': ('Jul', 7),  'Q': ('Aug', 8),
    'U': ('Sep', 9),  'V': ('Oct', 10), 'X': ('Nov', 11), 'Z': ('Dec', 12),
}

# In-memory cache
_cache: dict = {}
_cache_ts: float = 0
CACHE_TTL = 60  # seconds


# ─── helpers ──────────────────────────────────────────────────────────────────

def get_recent_trading_dates(n: int = 5) -> list:
    """Return the n most recent Mon-Fri dates ending today."""
    dates = []
    d = date.today()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    return dates


def fetch_cboe_csv(target_date: date) -> Optional[str]:
    url = CBOE_URL.format(target_date.strftime("%Y-%m-%d"))
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/csv,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.cboe.com/",
            },
        )
        if resp.status_code == 200 and len(resp.text.strip()) > 50:
            return resp.text
        print(f"CBOE {target_date}: status={resp.status_code}, len={len(resp.text)}")
    except requests.RequestException as e:
        print(f"CBOE fetch error {target_date}: {e}")
    return None


def _get_price(row: dict) -> Optional[float]:
    """Try multiple column name variants for settlement price."""
    for col in ("Price", "Settlement", "Settlement Price", "Settle",
                "Final Settlement", "Final Settlement Price", "Close", "Last"):
        v = row.get(col, "").strip().replace(",", "")
        if v:
            try:
                return float(v)
            except ValueError:
                pass
    # fallback: any col containing price/settle but not change/pct
    for k, v in row.items():
        kl = k.lower()
        if any(x in kl for x in ("price", "settle", "close")) and "change" not in kl and "%" not in kl:
            v = v.strip().replace(",", "")
            if v:
                try:
                    return float(v)
                except ValueError:
                    pass
    return None


def _get_expiry(row: dict, month_code: str, year_2d: int) -> date:
    """Parse expiry from CSV row, or compute a reasonable approximation."""
    for col in ("Exp Date", "Expiration Date", "Expiration", "Exp", "Maturity Date", "Maturity"):
        v = row.get(col, "").strip()
        if v:
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%b %d %Y", "%b %d, %Y"):
                try:
                    return datetime.strptime(v, fmt).date()
                except ValueError:
                    pass

    # Approximate: 3rd Wednesday of the expiry month
    now = datetime.now()
    century = (now.year // 100) * 100
    full_year = century + year_2d
    if full_year < now.year - 1:
        full_year += 100
    month_num = MONTH_CODES[month_code][1]
    d = date(full_year, month_num, 1)
    # Find first Wednesday (weekday 2)
    days_to_wed = (2 - d.weekday()) % 7
    third_wed_day = 1 + days_to_wed + 14  # +14 = two more weeks
    try:
        return date(full_year, month_num, third_wed_day)
    except ValueError:
        return date(full_year, month_num, 15)


def parse_vx_futures(csv_text: str) -> list:
    """Parse monthly VX futures from CBOE settlement CSV text."""
    lines = csv_text.strip().splitlines()

    # Find the header row (must contain 'Symbol')
    header_idx = None
    for i, line in enumerate(lines):
        if re.search(r'\bSymbol\b', line, re.IGNORECASE):
            header_idx = i
            break
    if header_idx is None:
        print("CSV parse: no Symbol header found")
        return []

    data_block = "\n".join(lines[header_idx:])

    # Auto-detect delimiter
    first = lines[header_idx]
    sep = "|" if "|" in first else ("\t" if "\t" in first else ",")

    try:
        reader = csv.DictReader(io.StringIO(data_block), delimiter=sep)
        rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in reader]
    except Exception as e:
        print(f"CSV DictReader error: {e}")
        return []

    futures = []
    for row in rows:
        symbol = row.get("Symbol", "").strip()

        # Monthly VX: VX/[A-Z]\d{1,2} — single letter month code + 1-2 digit year
        m = re.match(r"^VX/([A-Z])(\d{1,2})$", symbol)
        if not m:
            continue

        mc = m.group(1)
        yr = int(m.group(2))

        if mc not in MONTH_CODES:
            continue

        price = _get_price(row)
        if price is None or price <= 0:
            continue

        expiry = _get_expiry(row, mc, yr)
        month_name = MONTH_CODES[mc][0]

        futures.append({
            "symbol": symbol,
            "label": "",
            "month": f"{month_name} {expiry.year}",
            "expiry": expiry.isoformat(),
            "price": round(price, 4),
        })

    if not futures:
        print(f"CSV parse: 0 VX rows found. Sample row: {rows[0] if rows else 'none'}")
        return []

    # Remove contracts more than 45 days past expiry (cleanup)
    today = date.today()
    futures = [f for f in futures if date.fromisoformat(f["expiry"]) >= today - timedelta(days=45)]
    futures.sort(key=lambda x: x["expiry"])

    # Label VX1–VX6
    for i, f in enumerate(futures[:6]):
        f["label"] = f"VX{i + 1}"

    return futures[:6]


# ─── core data fetch ──────────────────────────────────────────────────────────

def _fetch_fresh() -> dict:
    # VIX spot via yfinance
    vix_spot = None
    try:
        info = yf.Ticker("^VIX").fast_info
        vix_spot = round(float(info["last_price"]), 2)
    except Exception as e:
        print(f"VIX spot error: {e}")

    # Walk back up to 5 trading days for CBOE settlement
    futures: list = []
    as_of: Optional[str] = None

    for td in get_recent_trading_dates(5):
        csv_text = fetch_cboe_csv(td)
        if csv_text:
            parsed = parse_vx_futures(csv_text)
            if parsed:
                futures = parsed
                as_of = td.isoformat()
                break
        time.sleep(0.3)

    # Spread & structure
    vx1 = next((f for f in futures if f["label"] == "VX1"), None)
    vx3 = next((f for f in futures if f["label"] == "VX3"), None)

    spread = None
    structure = None
    if vx1 and vx3:
        spread = round(vx3["price"] - vx1["price"], 4)
        structure = "BACKWARDATION" if spread < 0 else "CONTANGO"

    return {
        "as_of": as_of,
        "vix_spot": vix_spot,
        "futures": futures,
        "spread": spread,
        "structure": structure,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }


def get_data(force: bool = False) -> dict:
    global _cache, _cache_ts
    if not force and _cache and (time.time() - _cache_ts) < CACHE_TTL:
        return _cache
    _cache = _fetch_fresh()
    _cache_ts = time.time()
    return _cache


# ─── routes ───────────────────────────────────────────────────────────────────

@app.get("/api/vix")
async def api_get_vix():
    return get_data()


@app.post("/api/vix/refresh")
async def api_refresh():
    return get_data(force=True)


class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set — add it to .env")

    try:
        import anthropic as ant
    except ImportError:
        raise HTTPException(500, "Run: pip install anthropic")

    vix = get_data()

    futures_str = "  |  ".join(
        f"{f['label']} ({f['month']}): {f['price']:.2f}"
        for f in vix.get("futures", [])
    )

    spread = vix.get("spread")
    spread_str = f"{spread:+.4f}" if spread is not None else "N/A"
    structure = vix.get("structure", "N/A")
    signal = "BEARISH" if structure == "BACKWARDATION" else "NEUTRAL"

    system_prompt = f"""You are a terse volatility analyst specializing in VIX term structure and the McMillan VX3-VX1 signal. Respond in tight prose only — no bullet points, no headers, no markdown. Maximum 160 words.

LIVE DATA (settlement {vix.get('as_of', 'N/A')}):
VIX Spot: {vix.get('vix_spot', 'N/A')}
Futures chain: {futures_str or 'Unavailable'}
VX3−VX1 Spread: {spread_str}
Term Structure: {structure}
McMillan Signal: {signal}

MCMILLAN RULE: "When VX3−VX1 turns negative, it is time to be negative on stocks until this spread returns to a positive status."

SIGNAL HISTORY:
- SqueezeMetrics: SELL signal issued Jan 27 2025. No buy signal as of Mar 2026.
- McMillan VX3-VX1: Currently {signal} (spread {spread_str}, {structure}).

Answer the user concisely using live data above. Tight prose, no bullets, max 160 words."""

    try:
        client = ant.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": req.message}],
        )
        if not msg.content:
            raise HTTPException(500, "Anthropic returned empty response")
        return {"response": msg.content[0].text}
    except HTTPException:
        raise
    except ant.APIError as e:
        raise HTTPException(500, f"Anthropic error: {e}")
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


# ─── Monitor / notification routes ────────────────────────────────────────────

@app.get("/api/signal/state")
async def api_signal_state():
    """Return persisted signal state, alert history, and scheduler info."""
    state      = notifier.get_state()
    configured = notifier.notify_configured()
    hour       = int(os.getenv("MONITOR_HOUR", "17"))
    minute     = int(os.getenv("MONITOR_MINUTE", "5"))
    return {
        **state,
        "next_check_utc":  _next_check_iso(),
        "check_schedule":  f"Weekdays {hour:02d}:{minute:02d} ET",
        "notifications":   configured,
        "scheduler_running": _scheduler.running,
    }


@app.post("/api/signal/check")
async def api_signal_check():
    """Manually trigger a signal check (same as the daily job)."""
    data  = get_data(force=True)
    alert = notifier.check_and_notify(data)
    state = notifier.get_state()
    return {
        "vix_data": data,
        "alert_fired": alert,
        "current_state": state,
    }


@app.post("/api/signal/test")
async def api_signal_test():
    """Send a test email + SMS using current live data."""
    data   = get_data()
    result = notifier.send_test_notification(data)
    return result
