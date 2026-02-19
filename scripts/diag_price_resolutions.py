"""
DIAGNÓSTICO: Alertas resueltas incorrectamente por odds=1.0

Lee el inventario completo de alertas con outcome='correct' y
odds_max=1.0 o odds_min=1.0, y las clasifica en:

  A) end_date en el FUTURO → FALSAS SEGURAS (mercado todavía abierto)
  B) end_date en el PASADO → verificar via CLOB si winner=True
      B1) CLOB confirma winner → resolución correcta
      B2) CLOB no confirma   → FALSA (resolved por precio sin API)
      B3) CLOB inalcanzable  → INDETERMINADA

Solo lectura. No modifica nada.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
CLOB_DELAY = 0.12   # polite rate-limit


def get_db():
    from src.database.supabase_client import SupabaseClient
    return SupabaseClient()


def fetch_candidates(db) -> list[dict]:
    """All alerts with outcome='correct' and odds_max=1.0 OR odds_min=1.0."""
    rows = (
        db.client.table("alerts")
        .select(
            "id,market_id,direction,odds_at_alert,odds_max,odds_min,"
            "odds_max_date,odds_min_date,resolved_at,market_question,"
            "created_at,star_level"
        )
        .eq("outcome", "correct")
        .or_("odds_max.eq.1,odds_min.eq.1")
        .order("resolved_at", desc=True)
        .execute()
        .data
    ) or []
    return rows


def check_clob(market_id: str, session: requests.Session) -> dict:
    """
    Calls CLOB GET /markets/{condition_id}.
    Returns:
        {"status": "winner_yes"|"winner_no"|"closed_no_winner"|"open"|"error",
         "closed": bool, "end_date": str|None}
    """
    try:
        resp = session.get(f"{CLOB_BASE}/markets/{market_id}", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        returned_id = data.get("condition_id", "")
        if returned_id != market_id:
            return {"status": "error", "note": "condition_id mismatch",
                    "closed": None, "end_date": None}

        closed = bool(data.get("closed", False))
        end_date = data.get("end_date_iso") or data.get("end_date")

        # Check winner flag
        for token in data.get("tokens") or []:
            if token.get("winner") is True:
                outcome = (token.get("outcome") or "").upper()
                if outcome in ("YES", "NO"):
                    return {"status": f"winner_{outcome.lower()}",
                            "closed": closed, "end_date": end_date}

        if closed:
            return {"status": "closed_no_winner", "closed": True, "end_date": end_date}
        return {"status": "open", "closed": False, "end_date": end_date}

    except Exception as e:
        return {"status": "error", "note": str(e)[:80],
                "closed": None, "end_date": None}


def parse_dt(val) -> datetime | None:
    if not val:
        return None
    from dateutil import parser as dtp
    try:
        dt = dtp.parse(str(val))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def main():
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL y SUPABASE_KEY deben estar en el entorno.")
        sys.exit(1)

    db = get_db()
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    # ── 1. Fetch candidates ─────────────────────────────────
    logger.info("Consultando alertas con outcome='correct' y odds=1.0...")
    candidates = fetch_candidates(db)
    logger.info("Candidatos encontrados: %d", len(candidates))

    if not candidates:
        logger.info("Ninguna alerta afectada. Todo OK.")
        return

    now = datetime.now(timezone.utc)

    # ── 2. Classify ─────────────────────────────────────────
    future_end:         list[dict] = []   # A: end_date futuro → falsa segura
    clob_confirmed:     list[dict] = []   # B1: CLOB winner=True, correcto
    clob_no_winner:     list[dict] = []   # B2: CLOB closed pero sin winner
    clob_open:          list[dict] = []   # B2b: CLOB dice open
    clob_error:         list[dict] = []   # B3: API falla

    seen_markets: dict[str, dict] = {}   # cache CLOB by market_id

    logger.info("Verificando %d alertas contra CLOB API...", len(candidates))

    for i, alert in enumerate(candidates, 1):
        mid = alert.get("market_id", "")
        direction = (alert.get("direction") or "YES").upper()
        q_short = (alert.get("market_question") or "?")[:55]

        # Check end_date from the alert's resolved_at or odds dates
        # We'll check CLOB for the definitive end_date
        if mid not in seen_markets:
            time.sleep(CLOB_DELAY)
            seen_markets[mid] = check_clob(mid, session)

        clob = seen_markets[mid]
        end_dt = parse_dt(clob.get("end_date"))

        entry = {
            "id": alert["id"],
            "market_id": mid,
            "direction": direction,
            "star_level": alert.get("star_level"),
            "odds_max": alert.get("odds_max"),
            "odds_min": alert.get("odds_min"),
            "resolved_at": alert.get("resolved_at"),
            "question": q_short,
            "clob_status": clob["status"],
            "clob_end_date": clob.get("end_date"),
        }

        if clob["status"] == "error":
            clob_error.append(entry)
        elif end_dt and end_dt > now:
            # Future end_date → definitely still open
            future_end.append(entry)
        elif clob["status"].startswith("winner_"):
            # CLOB confirms winner
            winner_dir = clob["status"].split("_")[1].upper()
            entry["clob_winner"] = winner_dir
            entry["direction_matches"] = (winner_dir == direction)
            clob_confirmed.append(entry)
        elif clob["status"] == "closed_no_winner":
            clob_no_winner.append(entry)
        elif clob["status"] == "open":
            clob_open.append(entry)
        else:
            clob_error.append(entry)

        if i % 10 == 0:
            logger.info("  %d/%d procesadas...", i, len(candidates))

    # ── 3. Report ────────────────────────────────────────────
    total_false = len(future_end) + len(clob_open) + len(clob_no_winner)
    total_confirmed = len(clob_confirmed)
    total_undetermined = len(clob_error)

    print("\n" + "="*65)
    print("DIAGNÓSTICO — ALERTAS RESUELTAS POR PRECIO (odds=1.0)")
    print("="*65)
    print(f"\nTotal candidatas (outcome='correct', odds_max/min=1.0): {len(candidates)}")
    print(f"\n{'FALSAS (deberían ser pending):':<45} {total_false}")
    print(f"  A) end_date futuro (CLOB open/future):  {len(future_end)}")
    print(f"  B) CLOB confirma open:                  {len(clob_open)}")
    print(f"  C) CLOB closed pero sin winner flag:    {len(clob_no_winner)}")
    print(f"\n{'CONFIRMADAS por CLOB (winner=True):':<45} {total_confirmed}")
    if clob_confirmed:
        wrong_dir = [x for x in clob_confirmed if not x["direction_matches"]]
        print(f"     de las cuales dirección incorrecta:   {len(wrong_dir)}")
    print(f"\n{'INDETERMINADAS (CLOB error/timeout):':<45} {total_undetermined}")

    if future_end:
        print("\n── FALSAS SEGURAS (end_date futuro) ──────────────────────")
        for a in future_end:
            print(f"  #{a['id']:>5}  {a['star_level']}★  {a['direction']:<3}  "
                  f"end:{a['clob_end_date'] or '?':<22}  {a['question']}")

    if clob_open:
        print("\n── FALSAS (CLOB dice open) ────────────────────────────────")
        for a in clob_open:
            print(f"  #{a['id']:>5}  {a['star_level']}★  {a['direction']:<3}  "
                  f"end:{a['clob_end_date'] or '?':<22}  {a['question']}")

    if clob_no_winner:
        print("\n── FALSAS (closed sin winner flag) ────────────────────────")
        for a in clob_no_winner:
            print(f"  #{a['id']:>5}  {a['star_level']}★  {a['direction']:<3}  "
                  f"end:{a['clob_end_date'] or '?':<22}  {a['question']}")

    if clob_error:
        print("\n── INDETERMINADAS (CLOB error) ─────────────────────────────")
        for a in clob_error:
            print(f"  #{a['id']:>5}  {a['direction']:<3}  "
                  f"status:{a['clob_status']}  {a['question']}")

    print("\n" + "="*65)
    print(f"CONCLUSIÓN: {total_false} alertas a resetear a pending, "
          f"{total_confirmed} ya confirmadas, {total_undetermined} a revisar manualmente.")
    print("="*65 + "\n")


if __name__ == "__main__":
    main()
