"""Extract the structured parts of the customer reference document into ``doc_extract.json`` (the input of
``build.py``).  The document itself (``ref_doc.txt``, its text export) is not part of the repository: its extraction
is.

    python extract.py path/to/ref_doc.txt
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
L = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "ref_doc.txt").read_text(encoding="utf-8").split("\n")
T = "\n".join(L)
out = {}


def rows(block):
    """table rows ' | a | b | c' -> [a, b, c]"""
    res = []
    for ln in block.split("\n"):
        if ln.startswith(" | "):
            res.append([c.strip() for c in ln.split("|")[1:]])
    return res


def section(start, end):
    i = T.index(start)
    j = T.index(end, i + len(start))
    return T[i:j]


# -- groups A..I: heading + provenance + simulation note
grp = {}
cur = None
pending = None          # the provenance tag stands on the line BEFORE its heading
for k, ln in enumerate(L[164:386]):
    if ln.startswith("## ") and len(ln) > 3 and ln[3] in "ABCDEFGHI" and ln[4] == ".":
        cur = ln[3]
        grp[cur] = {"title": ln[6:].strip(), "items": []}
        pending = None
    elif ln.strip() in ("CONFIRMED", "CUSTOMER-PAST", "PROJECT", "DERIVED", "RESEARCH", "OPEN", "CONFLICT"):
        pending = ln.strip()
    elif ln.startswith("### ") and cur:
        grp[cur]["items"].append({"text": ln[4:].strip(), "provenance": pending})
        pending = None
    elif cur and grp[cur]["items"] and ln.startswith("시뮬레이션 반영:"):
        grp[cur]["items"][-1]["sim"] = ln.split(":", 1)[1].strip()
out["groups"] = grp

# -- modules and scenarios
out["modules"] = [r for r in rows(section("## 3. 필수 Simulation Module", "## 4.")) if r[0].startswith("SIM-")]
out["scenarios"] = [r for r in rows(section("## 4. 최소 Scenario", "## 5.")) if r[0].startswith("SCN-")]
out["timing"] = [r for r in rows(section("## 5. Timing", "## 6.")) if r[0] != "구간"]
sec6 = section("## 6. 시뮬레이션에서 반드시 OPEN", "## 7.")
out["open"] = [ln[2:].strip() for ln in sec6.split("\n") if ln.startswith("- ")]
sec7 = section("## 7. 권장 자동 산출물", "## Appendix A")
out["outputs"] = [ln[2:].strip() for ln in sec7.split("\n") if ln.startswith("- ")]
sec0 = T[re.search(r"^### [^\n]*구현 규칙$", T, re.M).start():T.index("## 1.")]
out["rules"] = [ln[2:].strip() for ln in sec0.split("\n") if ln.startswith("- ")]
m = re.search(r'"hard_rules": \[(.*?)\]', T, re.S)
out["hard_rules"] = [s.strip().strip('"') for s in m.group(1).split('",') if s.strip()]
sec1 = section("## 1. 최종 시뮬레이션이 답해야 하는 질문", "Machine-readable manifest")
qs = re.findall(r"### (.)\s*(.+)\n(.*?)\n", sec1)
out["questions"] = [{"mark": a, "name": b, "text": c} for a, b, c in qs]
out["registry"] = [r for r in rows(section("## 2. Parameter", "## A.")) if r[0] != "Parameter / 값"]

# -- WI table, safe-state table, KL15 table, ASIL table, metric sets
out["wi"] = [r for r in rows(section("## 사진에서 직접 확인한 내용", "Critical은 ASIL이 아니다")) if r[0] != "사진"]
out["cond"] = [r for r in rows(section("## 고객의 safe state를", "이는 “인버터")) if r[0].startswith("C")]
out["concepts"] = [r for r in rows(section("이는 “인버터", "14722의 합의")) if r[0] != "현재 컨셉"]
out["kl15"] = [r for r in rows(section("## Safe-state 진입 이유별", "원문상 분명한 것")) if r[0] != "진입 이유"]
out["asil"] = [r for r in rows(section("## ASIL은 MAX 표기를", "정상 Torque Control")) if r[0] != "원문 표기"]
out["metric_sets"] = [r for r in rows(section("정상 Torque Control", "TLSR 08이 양쪽")) if r[0] != "Metric 집합"]

# -- safety mechanisms
smtab = {r[0].split(" · ")[0]: {"name": r[0].split(" · ", 1)[1], "logical": r[1], "physical": r[2]}
         for r in rows(section("| 메커니즘 | 기능논리 책임", "SM-I-01 · 검출")) if r[0].startswith("SM-I-")}
smtxt = section("SM-I-01 · 검출", "### 토크 감시 하나를")
sms = []
for b in re.split(r"\n(?=SM-I-\d\d · )", "\n" + smtxt)[1:]:
    ls = b.split("\n")
    sid, kind = ls[0].split(" · ", 1)
    d = {"id": sid, "kind": kind, "title": ls[1].lstrip("# ").strip()}
    for r in rows(b):
        if r[0] != "설계 항목":
            d[r[0]] = r[1]
    d.update({"logical": smtab[sid]["logical"], "physical": smtab[sid]["physical"]})
    sms.append(d)
out["sm"] = sms
out["shared_deps"] = [r for r in rows(section("### 공유 의존성이 검출의 사각지대", "추가 업무 21개")) if r[0] != "공유 항목"]

# -- actions
acttxt = section("## 추가로 해야 할 업무 21개", "### 착수 순서와 계획 단위")
acts = []
for b in re.split(r"\n(?=ACT-\d\d · P\d)", acttxt)[1:]:
    ls = b.split("\n")
    aid, prio = ls[0].split(" · ")
    d = {"id": aid, "priority": prio, "title": ls[1].lstrip("# ").strip(), "basis": ls[2].strip()}
    for r in rows(b):
        if r[0] != "항목":
            d[r[0]] = r[1]
    acts.append(d)
out["actions"] = acts

# -- TSR-FRONT
ftxt = section("## 기존 초안에 추가·수정할 TSR 18개", "Polarion/System Composer 반영안")
fr = []
for b in re.split(r"\n(?=TSR-FRONT-\d\d\n)", ftxt)[1:]:
    ls = b.split("\n")
    d = {"id": ls[0].strip(), "title": ls[1].lstrip("# ").strip()}
    m = re.match(r"(고객 [^·]+) · ([^A-Z]*?)((?:While|When|Following|Throughout|For|The|Before)\b.*)$", ls[2])
    if m:
        d["source"], d["allocation"], d["statement"] = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    else:
        d["statement"] = ls[2]
    for r in rows(b):
        if r[0] != "검토 항목":
            d[r[0]] = r[1]
    fr.append(d)
out["front"] = fr

out["delta42"] = [r for r in rows(section("## 앞서 작성한 42개 추가 TSR 후보의 처리", "이 보고서는 이전 파일을")) if r[0] != "이전 후보"]
out["verification"] = [r for r in rows(section("## 고객 문서 때문에 추가·강화해야 할 검증 16개", "관측의 최소 계약")) if r[0].startswith("V")]
out["confirm"] = [r for r in rows(section("## 고객·내부 담당자와 닫아야 할 확인 사항", "## Appendix B")) if r[0].startswith("Q")]

# -- TSR-ADD
atxt = T[T.index("TSR-ADD-001 · 입력·통신기존"):]
adds = []
for b in re.split(r"\n(?=TSR-ADD-\d{3} · [^\n]*Draft\n)", "\n" + atxt)[1:]:
    ls = b.split("\n")
    head = ls[0]
    m = re.match(r"(TSR-ADD-\d{3}) · (.+?)(기존 요구 구체화|추가 후보|조건부 후보) (P\d) · Draft", head)
    d = {"id": m.group(1), "area": m.group(2), "class": m.group(3), "priority": m.group(4),
         "title": ls[1].lstrip("# ").strip(), "why": ls[2].strip()}
    body = "\n".join(ls[3:])
    keys = ["적용 조건", "기능논리 책임", "물리 실현 범위", "ASIL 처리", "확정할 값/조건", "검증·합격 기준", "반응 계약"]
    stmt = re.match(r"(.*?)적용 조건", body, re.S)
    d["statement"] = stmt.group(1).strip() if stmt else ""
    for k, nxt in zip(keys, keys[1:] + ["\n"]):
        mm = re.search(re.escape(k) + r"(.*?)" + (re.escape(nxt) if nxt != "\n" else r"\n"), body, re.S)
        if mm:
            d[k] = mm.group(1).strip()
    for k in ("설계·검증 시 주의:", "기존 문서와의 관계:"):
        mm = re.search(re.escape(k) + r"\s*(.*?)\n", body)
        if mm:
            d[k.rstrip(":")] = mm.group(1).strip()
    adds.append(d)
out["add"] = adds
out["common_contract"] = [r for r in rows(section("## 42개 요구에 공통으로 붙일 반응·시간 계약", "T_DET,max")) if r[0] != "통제할 항목"]
out["eight_contracts"] = rows(section("## 빈칸을 숫자로 채우기 전에 확정할 8개 계약", "## 현재 기능논리")) if "## 빈칸을" in T else []

(HERE / "doc_extract.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print({k: len(v) for k, v in out.items()})
print([len(g["items"]) for g in grp.values()], sum(len(g["items"]) for g in grp.values()))
