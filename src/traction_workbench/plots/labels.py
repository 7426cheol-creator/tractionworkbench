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
