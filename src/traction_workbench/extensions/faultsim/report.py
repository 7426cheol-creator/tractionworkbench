"""The safety-case evidence of the fault simulation as one self-contained HTML page for a reviewer or assessor:
what was evaluated (project, code, design variant), the requirement chain SG -> FSR -> TSR with its attributes,
the static review findings and latency bounds, the declared reaction strategies, the verification matrix with
coverage, worst timings and the failure-mode table, the declared common causes, and the limits of the evidence.

Inverter-level engineering evidence from a simulation: it supports a safety case, it is not one - vehicle-level
controllability, the item's other elements, hardware metrics and the process work products are outside it.
"""

from __future__ import annotations

import datetime as _dt
import html
import json

from .labels import quantity_label, reaction_label
from .safety import requirements_from_dict
from .strategy import strategies_from

_COL = {"PASS": "#1a7f37", "FAIL": "#cf222e", "UNKNOWN": "#b7791f", "NOT_APPLICABLE": "#8c959f",
        "OK": "#1a7f37", "INCONSISTENT": "#cf222e", "MISSING": "#cf222e", "WARNING": "#b7791f", "NOTE": "#57606a"}
_KO = {"PASS": "통과", "FAIL": "실패", "UNKNOWN": "판단 불가", "NOT_APPLICABLE": "해당 없음", "OK": "OK",
       "INCONSISTENT": "모순", "MISSING": "누락", "WARNING": "경고", "NOTE": "참고"}


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _tag(v: str) -> str:
    return (f'<span class="tag" style="color:{_COL.get(v, "#57606a")};border-color:{_COL.get(v, "#57606a")}">'
            f'{_e(_KO.get(v, v))}</span>')


def _ms(s) -> str:
    return "—" if s is None else f"{float(s) * 1e3:.4g} ms"


def _table(head, rows, cls="") -> str:
    h = "".join(f"<th>{_e(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><tr>{h}</tr>{b}</table></div>'


def criterion_text(c: dict) -> str:
    """A TSR criterion as people read it (times in ms)."""
    t = c.get("type")
    ms = lambda k: None if c.get(k) is None else float(c[k]) * (1e3 if k.endswith("_s") else 1.0)   # noqa: E731
    if t == "torque_window":
        side = {"accel": "acceleration side", "decel": "deceleration side", "both": "both sides"}.get(
            c.get("side", "both"), c.get("side"))
        return (f"shaft torque inside the dynamic window ({side}): ±max({c.get('abs_Nm')} N·m, "
                f"{float(c.get('rel') or 0) * 100:g} % |T|) around the request, delay {ms('delay_s') or 0:g} ms, "
                f"response τ {ms('response_tau_s') or 0:g} ms; excursion tolerated {ms('tolerance_s') or 0:g} ms"
                + ("; reduction allowed" if c.get("reduction_allowed") else ""))
    if t == "bound":
        q = quantity_label(c.get("quantity"))
        lim = []
        if c.get("min") is not None:
            lim.append(f">= {c['min']:g}")
        if c.get("max") is not None:
            lim.append(f"<= {c['max']:g}")
        return (f"{q} {' and '.join(lim)} from {c.get('origin', 't0')}" +
                (f", tolerated {ms('tolerance_s'):g} ms" if c.get("tolerance_s") else ""))
    if t == "safe_state":
        conds = "; ".join(
            f"{quantity_label(x['quantity'])} " + (f"in {x.get('values')}" if x["quantity"] == "bridge_in" else
                                                   " ".join(f"{k} {x[k]:g}" for k in ("min", "max")
                                                            if x.get(k) is not None))
            for x in c.get("conditions") or [])
        return (f"from the {c.get('origin', 'detection')}: {conds} reached within {ms('within_s') or 0:g} ms and "
                f"held {ms('hold_s') or 0:g} ms")
    if t == "no_false_reaction":
        return "no detection and no reaction without a fault"
    if t == "timing":
        return "FDTI / FRTI within the FSR budgets, FHTI within the FTTI"
    return json.dumps(c, ensure_ascii=False, default=str)


def _step_text(s) -> str:
    ex = {"none": "holds", "time": f"for {s.value:g} ms" if s.value is not None else "",
          "done": "until done"}.get(s.exit) or f"until {s.exit.replace('_', ' ')} {s.value:g}"
    if s.max_s is not None and s.exit not in ("none", "time"):
        ex += f" (max {s.max_s * 1e3:g} ms)"
    p = ", ".join(f"{k}={v}" for k, v in s.params.items())
    return f"{reaction_label(s.action)} {ex}" + (f" [{p}]" if p else "")


CSS = """
body{font-family:"Noto Sans KR","Malgun Gothic",system-ui,sans-serif;margin:0;background:#fff;color:#1f2328}
main{max-width:1180px;margin:0 auto;padding:24px 20px 60px}
h1{font-size:24px;margin:0 0 6px}h2{font-size:19px;margin:34px 0 8px;border-top:2px solid #d0d7de;padding-top:10px}
h3{font-size:15.5px;margin:18px 0 6px}.meta{color:#57606a;font-size:13px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0 12px}
th,td{border:1px solid #d0d7de;padding:4px 7px;vertical-align:top;text-align:left}th{background:#f6f8fa}
.tw{overflow-x:auto}.tag{border:1px solid;border-radius:10px;padding:0 7px;font-size:12px;white-space:nowrap}
.box{border-left:4px solid #0969da;background:#f6f8fa;padding:8px 12px;margin:10px 0;font-size:13.5px}
.warn{border-left-color:#bf8700}.mx td{text-align:center}.mx td:first-child{text-align:left}
code{font-size:12px}
@media (max-width:640px){.doc td,.doc th{min-width:4.6em}}
@media (prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}th{background:#161b22}
th,td{border-color:#30363d}.box{background:#161b22}}
"""


def safety_case_html(data: dict, *, project: dict, code: dict, changes: list | None = None,
                     review: dict | None = None, matrix: dict | None = None, independence: dict | None = None,
                     precision_notes: list | None = None, counterexamples: list | None = None) -> str:
    """The report (see the module note).  ``data`` is the fault_sim section evaluated (the variant applied)."""
    reqs = requirements_from_dict(data.get("requirements") or {})
    strategies = strategies_from(data)
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    P = []
    P.append(f"<h1>안전 근거 보고서 — 인과 고장 시뮬레이션 (인버터 수준)</h1>"
             f'<div class="meta">생성 {_e(now)} · 프로젝트 {_e(project.get("label", ""))} · 프로젝트 digest '
             f'{_e(str(project.get("digest", ""))[:16])} · 소프트웨어 {_e(code.get("version"))} '
             f'({_e(code.get("commit") or "커밋 정보 없음")})</div>')
    P.append('<div class="box warn">범위: 선언된 보호 아키텍처와 안전 요구를 인과 시뮬레이션으로 판정한 <b>인버터 수준의 엔지니어링 '
             '근거</b>입니다. 차량 수준 제어 가능성, 아이템의 다른 요소, 하드웨어 지표(SPFM/LFM/PMHF), 개발 프로세스 산출물은 '
             '범위 밖이며, ISO 26262 적합성 판정이 아닙니다. 판정은 시뮬레이션한 궤적에 대해서만 성립합니다(PASS는 해당 궤적의 '
             '진술, FAIL은 재현 가능한 반례).</div>')
    # -- design under evaluation
    P.append("<h2>1. 평가한 설계</h2>")
    P.append(f"<p>보호 아키텍처 근거: {_e(data.get('basis', ''))}</p>")
    if changes:
        P.append("<p>프로젝트 데이터 대비 <b>설계 변형</b> (이 보고서의 판정은 변형을 적용한 설계에 대한 것):</p>")
        P.append(_table(["경로", "프로젝트 값", "평가 값"],
                        [(f"<code>{_e(c['path'])}</code>",
                          _e(json.dumps(c["project"], ensure_ascii=False, default=str)),
                          _e(json.dumps(c["study"], ensure_ascii=False, default=str))) for c in changes]))
    else:
        P.append("<p>설계 변형 없음: 프로젝트의 fault_sim 데이터 그대로.</p>")
    # -- requirements
    P.append("<h2>2. 안전 요구 (SG → FSR → TSR)</h2>")
    P.append(f"<p class='meta'>{_e(reqs.basis)}</p>")
    P.append("<h3>안전 목표</h3>")
    P.append(_table(["ID", "안전 목표", "ASIL", "FTTI", "위험", "안전 상태", "운전 상황"],
                    [(_e(g.sg_id), _e(g.text), _e(g.asil or "—"), _ms(g.ftti_s), _e(g.hazard), _e(g.safe_state or "—"),
                      _e(g.situation or "—")) for g in reqs.goals]))
    P.append("<h3>기능 안전 요구</h3>")
    P.append(_table(["ID", "SG", "ASIL", "요구", "FDTI 예산", "FRTI 예산", "안전 상태 TSR", "할당 메커니즘",
                     "경고·성능 저하", "검증 방법"],
                    [(_e(f.fsr_id), _e(", ".join(f.sg)), _e(f.asil or "—"), _e(f.text), _ms(f.fdti_budget_s),
                      _ms(f.frti_budget_s), _e(f.safe_state or "—"), _e(", ".join(f.mechanisms)),
                      _e(f.warning or "—"), _e(", ".join(f.verification) or "—")) for f in reqs.fsrs]))
    P.append("<h3>기술 안전 요구</h3>")
    P.append(_table(["ID", "FSR", "ASIL", "수준", "요구", "판정 기준", "할당", "검증 방법", "값의 근거"],
                    [(_e(t.tsr_id), _e(t.fsr), _e(t.asil or "—"), _e(t.level), _e(t.text),
                      _e(criterion_text(t.criterion)), _e(t.allocation or "—"), _e(", ".join(t.verification) or "—"),
                      _e(t.rationale or "—")) for t in reqs.tsrs]))
    # -- static review
    if review:
        c = review["counts"]
        P.append("<h2>3. 정적 설계 검토</h2>")
        P.append(f"<p>모순 {c.get('INCONSISTENT', 0)} · 누락 {c.get('MISSING', 0)} · 경고 {c.get('WARNING', 0)} · "
                 f"참고 {c.get('NOTE', 0)} · OK {c.get('OK', 0)}. {_e(review.get('statement', ''))}</p>")
        P.append(_table(["상태", "요소", "점검", "내용"],
                        [(_tag(f["status"]), _e(f["element"]), _e(f["check"]), _e(f["detail"]))
                         for f in review["findings"] if f["status"] != "OK"]))
        P.append("<h3>검출 지연 범위 (큰 계단 고장 기준, 선언된 매개변수)</h3>")
        P.append("<p class='meta'>최소 = 디바운스 + 센서 지연(+ 시간 제한), 최대 = 여기에 태스크 주기 하나(+ 창 지연). 최소가 FDTI "
                 "예산을 넘으면 모순, 최대만 넘으면 고장 시점에 따른 경고입니다.</p>")
        span = lambda r: (_ms(r["detection_s"]) if r.get("detection_min_s") is None or r["detection_s"] is None  # noqa: E731
                          or abs(r["detection_s"] - r["detection_min_s"]) < 1e-12
                          else f"{_ms(r['detection_min_s'])} … {_ms(r['detection_s'])}")
        P.append(_table(["FSR", "메커니즘", "종류", "경로", "검출", "구성", "경로 지연", "FDTI 예산"],
                        [(_e(r["fsr"]), _e(r["mechanism"]), _e(r["kind"]), _e(r["path"]), span(r),
                          _e(r["detection_basis"]), _ms(r["path_delay_s"]), _ms(r["fdti_budget_s"]))
                         for r in review.get("latency", [])]))
    # -- reactions
    P.append("<h2>4. 반응 전략 (선언)</h2>")
    pol = data.get("policy") or {}
    P.append(_table(["규칙", "조건", "반응"],
                    [(str(i + 1), _e(json.dumps(r.get("if", "else"), ensure_ascii=False)),
                      _e(reaction_label(r.get("then") or r.get("else")))) for i, r in enumerate(pol.get("rules") or [])]))
    if strategies:
        P.append(_table(["전략", "단계", "대체 상태", "설명"],
                        [(_e(sid), "<br>".join(f"{i + 1}. {_e(_step_text(s))}" for i, s in enumerate(st.steps)),
                          _e(reaction_label(st.fallback_state)), _e(st.text)) for sid, st in strategies.items()]))
    # -- verification
    if matrix and matrix.get("rows"):
        rows = matrix["rows"]
        P.append("<h2>5. 검증 매트릭스 (시나리오 × 요구)</h2>")
        P.append(f"<p>{_e(matrix.get('statement', ''))}</p>")
        head = ["요구"] + [r["key"] for r in rows] + ["실행됨", "실패"]
        body = []
        for q in matrix["requirements"]:
            cov = matrix["coverage"][q]
            body.append([_e(q)] + [_tag(matrix["cells"][q][r["key"]]) for r in rows]
                        + [f"{cov['exercised']}/{len(rows)}", str(cov["fail"])])
        P.append(_table(head, body, "mx"))
        if matrix.get("skipped"):
            P.append("<p class='meta'>중복이라 뺀 시나리오: " + _e("; ".join(
                f"{s['key']} (= {s['same_as']})" for s in matrix["skipped"])) + "</p>")
        P.append("<h3>FSR별 최악 시간 (각각 한 궤적)</h3>")
        judged = {f.fsr_id: [t.tsr_id for t in reqs.tsrs if t.fsr == f.fsr_id and t.criterion["type"] == "timing"]
                  for f in reqs.fsrs}
        P.append(_table(["FSR", "최대 FDTI", "최대 FRTI", "최대 FHTI", "시간 판정"],
                        [(_e(f), *(f"{_ms(d[k]['value_s'])} ({_e(d[k]['scenario'])})" if k in d else "—"
                                   for k in ("FDTI", "FRTI", "FHTI")),
                          _e(", ".join(judged.get(f) or []) or "없음 — 측정값만 보고(예산은 정적 검토만)"))
                         for f, d in matrix["timing"].items()]))
        P.append("<h3>고장 모드 표 (시뮬레이션 기반 FMEA)</h3>")
        P.append(_table(["시나리오", "고장", "검출", "반응", "최종 브리지", "최대 |i| [A]", "최저 i_d [A]",
                         "최대 제동 토크 [N·m]", "최대 V_dc [V]", "실패 요구"],
                        [(_e(r["key"]), _e(r["fmea"]["fault"]),
                          _e(f"{r['fmea']['detected_by']} @ {r['fmea']['t_detect_ms']:.4g} ms"
                             if r["fmea"]["detected_by"] else "미검출"),
                          _e(reaction_label(r["fmea"]["reaction"]) if r["fmea"]["reaction"] else "—"),
                          _e(reaction_label(r["fmea"]["final_bridge"])), f"{r['fmea']['i_phase_peak_A']:.0f}",
                          f"{r['fmea']['i_d_min_A']:.0f}", f"{r['fmea']['T_brake_max_Nm']:.0f}",
                          f"{r['fmea']['v_dc_max_V']:.0f}",
                          _e(", ".join(r["fmea"]["failing"]) or "—")) for r in rows]))
    # -- common cause
    if independence:
        P.append("<h2>6. 공통 원인 (선언된 의존성)</h2>")
        P.append(_table(["FSR", "할당 메커니즘", "단일 원인 자원", "판단"],
                        [(_e(f["fsr"]), _e(", ".join(f["mechanisms"])), _e(", ".join(f["single_points"]) or "—"),
                          _e(f["statement"])) for f in independence.get("fsr", [])]))
        P.append(f"<p class='meta'>{_e(independence.get('note', ''))}</p>")
    # -- counterexamples
    if counterexamples:
        P.append("<h2>7. 반례 (재실행 가능)</h2>")
        P.append(_table(["ID", "실패 요구", "만든 시점"],
                        [(_e(c["id"]), _e(", ".join(c.get("failing") or [])), _e(c.get("created", "")))
                         for c in counterexamples]))
    # -- limits
    P.append("<h2>8. 가정·한계·미결 사항</h2><ul>")
    for n in precision_notes or []:
        P.append(f"<li>{_e(n)}</li>")
    P.append("<li>요구 값·아키텍처 값은 프로젝트가 선언한 데이터이며(예제는 합성), 이 보고서는 그 값의 타당성을 판정하지 않습니다 "
             "(값의 근거 열 참조).</li>")
    P.append("<li>검출 지연 상한은 큰 계단 고장 기준입니다. 경계 근처 고장의 늦은 검출은 캠페인(경계 이분 탐색)이 보여 줍니다.</li>")
    if matrix and matrix.get("not_exercised"):
        P.append(f"<li>검증 매트릭스에서 한 번도 실행되지 않은 요구: {_e(', '.join(matrix['not_exercised']))} — "
                 f"이 요구를 발동하는 시나리오가 필요합니다.</li>")
    if review:
        bad = [f for f in review["findings"] if f["status"] in ("INCONSISTENT", "MISSING")]
        for f in bad:
            P.append(f"<li>미결: {_e(f['element'])} — {_e(f['check'])}: {_e(f['detail'])}</li>")
    P.append("</ul>")
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>안전 근거 보고서</title><style>" + CSS +
            "</style></head><body><main class='doc'>" + "\n".join(P) + "</main></body></html>")
