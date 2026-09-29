"""Display names for identifiers shown to people (the identifiers themselves stay in data, records and files)."""

from __future__ import annotations

from ..i18n import tr

_PARAM = {
    "Vdc_V": ("DC 전압 Vdc", "DC voltage Vdc"),
    "I_peak_max_A": ("인버터 전류 한계 (상 peak)", "inverter current limit (phase peak)"),
    "voltage_reserve_fraction": ("전압 예비율 r_v", "voltage reserve r_v"),
    "voltage_budget_scale": ("전압 예산 배율", "voltage budget scale"),
    "discharge_power_max_W": ("DC 방전 전력 한계", "DC discharge power limit"),
    "charge_power_max_W": ("DC 충전 전력 한계", "DC charge power limit"),
    "discharge_current_max_A": ("DC 방전 전류 한계", "DC discharge current limit"),
    "charge_current_max_A": ("DC 충전 전류 한계", "DC charge current limit"),
    "id_min_A": ("id 하한 (선언 도메인)", "id lower bound (declared domain)"),
    "psi_pm_Wb": ("PM 자속 ψ_PM", "PM flux ψ_PM"),
    "Ld_H": ("d축 인덕턴스 Ld", "d-axis inductance Ld"),
    "Lq_H": ("q축 인덕턴스 Lq", "q-axis inductance Lq"),
    "Rs_ohm": ("상저항 Rs", "phase resistance Rs"),
    "inverter_loss_scale": ("인버터 손실 배율", "inverter loss scale"),
    "rotational_loss_scale": ("회전 손실 배율", "rotational loss scale"),
}
_KIND = {"boundary": ("시나리오 경계", "scenario boundary"), "hardware": ("하드웨어", "hardware"),
         "design": ("설계 선택", "design choice"), "diagnostic": ("진단용", "diagnostic"), "data": ("데이터", "data")}


def param_label(name: str) -> str:
    ko_en = _PARAM.get(name)
    return tr(*ko_en) if ko_en else name


def change_kind_label(kind: str) -> str:
    ko_en = _KIND.get(kind)
    return tr(*ko_en) if ko_en else kind


# claims (the judged items of a result), constraints, reasons and states: the identifier stays in the data
_CLAIM = {
    "requirement_at_condition": ("조건에서의 요구 충족", "requirement at the condition"),
    "electrical_existence": ("전기적 해 존재 (전압·전류·운전 영역)", "electrical solution (voltage, current, domain)"),
    "policy_static": ("최소전류 정책의 정적 달성 (DC 포함)", "static achievement by the minimum-current policy (incl. DC)"),
    "dc_source": ("DC 소스 한계 (평균 전력·전류)", "DC source limits (average power, current)"),
    "physical_existence_with_dc": ("DC 포함 물리적 가능성 (필요조건)", "physical possibility incl. DC (necessary condition)"),
    "duration": ("지속시간", "duration"),
    "source_coupling": ("배터리 소스 결합 (단자 전압)", "battery source coupling (terminal voltage)"),
    "dc_robust_all_admissible": ("DC 한계 — 허용 손실 전체에서", "DC limits — over every admissible loss"),
    "dc_outer_enclosure": ("DC 한계 — 외곽 포함 구간", "DC limits — outer enclosure"),
    "dc_actual_unknown_loss": ("DC 한계 — 실제 손실 미상", "DC limits — actual loss unknown"),
    "thermal_duration": ("열 지속시간", "thermal duration"),
    "repeated_load": ("반복 부하", "repeated load"),
    "ftti": ("FTTI 시간 예산", "FTTI time budget"),
    "passive_discharge": ("패시브 방전", "passive discharge"),
    "regen_overvoltage": ("회생 과전압", "regeneration overvoltage"),
    "conducted_emission": ("전도성 방출", "conducted emission"),
    "ripple_requirement": ("리플 요구", "ripple requirement"),
    "capacitor_loss": ("커패시터 ESR 손실", "capacitor ESR loss"),
    "capacitor_life": ("커패시터 수명", "capacitor life"),
    "timing": ("FTTI 타이밍 체인", "FTTI timing chain"),
    "discharge": ("능동 방전", "active discharge"),
    "overvoltage": ("회생 과전압", "regeneration overvoltage"),
    "safe_state": ("안전 상태", "safe state"),
    "emi_screening": ("전도성 EMI 스크리닝", "conducted EMI screening"),
    "load_rejection": ("부하 차단", "load rejection"),
    "module_loss": ("모듈 손실", "module loss"),
    "cranking": ("크랭킹", "cranking"),
    "thermal_cycling_damage": ("열 사이클 손상", "thermal-cycling damage"),
    "supplied_policy": ("공급된 제어 정책", "supplied control policy"),
    "asc_joint": ("ASC 과도 (두 전류-시간 요구 동시)", "ASC transient (both current-time requirements)"),
    "active_discharge": ("능동 방전", "active discharge"),
    "safe_state_selection": ("안전 상태 스크리닝", "safe-state screening"),
    "threshold_window": ("임계값 창", "threshold window"),
}
_CONSTRAINT = {
    "VOLTAGE": ("전압 한계 (명령 전압 예산)", "voltage limit (command budget)"),
    "CURRENT": ("인버터 전류 한계", "inverter current limit"),
    "DC_DISCHARGE_POWER": ("DC 방전 전력 한계", "DC discharge power limit"),
    "DC_CHARGE_POWER": ("DC 충전 전력 한계", "DC charge power limit"),
    "DC_DISCHARGE_CURRENT": ("DC 방전 전류 한계", "DC discharge current limit"),
    "DC_CHARGE_CURRENT": ("DC 충전 전류 한계", "DC charge current limit"),
    "ID_MIN": ("id 하한 (운전 영역)", "id lower bound (domain)"),
    "ID_MAX": ("id 상한 (운전 영역)", "id upper bound (domain)"),
    "IQ_MIN": ("iq 하한 (운전 영역)", "iq lower bound (domain)"),
    "IQ_MAX": ("iq 상한 (운전 영역)", "iq upper bound (domain)"),
    "SPEED_MIN": ("속도 하한", "speed lower bound"),
    "SPEED_MAX": ("속도 상한", "speed upper bound"),
    "DOMAIN": ("운전 영역", "operating domain"),
}
_REASON = {
    "MISSING_INPUT": ("입력 누락", "missing input"),
    "OUTSIDE_MODEL_DOMAIN": ("모델 적용 범위 밖", "outside the model domain"),
    "OUTSIDE_ALLOWED_OPERATING_DOMAIN": ("허용 운전 영역 밖", "outside the allowed operating domain"),
    "UNVALIDATED_DURATION": ("지속시간 근거 없음 (검증된 정격·열모델 없음)", "no validated duration evidence"),
    "UNCERTAINTY_OVERLAP": ("불확도 구간이 한계와 겹침", "uncertainty overlaps the limit"),
    "NUMERICAL_UNRESOLVED": ("수치적으로 미확정 (경계가 허용오차 안)", "numerically unresolved"),
    "INVALID_INPUT": ("잘못된 입력", "invalid input"),
    "POLICY_LIMITATION": ("정책의 한계 (다른 제어로는 가능할 수 있음)", "policy limitation"),
    "SAMPLED_COVERAGE": ("표본 검사만 (연속 구간 증명 아님)", "sampled coverage only"),
    "BOUNDARY_WITHIN_TOLERANCE": ("경계가 허용오차 안", "boundary within tolerance"),
    "OUT_OF_SCOPE": ("평가 범위 밖", "out of scope"),
    "CONSTRAINT_VIOLATION": ("제약 위반", "constraint violation"),
    "NECESSARY_CONDITION_VIOLATED": ("필요조건 위반 (어떤 제어로도 불가)", "necessary condition violated"),
    "RATING_NOT_MET": ("검증된 정격 밖", "outside the validated rating"),
    "CONFLICTING_EVIDENCE": ("근거가 서로 충돌", "conflicting evidence"),
    "BOUND_INCONCLUSIVE": ("보수 상한이 한계를 넘음 (위반 증거는 없음)", "conservative bound inconclusive"),
    "REQUIREMENT_INCOMPLETE": ("요구 정의 불완전", "requirement incomplete"),
    "COUPLED_MODEL_REQUIRED": ("소스·부하 결합 모델 필요", "coupled model required"),
    "SCREENING_ONLY": ("스크리닝 모델 (여유·필요량만, 합격 판정 아님)", "screening only"),
    "APPLICABILITY_UNCONFIRMED": ("근거가 이 제품·조건에 묶이지 않음", "applicability unconfirmed"),
}
_STATE = {
    "FEASIBLE": ("가능", "feasible"), "INFEASIBLE": ("불가능 (증명됨)", "infeasible (proven)"),
    "UNKNOWN": ("미확정", "unknown"), "PASS": ("만족", "pass"), "FAIL": ("불만족", "fail"),
    "ACTIVE": ("활성 (한계에 닿음)", "active (at the limit)"), "SATISFIED": ("만족 (여유 있음)", "satisfied (margin)"),
    "VIOLATED": ("위반", "violated"), "CERTIFIED": ("인증됨", "certified"),
}
_SECTION = {
    "drive": ("구동 모델", "drive model"), "dc_source": ("DC 소스", "DC source"), "module": ("파워 모듈", "power module"),
    "alternatives": ("설계 대안", "design alternatives"), "dc_link": ("DC-link 커패시터", "DC-link capacitor"),
    "controller": ("제어기·변조", "controller and modulation"), "thermal": ("냉각·열 회로망", "cooling and thermal networks"),
    "driveline": ("감속기·드라이브라인", "reducer and driveline"), "safety": ("안전 (FTTI·안전 상태 규칙)", "safety"),
    "emi_setup": ("EMI 시험 구성", "EMI setup"),
}


def _lookup(table: dict, key) -> str:
    ko_en = table.get(key)
    return tr(*ko_en) if ko_en else str(key)


def claim_label(name: str) -> str:
    return _lookup(_CLAIM, name)


def constraint_label(name: str) -> str:
    return _lookup(_CONSTRAINT, name)


def reason_label(code: str) -> str:
    return _lookup(_REASON, code)


def state_label(state: str) -> str:
    return _lookup(_STATE, state)


def section_label(name: str) -> str:
    return _lookup(_SECTION, name)


def yes_no(v) -> str:
    """A boolean for people: 예 / 아니오 (never True / False)."""
    if v is None:
        return "—"
    return tr("예", "yes") if v else tr("아니오", "no")
