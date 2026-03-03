"""
Sentinel Alpha — Bayesian Edge Analysis
========================================
Rigorous statistical test of whether the observed win rate significantly exceeds
the market's implied win probability (eff_price = direction-adjusted market odds).

Methodology
-----------
  Frequentist: Z-test, H0: p_hat == p_market (no edge).
  Bayesian:    Beta(1 + wins, 1 + losses) posterior over flat Beta(1,1) prior.
               P(edge > 0) = P(p > p_market | data).
  Monte Carlo: 100k simulations under H0, each drawing from Bernoulli(eff_price_i)
               per alert, to estimate empirical p-value.

Run:
    python -m system_audit.bayesian_edge_analysis

Outputs:
    system_audit/bayesian/YYYY-MM-DD_bayesian.json
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats

# ── Path bootstrap (works as module or direct script) ─────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from src.database.supabase_client import SupabaseClient
from system_audit.run_audit import (
    EFF_PRICE_BANDS,
    apply_dashboard_filters,
    fetch_resolved_alerts,
    get_eff_price,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Directory layout ──────────────────────────────────────────────────────────
BAYESIAN_DIR = Path(__file__).resolve().parent / "bayesian"

# ── Constants ─────────────────────────────────────────────────────────────────
MC_SIMULATIONS = 100_000
MIN_N_MC       = 20   # minimum n for Monte Carlo to be meaningful
MIN_N_SEGMENT  = 10   # minimum n to include a segment in the report


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CORE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_segment(pool: list[dict], run_mc: bool = True) -> dict | None:
    """
    Full Bayesian + frequentist analysis for a pool of resolved alerts.
    Returns None if pool is empty.
    """
    n = len(pool)
    if n == 0:
        return None

    wins   = sum(1 for a in pool if a.get("outcome") == "correct")
    losses = n - wins
    p_hat  = wins / n

    eff_prices = [get_eff_price(a) for a in pool]
    p_market   = float(np.mean(eff_prices))

    # ── Frequentist Z-test ────────────────────────────────────────────────────
    # H0: true WR = p_market (market is correctly calibrated, no edge)
    se = float(np.sqrt(p_market * (1.0 - p_market) / n))
    z  = (p_hat - p_market) / se if se > 0 else 0.0

    p_value_one_sided = float(stats.norm.sf(z))          # P(Z >= z | H0)
    p_value_two_sided = float(2.0 * min(p_value_one_sided, 1.0 - p_value_one_sided))

    # ── Bayesian posterior ────────────────────────────────────────────────────
    # Prior:     Beta(1, 1)  — uniform, uninformative
    # Posterior: Beta(1 + wins, 1 + losses)
    alpha_post = 1 + wins
    beta_post  = 1 + losses

    posterior_mean = alpha_post / (alpha_post + beta_post)
    posterior_mode = (
        (alpha_post - 1) / (alpha_post + beta_post - 2)
        if (alpha_post + beta_post) > 2 else None
    )

    # P(p > p_market | data)  — probability edge is real given observations
    prob_edge_positive = float(stats.beta.sf(p_market, alpha_post, beta_post))

    # 95% credible interval
    ci_lo = float(stats.beta.ppf(0.025, alpha_post, beta_post))
    ci_hi = float(stats.beta.ppf(0.975, alpha_post, beta_post))

    # ── Monte Carlo under H0 ──────────────────────────────────────────────────
    # Each simulation: draw n outcomes from Bernoulli(eff_price_i), compute WR.
    # Monte Carlo p-value = fraction of simulated WRs >= observed p_hat.
    mc_p_value     = None
    mc_simulations = None
    if run_mc and n >= MIN_N_MC:
        ep_arr  = np.array(eff_prices)
        rng     = np.random.default_rng(42)
        draws   = rng.random((MC_SIMULATIONS, n))            # (sims, n)
        sim_wr  = (draws < ep_arr[np.newaxis, :]).mean(axis=1)
        mc_p_value     = float((sim_wr >= p_hat).mean())
        mc_simulations = MC_SIMULATIONS

    return {
        "n":                  n,
        "wins":               wins,
        "losses":             losses,
        "p_hat":              round(p_hat, 4),
        "p_market":           round(p_market, 4),
        "edge_pp":            round((p_hat - p_market) * 100, 2),
        "z_score":            round(float(z), 4),
        "p_value_one_sided":  round(p_value_one_sided, 6),
        "p_value_two_sided":  round(p_value_two_sided, 6),
        "bayesian": {
            "alpha_posterior":      alpha_post,
            "beta_posterior":       beta_post,
            "posterior_mean":       round(float(posterior_mean), 4),
            "posterior_mode":       round(float(posterior_mode), 4) if posterior_mode is not None else None,
            "ci_95_lo":             round(ci_lo, 4),
            "ci_95_hi":             round(ci_hi, 4),
            "prob_edge_positive":   round(prob_edge_positive, 4),
        },
        "monte_carlo": {
            "simulations": mc_simulations,
            "p_value":     round(mc_p_value, 6) if mc_p_value is not None else None,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SNAPSHOT COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

def load_previous_bayesian(bayesian_dir: Path) -> dict | None:
    """Load the most recent *_bayesian.json, if any."""
    files = sorted(bayesian_dir.glob("*_bayesian.json"))
    if not files:
        return None
    with open(files[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def _delta(new_val, old_val) -> float | None:
    if new_val is None or old_val is None:
        return None
    try:
        return round(float(new_val) - float(old_val), 4)
    except (TypeError, ValueError):
        return None


def _seg_delta(c_seg: dict | None, p_seg: dict | None) -> dict | None:
    if not c_seg or not p_seg:
        return None
    return {
        "delta_n":                   _delta(c_seg.get("n"), p_seg.get("n")),
        "delta_p_hat":               _delta(c_seg.get("p_hat"), p_seg.get("p_hat")),
        "delta_p_market":            _delta(c_seg.get("p_market"), p_seg.get("p_market")),
        "delta_edge_pp":             _delta(c_seg.get("edge_pp"), p_seg.get("edge_pp")),
        "delta_z_score":             _delta(c_seg.get("z_score"), p_seg.get("z_score")),
        "delta_prob_edge_positive":  _delta(
            (c_seg.get("bayesian") or {}).get("prob_edge_positive"),
            (p_seg.get("bayesian") or {}).get("prob_edge_positive"),
        ),
    }


def calc_deltas(current: dict, previous: dict) -> dict:
    """Compare current vs previous: delta n, p_hat, P(edge>0) per segment."""
    result: dict = {
        "previous_date": previous.get("fecha"),
        "global":        _seg_delta(current.get("global"), previous.get("global")),
        "by_eff_price":  {},
        "by_stars":      {},
    }
    for band in [b[0] for b in EFF_PRICE_BANDS]:
        d = _seg_delta(
            (current.get("by_eff_price") or {}).get(band),
            (previous.get("by_eff_price") or {}).get(band),
        )
        if d:
            result["by_eff_price"][band] = d
    for star in range(1, 6):
        k = str(star)
        d = _seg_delta(
            (current.get("by_stars") or {}).get(k),
            (previous.get("by_stars") or {}).get(k),
        )
        if d:
            result["by_stars"][k] = d
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CONSOLE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def _pct(v, decimals: int = 1) -> str:
    if v is None:
        return "N/A"
    return f"{float(v) * 100:.{decimals}f}%"


def _f(v, decimals: int = 4) -> str:
    if v is None:
        return "N/A"
    return f"{float(v):.{decimals}f}"


def _sig_stars(p: float | None) -> str:
    """Significance indicator for p-values."""
    if p is None:
        return ""
    if p < 0.01:
        return " ***"
    if p < 0.05:
        return " **"
    if p < 0.10:
        return " *"
    return ""


def _format_delta(delta: dict | None) -> str:
    if not delta:
        return ""
    parts = []
    if delta.get("delta_n") is not None:
        parts.append(f"Δn={int(delta['delta_n']):+d}")
    if delta.get("delta_p_hat") is not None:
        parts.append(f"Δp_hat={delta['delta_p_hat'] * 100:+.1f}pp")
    if delta.get("delta_prob_edge_positive") is not None:
        parts.append(f"ΔP(edge>0)={delta['delta_prob_edge_positive'] * 100:+.1f}pp")
    return f"  [{', '.join(parts)}]" if parts else ""


def print_segment(label: str, seg: dict, delta: dict | None = None) -> None:
    if not seg:
        return
    b  = seg.get("bayesian") or {}
    mc = seg.get("monte_carlo") or {}

    sig    = _sig_stars(seg.get("p_value_one_sided"))
    mc_sig = _sig_stars(mc.get("p_value"))

    print(f"\n  {label}  (n={seg['n']}, wins={seg['wins']}, losses={seg['losses']}){_format_delta(delta)}")
    print(f"    p_hat={_pct(seg['p_hat'])}  p_market={_pct(seg['p_market'])}  edge={seg['edge_pp']:+.2f}pp")
    print(f"    Z={_f(seg['z_score'], 2)}  p(one-sided)={_f(seg['p_value_one_sided'], 4)}{sig}")
    print(f"    Posterior mean={_pct(b.get('posterior_mean'))}  "
          f"95%CI=[{_pct(b.get('ci_95_lo'))}, {_pct(b.get('ci_95_hi'))}]")
    print(f"    P(edge>0)={_pct(b.get('prob_edge_positive'), 2)}")
    if mc.get("p_value") is not None:
        print(f"    Monte Carlo p={_f(mc['p_value'], 4)}{mc_sig}  ({mc['simulations']:,} sims)")


def print_summary(data: dict) -> None:
    SEP = "═" * 66
    print(f"\n{SEP}")
    print(f"  SENTINEL ALPHA — ANÁLISIS BAYESIANO DE EDGE  {data.get('fecha', '')}")
    print(SEP)
    print(f"  Pool:  {data.get('total_in_pool', 0)} mercados únicos (filtros dashboard aplicados)")
    print(f"  MC:    {data.get('mc_simulations', 0):,} simulaciones bajo H0")
    print(f"  Sig:   * p<0.10  ** p<0.05  *** p<0.01\n")

    vs = data.get("vs_previous")

    # ── Global ────────────────────────────────────────────────────────────────
    print_segment("GLOBAL", data.get("global"), (vs or {}).get("global"))

    # ── By eff_price ─────────────────────────────────────────────────────────
    print(f"\n  {'─' * 62}")
    print(f"  POR EFF_PRICE BAND")
    ep_deltas = (vs or {}).get("by_eff_price") or {}
    for band in [b[0] for b in EFF_PRICE_BANDS]:
        seg = (data.get("by_eff_price") or {}).get(band)
        if seg and seg.get("n", 0) >= MIN_N_SEGMENT:
            print_segment(f"eff_price {band}", seg, ep_deltas.get(band))

    # ── By star level ─────────────────────────────────────────────────────────
    print(f"\n  {'─' * 62}")
    print(f"  POR NIVEL DE ESTRELLAS")
    star_deltas = (vs or {}).get("by_stars") or {}
    for star in range(1, 6):
        k   = str(star)
        seg = (data.get("by_stars") or {}).get(k)
        if seg and seg.get("n", 0) >= MIN_N_SEGMENT:
            print_segment(f"{star}★", seg, star_deltas.get(k))

    # ── vs previous ──────────────────────────────────────────────────────────
    if vs:
        print(f"\n  {'─' * 62}")
        print(f"  VS SNAPSHOT ANTERIOR  ({vs.get('previous_date', 'N/A')})")
        gd = vs.get("global") or {}
        if gd.get("delta_n") is not None:
            print(f"    Δ mercados:         {int(gd['delta_n']):+d}")
        if gd.get("delta_p_hat") is not None:
            print(f"    Δ p_hat:            {gd['delta_p_hat'] * 100:+.2f}pp")
        if gd.get("delta_edge_pp") is not None:
            print(f"    Δ edge (pp):        {gd['delta_edge_pp']:+.2f}pp")
        if gd.get("delta_prob_edge_positive") is not None:
            print(f"    Δ P(edge>0):        {gd['delta_prob_edge_positive'] * 100:+.1f}pp")

    print(f"\n{SEP}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("Starting Bayesian edge analysis — %s", today)

    BAYESIAN_DIR.mkdir(parents=True, exist_ok=True)

    db = SupabaseClient()
    logger.info("Fetching resolved alerts from Supabase...")
    raw  = fetch_resolved_alerts(db)
    logger.info("  Raw resolved: %d", len(raw))

    pool = apply_dashboard_filters(raw)
    logger.info("  Pool after dashboard filters: %d", len(pool))

    if not pool:
        logger.error("Empty pool after filters — nothing to analyze.")
        return

    # ── Run analyses ──────────────────────────────────────────────────────────
    logger.info("Running global analysis (%d Monte Carlo sims)...", MC_SIMULATIONS)
    global_result = analyze_segment(pool, run_mc=True)

    logger.info("Running by eff_price band...")
    by_eff_price: dict = {}
    for label, lo, hi in EFF_PRICE_BANDS:
        seg = [a for a in pool if lo <= get_eff_price(a) < hi]
        by_eff_price[label] = analyze_segment(seg, run_mc=True)

    logger.info("Running by star level...")
    by_stars: dict = {}
    for star in range(1, 6):
        seg = [a for a in pool if (a.get("star_level") or 0) == star]
        by_stars[str(star)] = analyze_segment(seg, run_mc=True)

    # ── Compare with previous snapshot ───────────────────────────────────────
    previous    = load_previous_bayesian(BAYESIAN_DIR)
    vs_previous = None
    if previous:
        vs_previous = calc_deltas(
            {
                "fecha":        today,
                "global":       global_result,
                "by_eff_price": by_eff_price,
                "by_stars":     by_stars,
            },
            previous,
        )
        logger.info("  Compared with snapshot: %s", previous.get("fecha"))
    else:
        logger.info("  No previous Bayesian snapshot — first run.")

    # ── Assemble snapshot ─────────────────────────────────────────────────────
    snapshot = {
        "fecha":          today,
        "total_in_pool":  len(pool),
        "mc_simulations": MC_SIMULATIONS,
        "global":         global_result,
        "by_eff_price":   by_eff_price,
        "by_stars":       by_stars,
        "vs_previous":    vs_previous,
    }

    # ── Save ──────────────────────────────────────────────────────────────────
    out_path = BAYESIAN_DIR / f"{today}_bayesian.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    logger.info("  Saved: %s", out_path.name)

    # ── Print ─────────────────────────────────────────────────────────────────
    print_summary(snapshot)


if __name__ == "__main__":
    main()
