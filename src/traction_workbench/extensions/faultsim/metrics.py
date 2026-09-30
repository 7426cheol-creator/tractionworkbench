"""Hardware architectural metrics per metric set (single-point / latent fault metrics, PMHF) from a declared
failure-mode table - the ISO 26262-5 quantities, evaluated separately for each set of top-level requirements.

Failure-mode row (FIT = failures per 1e9 h):

    {"element", "mode", "fit", "safety_related": bool, "violates": [requirement ids it can violate on its own],
     "dc_spf": diagnostic coverage of that violation by a safety mechanism (0..1), "mpf": bool (it can violate a
     requirement only together with another fault), "dc_latent": coverage of its latent part (0..1),
     "mpf_of": requirement ids an MPF contributes to (default: violates),
     "partner_fit": the failure rate of the fault it would combine with (for the dual-point PMHF term)}

A metric set is the list of requirement ids it covers (e.g. the torque set and the destabilisation set; a requirement
may belong to both).  For each set S separately:

    lambda_S      = sum of the safety-related modes relevant to S (directly violating a requirement of S, or MPF)
    lambda_SPF,RF = sum over the direct modes of fit (1 - dc_spf)
    lambda_MPF,L  = sum over the direct modes of fit dc_spf (1 - dc_latent) + over the MPF modes of fit (1 - dc_latent)
    SPFM = 1 - lambda_SPF,RF / lambda_S;   LFM = 1 - lambda_MPF,L / (lambda_S - lambda_SPF,RF)
    PMHF = lambda_SPF,RF + sum over latent contributions of fit_latent * partner_fit * T_life / 2  (1/h, shown in FIT)

Each set is judged against its own targets; the sets are never added (a shared mode is counted in each set it
belongs to, the two PMHF values are two separate claims) and a set's metric is never an average of element ratios.
Undeclared targets (e.g. OPEN customer parameters) give UNKNOWN with the computed values; a dual-point term without a
partner rate is reported as not evaluated.
"""

from __future__ import annotations

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


def metric_set(rows: list, reqs: list, lifetime_h: float) -> dict:
    S = set(reqs)
    lam_S = spf = mpf_l = 0.0
    dual = 0.0
    dual_missing = []
    used = []
    for r in rows:
        if not r.get("safety_related", True):
            continue
        reach = set(r.get("mpf_of") or r.get("violates") or ())
        direct = bool(S & set(r.get("violates") or ())) and not r.get("mpf")
        if not (S & reach):
            continue
        fit = float(r["fit"])
        lam_S += fit
        used.append(f"{r['element']}/{r['mode']}")
        if direct:
            dc = float(r.get("dc_spf", 0.0))
            spf += fit * (1.0 - dc)
            lat = fit * dc * (1.0 - float(r.get("dc_latent", 0.0)))
        else:
            lat = fit * (1.0 - float(r.get("dc_latent", 0.0)))
        mpf_l += lat
        if lat > 0:
            if r.get("partner_fit") is None:
                dual_missing.append(f"{r['element']}/{r['mode']}")
            else:
                dual += lat * 1e-9 * float(r["partner_fit"]) * 1e-9 * lifetime_h / 2.0
    spfm = 1.0 - spf / lam_S if lam_S > 0 else None
    lfm = 1.0 - mpf_l / (lam_S - spf) if lam_S - spf > 0 else None
    pmhf_fit = spf + dual * 1e9
    return {"lambda_S_fit": lam_S, "lambda_spf_rf_fit": spf, "lambda_mpf_latent_fit": mpf_l, "SPFM": spfm, "LFM": lfm,
            "PMHF_fit": pmhf_fit, "dual_point_fit": dual * 1e9, "dual_point_missing": dual_missing, "modes": used}


def judge_metrics(m: dict, targets: dict) -> dict:
    """``targets``: {"SPFM_min", "LFM_min", "PMHF_max_fit"} with None for an OPEN target."""
    out, reasons = {}, []
    for key, tk, better in (("SPFM", "SPFM_min", "ge"), ("LFM", "LFM_min", "ge"), ("PMHF_fit", "PMHF_max_fit", "le")):
        v, tgt = m.get(key), targets.get(tk)
        if v is None:
            out[key] = UNKNOWN
            reasons.append(f"{key}: not computable (no relevant failure rate)")
        elif tgt is None:
            out[key] = UNKNOWN
            reasons.append(f"{key} = {v:.4g}: target {tk} OPEN")
        else:
            ok = v >= tgt - 1e-12 if better == "ge" else v <= tgt + 1e-12
            if key == "PMHF_fit" and m.get("dual_point_missing") and ok:
                out[key] = UNKNOWN
                reasons.append(f"PMHF {v:.4g} FIT <= {tgt:g} without the dual-point term of "
                               f"{', '.join(m['dual_point_missing'][:3])} (no partner rate)")
                continue
            out[key] = PASS if ok else FAIL
            reasons.append(f"{key} = {v:.4g} {'meets' if ok else 'misses'} {tgt:g}")
    order = {FAIL: 2, UNKNOWN: 1, PASS: 0}
    worst = max(out.values(), key=lambda x: order[x]) if out else UNKNOWN
    return {"verdict": worst, "per_metric": out, "reasons": reasons}
