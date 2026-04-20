"""
VIX Signal Monitor — Notification & State Management

Tracks VX3-VX1 structure transitions and fires email + SMS alerts
when the signal changes (both bullish and bearish crossovers).
State persists in signal_state.json between restarts.
"""
import email.policy
import json
import os
import re
import smtplib

import requests
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_FILE = Path(os.getenv("STATE_DIR", str(Path(__file__).parent))) / "signal_state.json"
ET = ZoneInfo("America/New_York")

# Free email-to-SMS gateways — no API key, no cost
SMS_GATEWAYS = {
    "att":        "@txt.att.net",
    "verizon":    "@vtext.com",
    "tmobile":    "@tmomail.net",
    "sprint":     "@messaging.sprintpcs.com",
    "boost":      "@sms.myboostmobile.com",
    "cricket":    "@mms.cricketwireless.net",
    "metro":      "@mymetropcs.com",
    "uscellular": "@email.uscc.net",
    "virgin":     "@vmobl.com",
}


# ─── State persistence ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "last_structure":  None,
        "last_spread":     None,
        "last_as_of":      None,
        "last_check_utc":  None,
        "initialized":     False,
        "alerts_sent":     [],
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def get_state() -> dict:
    return load_state()


def init_state(vix_data: dict) -> None:
    """Called once on app startup to establish baseline without alerting."""
    state = load_state()
    if state.get("initialized"):
        print(
            f"[monitor] Resuming — last state: {state.get('last_structure')}, "
            f"spread={state.get('last_spread')}, as_of={state.get('last_as_of')}"
        )
        return
    state.update({
        "last_structure": vix_data.get("structure"),
        "last_spread":    vix_data.get("spread"),
        "last_as_of":     vix_data.get("as_of"),
        "last_check_utc": datetime.now(timezone.utc).isoformat(),
        "initialized":    True,
    })
    save_state(state)
    print(
        f"[monitor] Baseline set: {state['last_structure']}, "
        f"spread={state['last_spread']}, as_of={state['last_as_of']}"
    )


# ─── Email / SMS ──────────────────────────────────────────────────────────────

def _smtp_send(to_addrs: list, subject: str, body: str) -> bool:
    host = os.getenv("NOTIFY_SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("NOTIFY_SMTP_PORT", "587"))
    frm  = os.getenv("NOTIFY_EMAIL_FROM", "").strip()
    pwd  = os.getenv("NOTIFY_EMAIL_PASSWORD", "").strip()

    if not (frm and pwd and to_addrs):
        print("[notifier] Email not configured — skipping (set NOTIFY_EMAIL_FROM/PASSWORD/TO in .env)")
        return False

    # Use modern EmailMessage API with SMTP policy — fully UTF-8 safe on Windows
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["Subject"] = subject
    msg["From"]    = frm
    msg["To"]      = ", ".join(to_addrs)
    msg.set_content(body, charset="utf-8")

    try:
        with smtplib.SMTP(host, port, timeout=15) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(frm, pwd)
            srv.send_message(msg)
        print(f"[notifier] Email sent to {to_addrs}")
        return True
    except Exception as e:
        import traceback
        print(f"[notifier] Email error: {e}")
        print("[notifier] Full traceback:")
        traceback.print_exc()
        return False


def send_email(subject: str, body: str) -> bool:
    raw = os.getenv("NOTIFY_EMAIL_TO", "")
    to  = [a.strip() for a in raw.split(",") if a.strip()]
    return _smtp_send(to, subject, body)


def send_sms(body: str) -> bool:
    """
    Send push notification via ntfy.sh (preferred) or fall back to
    email-to-SMS carrier gateway.

    ntfy.sh setup:
      1. Install the ntfy app (iOS / Android) from ntfy.sh
      2. Subscribe to your topic in the app
      3. Set NOTIFY_NTFY_TOPIC=your-unique-topic in .env
    """
    # ── ntfy.sh push notification (preferred) ────────────────────────────────
    ntfy_topic = os.getenv("NOTIFY_NTFY_TOPIC", "").strip()
    if ntfy_topic:
        try:
            resp = requests.post(
                f"https://ntfy.sh/{ntfy_topic}",
                data=body[:500].encode("utf-8"),
                headers={
                    "Title": "VIX Term Structure Alert",
                    "Priority": "high",
                    "Tags": "chart_with_upwards_trend",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"[notifier] ntfy.sh push sent → topic={ntfy_topic}")
                return True
            print(f"[notifier] ntfy.sh error: HTTP {resp.status_code}")
        except Exception as e:
            print(f"[notifier] ntfy.sh error: {e}")
        return False

    # ── Email-to-SMS fallback ─────────────────────────────────────────────────
    phone   = re.sub(r"\D", "", os.getenv("NOTIFY_PHONE", ""))
    carrier = os.getenv("NOTIFY_CARRIER", "").lower().strip()
    gateway = SMS_GATEWAYS.get(carrier)

    if not (phone and gateway):
        if phone or carrier:
            print("[notifier] SMS not sent — set NOTIFY_NTFY_TOPIC or check NOTIFY_PHONE/CARRIER in .env")
        return False

    sms_to = f"{phone}{gateway}"
    short  = body[:155] + "..." if len(body) > 155 else body
    return _smtp_send([sms_to], "VIX Alert", short)


def notify_configured() -> dict:
    """Return which notification channels are configured."""
    email_ok = bool(
        os.getenv("NOTIFY_EMAIL_FROM")
        and os.getenv("NOTIFY_EMAIL_PASSWORD")
        and os.getenv("NOTIFY_EMAIL_TO")
    )
    ntfy_ok = bool(os.getenv("NOTIFY_NTFY_TOPIC", "").strip())
    sms_ok  = ntfy_ok or bool(
        os.getenv("NOTIFY_PHONE")
        and os.getenv("NOTIFY_CARRIER")
        and SMS_GATEWAYS.get(os.getenv("NOTIFY_CARRIER", "").lower())
    )
    return {"email": email_ok, "sms": sms_ok, "ntfy": ntfy_ok}


# ─── Core check logic ─────────────────────────────────────────────────────────

def check_and_notify(vix_data: dict) -> dict | None:
    """
    Compare current signal to saved state.
    Fire alert on CONTANGO ↔ BACKWARDATION transitions.
    Returns alert dict if fired, else None.
    """
    state     = load_state()
    structure = vix_data.get("structure")
    spread    = vix_data.get("spread")
    as_of     = vix_data.get("as_of")
    spot      = vix_data.get("vix_spot")
    prev      = state.get("last_structure")
    now_utc   = datetime.now(timezone.utc).isoformat()

    state["last_check_utc"] = now_utc
    alert = None

    # Only alert on a real transition — never on first run (prev is None)
    if prev is not None and structure is not None and prev != structure and spread is not None:
        spread_str = f"{spread:+.4f}"

        if structure == "CONTANGO":
            # ── Bullish crossover ───────────────────────────────────────
            subject   = "[VIX] McMillan Signal Cleared - CONTANGO"
            headline  = "VX3-VX1 spread returned to positive. McMillan signal is now NEUTRAL."
            detail    = (
                'McMillan: "...until this spread returns to a positive status." - that condition is now met.\n\n'
                "SqueezeMetrics SELL remains active (issued Jan 27 2025). Watch for a confirmed buy signal before adding risk."
            )
            sms_body  = (
                f"VIX NEUTRAL: VX3-VX1 spread {spread_str}. Structure CONTANGO. "
                f"McMillan signal cleared. VIX={spot}"
            )
            kind = "BULLISH_CROSSOVER"
        else:
            # ── Bearish crossover ───────────────────────────────────────
            subject   = "[VIX] McMillan Signal Triggered - BACKWARDATION"
            headline  = "VX3-VX1 spread turned negative. McMillan signal is BEARISH."
            detail    = (
                'McMillan: "When VX3-VX1 turns negative, it is time to be negative on stocks '
                'until this spread returns to a positive status."\n\n'
                "SqueezeMetrics SELL also active (issued Jan 27 2025). Both signals aligned bearish."
            )
            sms_body  = (
                f"VIX BEARISH: VX3-VX1 spread {spread_str}. Structure BACKWARDATION. "
                f"McMillan SELL triggered. VIX={spot}"
            )
            kind = "BEARISH_CROSSOVER"

        body = (
            f"{headline}\n\n"
            f"Settlement Date : {as_of}\n"
            f"VIX Spot        : {spot}\n"
            f"VX3−VX1 Spread  : {spread_str}\n"
            f"Structure       : {structure}\n"
            f"Previous        : {prev}\n\n"
            f"{detail}\n\n"
            f"── VIX Term Structure Monitor ──"
        )

        email_ok = send_email(subject, body)
        sms_ok   = send_sms(sms_body)

        alert = {
            "type":       kind,
            "timestamp":  now_utc,
            "as_of":      as_of,
            "spread":     spread,
            "structure":  structure,
            "prev":       prev,
            "email_sent": email_ok,
            "sms_sent":   sms_ok,
        }
        state["alerts_sent"] = (state.get("alerts_sent") or [])[-49:] + [alert]
        print(f"[monitor] ALERT FIRED: {kind} | spread={spread_str} | email={email_ok} | sms={sms_ok}")
    else:
        spread_disp = f"{spread:+.4f}" if spread is not None else "N/A"
        print(f"[monitor] Check complete. Structure={structure}, Spread={spread_disp}, Prev={prev} — no transition")

    # Persist updated state
    state.update({
        "last_structure": structure,
        "last_spread":    spread,
        "last_as_of":     as_of,
        "initialized":    True,
    })
    save_state(state)
    return alert


def send_test_notification(vix_data: dict) -> dict:
    """Send a test alert using live data, regardless of signal state."""
    spot      = vix_data.get("vix_spot")
    spread    = vix_data.get("spread")
    structure = vix_data.get("structure")
    as_of     = vix_data.get("as_of")
    spread_str = f"{spread:+.4f}" if spread is not None else "N/A"

    subject = "[VIX Monitor] Test Notification"
    body = (
        f"This is a test alert from your VIX Term Structure Monitor.\n\n"
        f"Current Live Data:\n"
        f"Settlement Date : {as_of}\n"
        f"VIX Spot        : {spot}\n"
        f"VX3-VX1 Spread  : {spread_str}\n"
        f"Structure       : {structure}\n\n"
        f"If you received this, email notifications are working correctly.\n\n"
        f"-- VIX Term Structure Monitor --"
    )
    sms_body = f"VIX Monitor test: VIX={spot}, spread={spread_str}, {structure}"

    email_ok = send_email(subject, body)
    sms_ok   = send_sms(sms_body)

    return {"email_sent": email_ok, "sms_sent": sms_ok, "configured": notify_configured()}
