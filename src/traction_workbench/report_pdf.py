"""PDF engineering report (matplotlib only, no GUI): decision summary, operating-point graphs, envelope, analyses,
and the full Markdown decision record as an appendix.

    twb report CASE.json --pdf report.pdf
"""

from __future__ import annotations

import datetime as _dt
import textwrap
from pathlib import Path

import matplotlib
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure

from . import __version__
from .i18n import tr
from .plots import figures as F
from .plots import style as S
from .viz import design as DS
from .viz import maps as M
from .viz import operating as O
from .viz import sweeps as SW

A4_P = (8.27, 11.69)
A4_L = (11.69, 8.27)


_SYMBOLS = {"✅": "[OK]", "⛔": "[X]", "❔": "[?]", "⚠️": "[!]", "⚠": "[!]", "\ufe0f": ""}


def _plain(text: str) -> str:
    """Replace emoji badges (no glyph in the PDF fonts) with ASCII markers."""
    for k, v in _SYMBOLS.items():
        text = text.replace(k, v)
    return "".join(ch if ord(ch) < 0x10000 else "?" for ch in text)


def _wrap(text: str, width: int) -> list[str]:
    out = []
    for para in _plain(str(text)).splitlines() or [""]:
        out.extend(textwrap.wrap(para, width=width, replace_whitespace=False, drop_whitespace=False) or [""])
    return out


class _Page:
    """Simple top-down text layout on a portrait A4 figure."""

    def __init__(self, pdf, title: str | None = None):
        self.pdf = pdf
        self.fig = Figure(figsize=A4_P)
        self.y = 0.95
        if title:
            self.text(title, size=14, weight="bold", gap=0.012)

    def text(self, s: str, size: float = 8.5, weight: str = "normal", color: str | None = None, width: int = 110,
             gap: float = 0.004, family=None, x: float = 0.07):
        lh = size / 72.0 / A4_P[1] * 1.45
        for line in _wrap(s, width):
            if self.y < 0.05:
                self.flush()
                self.fig = Figure(figsize=A4_P)
                self.y = 0.95
            kw = {"fontfamily": family} if family else {}
            self.fig.text(x, self.y, line, fontsize=size, fontweight=weight, color=color or S.theme()["fg"], va="top", **kw)
            self.y -= lh
        self.y -= gap

    def box(self, s: str, fg: str, bg: str, size: float = 16):
        self.fig.text(0.07, self.y, f"  {s}  ", fontsize=size, fontweight="bold", color=fg, va="top",
                      bbox=dict(boxstyle="round,pad=0.4", fc=bg, ec=fg))
        self.y -= size / 72.0 / A4_P[1] * 2.2

    def flush(self):
        self.pdf.savefig(self.fig)


def _fig(pdf, fn, *args, size=A4_L, **kwargs):
    fig = Figure(figsize=size)
    fn(fig, *args, **kwargs)
    pdf.savefig(fig)


def build_pdf(path, record: dict, rec=None, case=None, progress=None, envelope: bool = True) -> Path:
    """Write the report; ``rec``/``case`` (objects from ``service.evaluate_case_full``) enable the graphs."""
    prog = progress or (lambda f, m="": None)
    matplotlib.rcParams.update({})
    S.apply("light")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    v = record["verdict"]
    colors = {"PASS": ("#1a7f37", "#dafbe1"), "FAIL": ("#cf222e", "#ffebe9"), "UNKNOWN": ("#9a6700", "#fff8c5")}
    meta = {"Title": f"Engineering Decision Record {record['record_id']}", "Author": "Traction Workbench",
            "Subject": record["requirement"].get("original_text", ""), "Creator": f"traction-workbench {__version__}"}
    with PdfPages(path, metadata=meta) as pdf:
        prog(0.05, "summary")
        pg = _Page(pdf, tr("엔지니어링 의사결정 기록", "Engineering Decision Record"))
        req = record["requirement"]
        pg.text(f"{req.get('req_id', '')} — {req.get('original_text', '')}", size=10, weight="bold", width=90)
        fg, bg = colors.get(v["verdict"], ("#57606a", "#f6f8fa"))
        pg.box(v["verdict"], fg, bg)
        pg.text(f"{tr('사유', 'reasons')}: {', '.join(v['reasons']) or '—'} · {tr('결정 claim', 'deciding claims')}: "
                f"{', '.join(v.get('deciding_claims', [])) or '—'}", size=9)
        for q in v.get("qualifiers", []):
            pg.text(f"· {q}", size=8.5)
        pg.text(f"{tr('범위', 'scope')}: {v.get('scope', '')}", size=8, color="#57606a")
        pg.text(tr("판정 항목 (조건별)", "claims per condition"), size=11, weight="bold", gap=0.006)
        for i, c in enumerate(record["conditions"]):
            sc = c["scenario"]
            pg.text(f"#{i + 1} n = {sc['speed_rpm_mechanical']:g} rpm · Vdc = {sc['Vdc_V_inverter_dc_terminal']:g} V · "
                    f"{tr('토크 여유', 'torque margin')} {c['torque_capability_margin_Nm']}", size=9, weight="bold")
            claims = [c["requirement_claim_at_this_condition"]] + c["policy_solution"]["claims"]
            if c.get("duration_claim"):
                claims.append(c["duration_claim"])
            for cl in claims:
                pg.text(f"  {cl['name']:<28} {cl['status']:<11} {', '.join(cl.get('reasons') or [])}", size=8,
                        family=["DejaVu Sans Mono"] + list(matplotlib.rcParams["font.family"])[1:],
                        color=colors.get({"FEASIBLE": "PASS", "INFEASIBLE": "FAIL"}.get(cl["status"], "UNKNOWN"))[0])
                if cl.get("detail"):
                    pg.text(f"      {cl['detail']}", size=7.5, color="#57606a", width=120)
            if i >= 5 and len(record["conditions"]) > 7:
                pg.text(f"… {len(record['conditions']) - i - 1} more conditions (see appendix)", size=8)
                break
        for title, items in ((tr("제한 요인", "limiting factors"), record["limiting_factors"]),
                             (tr("다음 조치", "next actions"), record["next_actions"]),
                             (tr("평가하지 않은 항목", "not evaluated"), record["not_evaluated"])):
            if items:
                pg.text(title, size=11, weight="bold", gap=0.004)
                for it in items:
                    pg.text(f"· {it}", size=8.5, width=115)
        m = record["model"]
        pg.text(tr("모델·재현 정보", "model & reproducibility"), size=11, weight="bold")
        pg.text(f"{m['drive_id']} rev {m['revision']} · fidelity {m['fidelity']} · {m['provenance'].get('origin')} · "
                f"{m['provenance'].get('validation_status', '')}", size=8, width=120)
        pg.text(f"record {record['record_id']} · input SHA-256 {record['input_sha256']} · software "
                f"{record['software']['version']} · {_dt.datetime.now().isoformat(timespec='seconds')}", size=7.5,
                color="#57606a", width=130)
        pg.flush()

        if rec is not None and case is not None:
            idx = next((i for i, c in enumerate(rec.conditions) if c.primary.point is not None), 0)
            cond = rec.conditions[idx]
            sc = cond.scenario
            T = cond.primary_torque_Nm            # the accepted witness torque (review R2 D-R2-03)
            title = f"{req.get('req_id', '')} · {sc.speed_rpm:g} rpm · {T:g} N·m · {sc.Vdc_V:g} V"
            prog(0.2, "id-iq map")
            plane = M.idiq_plane(case.drive, sc.source_limits, sc.speed_rpm, sc.Vdc_V, T, scenario=sc)
            _fig(pdf, F.fig_idiq, plane, title=title)
            if cond.primary.point is not None:
                pt = cond.primary.point
                pv = O.point_view(case.drive, sc, pt.id_A, pt.iq_A, T)
                prog(0.35, "waveforms")
                from .plots import schematics as SC
                _fig(pdf, SC.fig_system_overview, O.overview_info(pv), title=title, size=(11.69, 5.6))
                _fig(pdf, F.fig_waveforms, O.waveforms(pv), title=title, size=A4_P)
                _fig(pdf, F.fig_phasor, O.phasor(pv), O.hexagon(pv), title=title)
                _fig(pdf, F.fig_power_constraints, O.power_chain(pt), O.constraint_rows(pt), title=title)
            if envelope:
                prog(0.5, "envelope")
                env = SW.envelope(case.drive, sc.source_limits, sc.Vdc_V, n=25)
                reqs = [{"id": req.get("req_id", ""), "speed_rpm": sc.speed_rpm, "torque_Nm": T, "verdict": v["verdict"]}]
                _fig(pdf, F.fig_envelope, env, reqs)
            an = record.get("analyses") or {}
            sizing = {s["parameter"]["parameter"]: s for s in an.get("sizing", [])}
            for k, (name, s) in enumerate(sizing.items()):
                prog(0.6 + 0.2 * k / max(1, len(sizing)), f"sizing {name}")
                lo, hi = s["search_range"]
                import numpy as np
                cv = DS.capability_vs_parameter(case.drive, rec.conditions[0].scenario, name, np.linspace(lo, hi, 21),
                                                T_request=T, direction=1 if T >= 0 else -1)
                _fig(pdf, F.fig_capability_vs_parameter, cv, s)
            if an.get("dominance") or an.get("relaxation"):
                _fig(pdf, F.fig_dominance, an.get("dominance") or {"single": [], "base_policy_capability_Nm": None},
                     an.get("relaxation"))
        prog(0.9, "appendix")
        pg = _Page(pdf, tr("부록: 의사결정 기록 (Markdown 원문)", "Appendix: decision record (Markdown)"))
        mono = ["DejaVu Sans Mono"] + list(matplotlib.rcParams["font.family"])[1:]
        for line in record.get("markdown", "").splitlines():
            pg.text(line, size=6.8, family=mono, width=135, gap=0.0)
        pg.flush()
    prog(1.0, "done")
    return path
