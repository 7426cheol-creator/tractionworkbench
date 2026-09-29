"""The engineering reading of results (``insight``): it reads the record back, it never decides.

* every number it states is the record's (checked against the record for the built-in examples);
* the verdict word and the headline follow the record's verdict; nothing is claimed that the record does not hold;
* no identifier (claim, constraint or reason code, True/False) reaches the text a person reads;
* the English reading has no Korean left in it.
"""

import re

import pytest

from traction_workbench import api
from traction_workbench.i18n import language, set_language
from traction_workbench.insight import num
from traction_workbench.insight.decision import decision_insight

PRESETS = {p["key"]: p for p in api.PRESETS}
CODE = re.compile(r"\b(?:[a-z]+_[a-z_]+|[A-Z]{3,}_[A-Z_]+|True|False)\b")
# identifiers that are names in their own right inside the reading (formulas and quoted engine terms)
ALLOWED = {"eta_mot", "eta_regen", "P_shaft", "P_dc", "iq_min", "id_A", "iq_A", "R_s", "T_e", "P_cu", "P_inv", "P_ac",
           "f_e", "v_d", "I_dc", "P_ESR"}


@pytest.fixture(autouse=True)
def _korean():
    before = language()
    set_language("ko")
    yield
    set_language(before)


def _record(key, **an):
    p = PRESETS[key]
    analyses = {**(p.get("analyses") or {}), **an}
    return api.evaluate({"requirement": p["req"], "analyses": analyses})


def _text(ins) -> str:
    return "\n".join(ins.plain_lines())


def test_the_verdict_and_headline_follow_the_record():
    for key, expected in (("ts012_600", "PASS"), ("ts012_450", "FAIL"), ("ts012_10s", "UNKNOWN"), ("regen_80", "PASS"),
                          ("regen_100", "FAIL"), ("dis_350", "FAIL"), ("range", "PASS"), ("stall", "PASS")):
        rec = _record(key)
        ins = decision_insight(rec)
        assert rec["verdict"]["verdict"] == expected and ins.verdict == expected
        word = {"PASS": "달성 가능", "FAIL": "불가능 (증명됨)", "UNKNOWN": "미확정"}[expected]
        assert word in ins.headline, (key, ins.headline)


def test_a_pass_reads_its_margin_capability_and_limiting_constraints_from_the_record():
    rec = _record("ts012_600", dominance=True)
    c = rec["conditions"][0]
    ins = decision_insight(rec)
    cap = c["policy_capability"]["achieved_value_Nm"]
    m = c["torque_capability_margin_Nm"]
    assert f"{num(cap)} N·m" in ins.headline and f"{num(m)} N·m" in ins.headline
    txt = _text(ins)
    op = c["policy_solution"]["operating_point"]
    cur = next(x for x in op["constraints"] if x["name"] == "CURRENT")
    assert f"{num(cur['demand'])} A / {num(cur['limit'])} A" in txt           # |i| of the limit, as computed
    assert "전압 한계에 닿아 있음" in txt and "약계자 운전" in txt              # VOLTAGE ACTIVE and id < 0 in the record
    # the dominance ranking is the record's: the largest +1 % gain first, with its value
    dom = sorted(rec["analyses"]["dominance"]["single"], key=lambda r: -r["gain_Nm"])
    first = next(s for s in ins.sections if s.title.startswith("병목")).items[0]
    assert "DC 방전 전력 한계" in first.text and f"+{num(dom[0]['gain_Nm'], 3)} N·m" in first.text
    # losses add up to the record's breakdown
    total = sum(op["loss_breakdown_W"].values())
    assert f"{num(total / 1e3)} kW" in txt


def test_a_proven_failure_names_the_necessary_condition_and_what_would_change_it():
    rec = _record("ts012_450")
    ins = decision_insight(rec)
    txt = _text(ins)
    ev = next(e for cl in rec["conditions"][0]["policy_solution"]["claims"] for e in cl.get("evidence") or []
              if "abs_vd_lower_bound_V" in (e.get("data") or {}))["data"]
    assert f"{num(ev['abs_vd_lower_bound_V'])} V" in txt and f"{num(ev['voltage_budget_V'])} V" in txt
    sz = {s["parameter"]["parameter"]: s for s in rec["analyses"]["sizing"]}
    assert f"≥ {num(sz['Vdc_V']['minimal_feasible_value'])} V" in txt                 # the sized Vdc edge
    assert "전체에서 불가능" in txt and not sz["I_peak_max_A"]["feasible_ranges"]       # more current does not help
    joint = rec["analyses"]["relaxation"]["joint"][0]
    assert f"+{100 * joint['minimal_relative_relaxation_each']:.1f} %" in txt


def test_an_open_duration_is_read_as_undecided_not_as_a_pass():
    rec = _record("ts012_10s")
    ins = decision_insight(rec)
    assert rec["verdict"]["verdict"] == "UNKNOWN"
    assert "정적으로는 가능" in ins.headline and "지속시간 근거 없음" in ins.headline
    dur = [it for s in ins.sections for it in s.items if "지속시간" in it.text and it.level == "open"]
    assert dur, "the duration item stays open"


def test_braking_reads_the_braking_direction_and_an_uncertified_capability_as_found_only():
    rec = _record("regen_80")
    ins = decision_insight(rec)
    pc = rec["conditions"][0]["policy_capability"]
    assert not pc["certified"]
    assert "회생 제동 80 N·m" in ins.headline and "찾은 값, 인증 안 됨" in ins.headline
    assert "음수 = 배터리로 회수" in _text(ins)


def test_no_identifier_reaches_the_reading_and_english_is_complete():
    for key in PRESETS:
        rec = _record(key, dominance=True, relaxation=True)
        for lang in ("ko", "en"):
            set_language(lang)
            ins = decision_insight(rec)
            txt = _text(ins)
            bad = {m for m in CODE.findall(txt) if m not in ALLOWED}
            # engine scope strings quoted verbatim may hold lower-case words joined by underscores; none are expected
            assert not bad, (key, lang, sorted(bad))
            if lang == "en":
                assert not re.search(r"[가-힣]", txt), (key, [ln for ln in txt.splitlines()
                                                                      if re.search(r"[가-힣]", ln)][:3])
            assert ins.html() and ins.markdown().startswith("**")
        set_language("ko")


def test_the_reading_never_changes_the_record():
    import copy
    import json
    rec = _record("dis_350", dominance=True, relaxation=True)
    before = json.dumps(rec, sort_keys=True, default=str)
    decision_insight(copy.deepcopy(rec))
    decision_insight(rec)
    assert json.dumps(rec, sort_keys=True, default=str) == before
