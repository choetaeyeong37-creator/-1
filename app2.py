"""전력질주 — 스마트 에너지 & 역률 종합 시뮬레이터

실행:  python -m pip install streamlit pandas numpy plotly
       streamlit run app2.py
권장 Streamlit 버전: 1.37 이상 (구버전/신버전 width 인자 차이는 자동 처리)
"""
from __future__ import annotations

import inspect
import math
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# =========================================================
# 0. 계산 로직 (UI와 분리 — 단독 테스트 가능)
# =========================================================
# --- LOGIC START ---

SEASONS = {
    "other": {
        "label": "기타계절 — 봄·가을 (3~6월, 9~11월)",
        "limits": (200, 400),
        "super": False,
        "avg_1p": 155.0,   # 1인가구 계절 평균 사용량(kWh)
        "avg_all": 300.0,  # 전국 가구 계절 평균 사용량(kWh)
    },
    "winter": {
        "label": "기타계절 — 동계 (12~2월, 슈퍼유저 적용)",
        "limits": (200, 400),
        "super": True,
        "avg_1p": 195.0,
        "avg_all": 345.0,
    },
    "summer": {
        "label": "하계 — 여름철 (7~8월, 슈퍼유저 적용)",
        "limits": (300, 450),
        "super": True,
        "avg_1p": 225.0,
        "avg_all": 395.0,
    },
}

RATES = {
    "low": {  # 주택용전력(저압) — 단독주택, 다세대, 원룸 등
        "label": "주택용 저압 (단독·다세대·원룸)",
        "base": (910, 1600, 7300),
        "energy": (120.0, 214.6, 307.3),
        "super_rate": 736.2,  # 하계/동계 1,000kWh 초과 전력량요금 (원/kWh)
    },
    "high": {  # 주택용전력(고압) — 아파트 등
        "label": "주택용 고압 (아파트)",
        "base": (730, 1260, 6060),
        "energy": (105.0, 174.0, 242.3),
        "super_rate": 601.3,  # 하계/동계 1,000kWh 초과 전력량요금 (원/kWh)
    },
}

CLIMATE_RATE = 9.0  # 기후환경요금 (원/kWh)
FUEL_RATE = 5.0     # 연료비조정단가 (원/kWh)
VAT_RATE = 0.10     # 부가가치세 (10%)
FUND_RATE = 0.037   # 전력산업기반기금 (3.7%)

COFFEE_PRICE = 4500    # 아메리카노 1잔 환산 기준가 (원)
CHICKEN_PRICE = 20000  # 치킨 1마리 환산 기준가 (원)

# 24시간 상시 구동이 필수적인 기기 키워드 (절감 가이드 시간 단축 추천 및 예산 조절에서 제외)
ESSENTIAL_24H_KEYWORDS = ("냉장고", "공유기", "와이파이", "모뎀", "라우터")


def is_24h_essential(row: pd.Series) -> bool:
    """하루 24시간 구동되거나 상시 전원이 필수인 기기(냉장고, 공유기 등)인지 판별한다."""
    name = str(row.get("기기명", ""))
    hours = float(row.get("하루 사용(h)", 0.0))
    return hours >= 24.0 or any(kw in name for kw in ESSENTIAL_24H_KEYWORDS)


def calculate_bill(kwh: float, season: str = "other", voltage: str = "low") -> dict:
    """월 사용량(kWh), 계절, 전압(저압/고압)으로 주택용 전기요금을 항목별로 계산한다."""
    kwh = max(0.0, float(kwh))
    l1, l2 = SEASONS[season]["limits"]
    is_super_season = SEASONS[season]["super"]

    if kwh <= 0:
        return dict(
            total=0, tier=1, is_super=False, applied_rate=RATES[voltage]["energy"][0],
            base=0, energy=0, climate=0, fuel=0, vat=0, fund=0
        )

    tier = 1 if kwh <= l1 else (2 if kwh <= l2 else 3)
    is_super = bool(is_super_season and kwh > 1000.0)

    base_fee = RATES[voltage]["base"][tier - 1]
    e_rates = RATES[voltage]["energy"]
    super_rate = RATES[voltage]["super_rate"]

    if kwh <= l1:
        energy = kwh * e_rates[0]
        applied_rate = e_rates[0]
    elif kwh <= l2:
        energy = (l1 * e_rates[0]) + ((kwh - l1) * e_rates[1])
        applied_rate = e_rates[1]
    else:
        if is_super:
            energy = (
                (l1 * e_rates[0])
                + ((l2 - l1) * e_rates[1])
                + ((1000.0 - l2) * e_rates[2])
                + ((kwh - 1000.0) * super_rate)
            )
            applied_rate = super_rate
        else:
            energy = (l1 * e_rates[0]) + ((l2 - l1) * e_rates[1]) + ((kwh - l2) * e_rates[2])
            applied_rate = e_rates[2]

    climate = kwh * CLIMATE_RATE
    fuel = kwh * FUEL_RATE

    subtotal = math.floor(base_fee + energy + climate + fuel)  # 전기요금계 (원 미만 절사)
    vat = int(subtotal * VAT_RATE + 0.5)                      # 부가가치세 (원 미만 4사5입)
    fund = int(subtotal * FUND_RATE / 10) * 10                # 전력산업기반기금 (10원 미만 절사)
    total = (subtotal + vat + fund) // 10 * 10                # 청구금액 (10원 미만 절사)

    return dict(
        total=total,
        tier=tier,
        is_super=is_super,
        applied_rate=applied_rate,
        base=base_fee,
        energy=round(energy),
        climate=round(climate),
        fuel=round(fuel),
        vat=vat,
        fund=fund,
    )


def bill_total(kwh: float, season: str = "other", voltage: str = "low") -> int:
    return calculate_bill(kwh, season, voltage)["total"]


def flat_tier1_bill_total(kwh: float, voltage: str = "low") -> int:
    """누진제가 없다고 가정할 때(1단계 기본요금 및 1단계 단가만 적용 시)의 비교용 요금."""
    kwh = max(0.0, float(kwh))
    if kwh <= 0:
        return 0
    base_fee = RATES[voltage]["base"][0]
    energy = kwh * RATES[voltage]["energy"][0]
    subtotal = math.floor(base_fee + energy + kwh * (CLIMATE_RATE + FUEL_RATE))
    vat = int(subtotal * VAT_RATE + 0.5)
    fund = int(subtotal * FUND_RATE / 10) * 10
    return (subtotal + vat + fund) // 10 * 10


def max_kwh_for_budget(budget: float, season: str = "other", voltage: str = "low") -> float:
    """목표 금액(예산 또는 청구 요금)에 해당하는 월 사용량(kWh) — 이분 탐색."""
    lo, hi = 0.0, 3000.0
    if bill_total(hi, season, voltage) <= budget:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2
        if bill_total(mid, season, voltage) <= budget:
            lo = mid
        else:
            hi = mid
    return lo


def calc_energy_cashback(last_year_kwh: float, cur_kwh: float) -> tuple[int, float]:
    """작년 동월 대비 절감량으로 한전 주택용 에너지캐시백 예상 환급액(원)과 절감률(%)을 계산한다."""
    if last_year_kwh <= 0 or cur_kwh >= last_year_kwh:
        return 0, 0.0
    saved_kwh = last_year_kwh - cur_kwh
    save_rate = (saved_kwh / last_year_kwh) * 100.0
    if save_rate < 3.0:
        return 0, save_rate
    capped_saved_kwh = min(saved_kwh, last_year_kwh * 0.30)
    if save_rate >= 20.0:
        unit = 100
    elif save_rate >= 10.0:
        unit = 80
    elif save_rate >= 5.0:
        unit = 60
    else:
        unit = 30
    return int(capped_saved_kwh * unit), save_rate


APPLIANCE_COLS = [
    "기기명", "소비전력(W)", "가동률(%)", "하루 사용(h)",
    "월 사용일수", "대기전력(W)", "대기차단",
]


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """표 편집 결과를 계산 가능한 형태로 정리한다 (빈 칸·범위 밖 값 보정)."""
    d = df.copy()
    for c in APPLIANCE_COLS:
        if c not in d.columns:
            d[c] = None
    d = d[APPLIANCE_COLS]
    d["기기명"] = d["기기명"].fillna("").astype(str).str.strip()
    defaults = {
        "소비전력(W)": (0.0, 0.0, 20000.0),
        "가동률(%)": (100.0, 0.0, 100.0),
        "하루 사용(h)": (0.0, 0.0, 24.0),
        "월 사용일수": (30.0, 0.0, 31.0),
        "대기전력(W)": (0.0, 0.0, 500.0),
    }
    for col, (dflt, lo, hi) in defaults.items():
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(dflt).clip(lo, hi)
    d["대기차단"] = d["대기차단"].map(lambda v: bool(v) if pd.notna(v) else False).astype(bool)
    d = d[~((d["기기명"] == "") & (d["소비전력(W)"] == 0))]
    d.loc[d["기기명"] == "", "기기명"] = "이름 없는 기기"
    return d.reset_index(drop=True)


def analyze_appliances(
    df: pd.DataFrame, days: int, block_all: bool, season: str, voltage: str
) -> pd.DataFrame:
    """기기별 월 사용량과 '끄면 절약되는 요금'을 계산한다."""
    d = df.copy()
    use_days = d["월 사용일수"].clip(upper=days)
    d["사용 kWh"] = (
        d["소비전력(W)"] * d["가동률(%)"] / 100.0 * d["하루 사용(h)"] * use_days / 1000.0
    )
    standby_hours = (24.0 * days - d["하루 사용(h)"] * use_days).clip(lower=0)
    d["대기 kWh(전체)"] = d["대기전력(W)"] * standby_hours / 1000.0
    blocked = d["대기차단"] | bool(block_all)
    d["대기 kWh"] = np.where(blocked, 0.0, d["대기 kWh(전체)"])
    d["월 사용량(kWh)"] = d["사용 kWh"] + d["대기 kWh"]
    total = float(d["월 사용량(kWh)"].sum())
    full = bill_total(total, season, voltage)
    d["끄면 절약(원/월)"] = [
        full - bill_total(max(0.0, total - k), season, voltage) for k in d["월 사용량(kWh)"]
    ]
    d["비중(%)"] = d["월 사용량(kWh)"] / total * 100 if total > 0 else 0.0
    d["상시가동"] = [is_24h_essential(r) for _, r in d.iterrows()]
    return d


def kwh_per_hour(row: pd.Series, days: int, block_all: bool) -> float:
    """하루 사용시간을 1시간 늘릴 때 월 사용량 변화(kWh). 대기전력이 줄어드는 만큼 보정."""
    use_days = min(float(row["월 사용일수"]), days)
    standby_w = 0.0 if (row["대기차단"] or block_all) else float(row["대기전력(W)"])
    return use_days * (row["소비전력(W)"] * row["가동률(%)"] / 100.0 - standby_w) / 1000.0


# ---- 역률 계산 및 시나리오 ----
STD_CAPACITORS = [5, 7.5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300]

LOAD_SCENARIOS = {
    "직접 입력 (사용자 설정)": {"supply": "3상 380V", "p_kw": 100.0, "pf1": 75, "pf2": 95},
    "소형 가공 공장 (유도전동기·컴프레서 위주 — 대표 역률 70%)": {"supply": "3상 380V", "p_kw": 150.0, "pf1": 70, "pf2": 95},
    "상업용 빌딩/매장 (냉난방기·형광등 혼합 — 대표 역률 80%)": {"supply": "3상 380V", "p_kw": 80.0, "pf1": 80, "pf2": 95},
    "대형 마트/물류창고 (냉동설비·컨베이어 — 대표 역률 75%)": {"supply": "3상 380V", "p_kw": 250.0, "pf1": 75, "pf2": 95},
    "소규모 카페/음식점 (단상 인버터·전열기기 혼합 — 대표 역률 85%)": {"supply": "단상 220V", "p_kw": 25.0, "pf1": 85, "pf2": 95},
}


def pf_fee_rate(pf_pct: float, step: float) -> float:
    """기본요금 가감률. 90% 미달(60%까지)은 할증(+), 90% 초과(95%까지)는 할인(-)."""
    if pf_pct < 90:
        return (90.0 - max(pf_pct, 60.0)) * step
    return -(min(pf_pct, 95.0) - 90.0) * step


def pf_after_capacitor(p_kw: float, pf1: float, qc_kvar: float) -> tuple[float, bool]:
    """콘덴서 qc 설치 후 역률과 진상(과보상) 여부."""
    q1 = p_kw * math.tan(math.acos(pf1))
    q_res = q1 - qc_kvar
    return p_kw / math.hypot(p_kw, q_res), q_res < 0


def analyze_pf_interval_efficiency(
    p_kw: float, base_fee: float, step: float, cap_unit: float
) -> pd.DataFrame:
    """역률 5% 구간별 필요 콘덴서 용량, 1%당 소요 용량, 손실 감소율, 요금 절감 및 회수기간을 계산한다."""
    intervals = [(60, 65), (65, 70), (70, 75), (75, 80), (80, 85), (85, 90), (90, 95), (95, 100)]
    rows = []
    for p1_pct, p2_pct in intervals:
        c1, c2 = p1_pct / 100.0, p2_pct / 100.0
        th1, th2 = math.acos(c1), math.acos(c2)
        dq = p_kw * (math.tan(th1) - math.tan(th2))
        q_per_pct = dq / 5.0
        loss_red_seg = (1.0 - (c1 / c2) ** 2) * 100.0
        fee1 = base_fee * (1.0 + pf_fee_rate(p1_pct, step))
        fee2 = base_fee * (1.0 + pf_fee_rate(p2_pct, step))
        save_won = fee1 - fee2
        install_cost = dq * cap_unit
        payback = (install_cost / save_won) if save_won > 0 else float("inf")

        if (p1_pct, p2_pct) == (80, 85):
            note = "⭐ 최고 가성비 구간 (1%당 콘덴서 최소 소요)"
        elif (p1_pct, p2_pct) == (90, 95):
            note = "🎯 최종 목표 구간 (기본요금 할인 상한선)"
        elif p1_pct >= 95:
            note = "❌ 비효율 구간 (추가 요금할인 0원 · 용량 급증)"
        elif p1_pct < 75:
            note = "⚡ 선로 손실(I²R) 감소 폭 최대 구간"
        else:
            note = "✅ 우수 효율 구간 (할증 페널티 감면)"

        rows.append({
            "역률 개선 구간": f"{p1_pct}% → {p2_pct}%",
            "구간 필요 용량 ΔQ (kVAR)": round(dq, 2),
            "1%당 필요 용량 (kVAR/1%)": round(q_per_pct, 2),
            "구간 선로손실 감소율 (%)": round(loss_red_seg, 1),
            "월 기본요금 절감 (원)": int(round(save_won)),
            "투자 회수기간": f"{payback:.1f}개월" if payback < 999 else "회수 불가 (할인상한 초과)",
            "구간 효율 진단": note,
        })
    return pd.DataFrame(rows)


# --- LOGIC END ---


# =========================================================
# 1. 페이지 설정 & 스타일
# =========================================================
st.set_page_config(
    page_title="전력질주 — 전기요금 & 역률 시뮬레이터",
    page_icon="⚡",
    layout="wide",
)

st.markdown(
    """
    <style>
    [data-testid="stMetric"] {
        background: rgba(128,128,128,0.07);
        border: 1px solid rgba(128,128,128,0.22);
        border-radius: 12px;
        padding: 14px 16px;
    }
    .stTabs [data-baseweb="tab"] { font-weight: 600; }
    .hero-title { font-size: 1.9rem; font-weight: 800; margin: 0 0 2px 0; }
    .hero-sub { opacity: .7; margin: 0 0 14px 0; }
    .conv-box {
        background: linear-gradient(135deg, rgba(16,185,129,0.10) 0%, rgba(59,130,246,0.10) 100%);
        border: 1px solid rgba(16,185,129,0.35);
        border-radius: 12px;
        padding: 14px 18px;
        margin-top: 12px;
        margin-bottom: 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _stretch_kw(fn) -> dict:
    """Streamlit 버전에 따라 use_container_width / width="stretch" 를 자동 선택한다."""
    try:
        p = inspect.signature(fn).parameters.get("width")
    except (TypeError, ValueError):
        p = None
    if p is not None and (p.default in ("stretch", "content") or "stretch" in str(p.annotation)):
        return {"width": "stretch"}
    return {"use_container_width": True}


def show_chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, **_stretch_kw(st.plotly_chart))


def won(x: float) -> str:
    return f"{int(round(x)):,}원"


def make_progressive_curve(
    cur_kwh: float, season: str, voltage: str, height: int = 380
) -> go.Figure:
    """누진세 위험도 곡선 그래프 (기울기 강조 + 1단계 단일단가 비교선 + 구간 배경색)."""
    l1, l2 = SEASONS[season]["limits"]
    is_super_season = SEASONS[season]["super"]

    default_max = 1150.0 if is_super_season else 850.0
    max_x = max(default_max, min(1600.0, cur_kwh * 1.25))
    xs = np.linspace(0, max_x, 240)
    ys_progressive = [bill_total(float(k), season, voltage) for k in xs]
    ys_flat = [flat_tier1_bill_total(float(k), voltage) for k in xs]
    cur_bill = bill_total(cur_kwh, season, voltage)

    fig = go.Figure()

    fig.add_vrect(x0=0, x1=l1, fillcolor="#22c55e", opacity=0.06, line_width=0)
    fig.add_vrect(x0=l1, x1=l2, fillcolor="#f59e0b", opacity=0.08, line_width=0)
    if is_super_season and max_x > 1000:
        fig.add_vrect(x0=l2, x1=1000, fillcolor="#ef4444", opacity=0.10, line_width=0)
        fig.add_vrect(x0=1000, x1=max_x, fillcolor="#7f1d1d", opacity=0.16, line_width=0)
    else:
        fig.add_vrect(x0=l2, x1=max_x, fillcolor="#ef4444", opacity=0.12, line_width=0)

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys_flat,
            mode="lines",
            name="누진세 없을 때 (1단계 단가 기준)",
            line=dict(color="#64748b", width=2, dash="dot"),
            hovertemplate="1단계 유지 시: %{y:,}원<extra></extra>",
        )
    )

    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys_progressive,
            mode="lines",
            name="실제 누진세 적용 요금",
            line=dict(color="#dc2626", width=3.5),
            fill="tonexty",
            fillcolor="rgba(239, 68, 68, 0.14)",
            hovertemplate="실제 청구 요금: %{y:,}원<extra></extra>",
        )
    )

    fig.add_vline(
        x=l1, line_dash="dash", line_color="#16a34a",
        annotation_text=f"1→2단계 ({l1}kWh)", annotation_position="top left"
    )
    fig.add_vline(
        x=l2, line_dash="dash", line_color="#d97706",
        annotation_text=f"2→3단계 폭등 ({l2}kWh)", annotation_position="top left"
    )
    if is_super_season and max_x >= 1000:
        fig.add_vline(
            x=1000, line_dash="dot", line_color="#991b1b",
            annotation_text="슈퍼유저 (1,000kWh)", annotation_position="top left"
        )

    fig.add_trace(
        go.Scatter(
            x=[cur_kwh],
            y=[cur_bill],
            mode="markers+text",
            name="현재 나의 위치",
            marker=dict(color="#1d4ed8", size=13, symbol="diamond", line=dict(color="white", width=2)),
            text=[f" 현재 ({cur_kwh:.0f}kWh / {cur_bill:,}원)"],
            textposition="top right" if cur_kwh < max_x * 0.65 else "top left",
            hovertemplate="현재 사용량: %{x:.1f} kWh<br>예상 요금: %{y:,}원<extra></extra>",
        )
    )

    fig.update_layout(
        xaxis_title="월간 전력 사용량 (kWh)",
        yaxis_title="청구 전기요금 (원)",
        height=height,
        margin=dict(l=10, r=15, t=35, b=35),
        legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center"),
        hovermode="x unified",
    )
    return fig


# =========================================================
# 2. 가전 프리셋 & 세션 상태 (1인가구 기본값 + 조사 데이터 통합)
# =========================================================
PRESETS = {
    "원룸 벽걸이 에어컨 (6평형)": (1000, 60, 4, 30, 2.0),
    "냉장고 (소형 2도어)": (100, 35, 24, 30, 0.0),
    "데스크톱 PC": (300, 100, 5, 30, 2.5),
    "전자레인지": (1000, 100, 0.2, 30, 2.0),
    "와이파이 공유기": (10, 100, 24, 30, 0.0),
    "TV & 셋톱박스": (150, 100, 3, 30, 8.0),
    "원룸 드럼세탁기 (9kg)": (1500, 100, 1, 12, 1.0),
    "노트북": (60, 100, 5, 30, 0.5),
    "모니터": (30, 100, 5, 30, 0.5),
    "인덕션 (1구)": (2000, 100, 0.5, 30, 1.5),
    "전기주전자": (1500, 100, 0.1, 30, 0.0),
    "헤어드라이어": (1200, 100, 0.2, 30, 0.0),
    "선풍기": (40, 100, 6, 30, 0.0),
    "전기장판/온수매트": (180, 50, 6, 30, 0.0),
    "전기히터(소형)": (1000, 70, 2, 30, 0.0),
    "에어프라이어": (1400, 100, 0.3, 30, 1.0),
    "공기청정기": (40, 100, 12, 30, 1.0),
    "제습기": (250, 60, 4, 30, 1.0),
    "삼성 비스포크 AI 하이브리드 냉장고 RM80F91H1X (월 43.2kWh)": (150, 40, 24, 30, 0.0),
    "LG 디오스 AI 오브제컬렉션 STEM T876MEE141 (월 43.3kWh)": (150.3, 40, 24, 30, 0.0),
    "LG 디오스 860L 기본형 T876MEE041 (월 43.3kWh)": (150.3, 40, 24, 30, 0.0),
    "삼성 무풍 콤보 갤러리 에어컨 AF80F18D28WRS (18+6평, 1.6kW)": (1600, 60, 5, 30, 4.0),
    "삼성 무풍 콤보 에어컨 AF20DX936WFRS (20+6평, 1.9kW)": (1900, 60, 5, 30, 4.0),
    "삼성 무풍 콤보 에어컨 AF80F25D29WRS (25+6평, 2.3kW)": (2300, 60, 5, 30, 4.0),
    "LG 트롬 세탁기 F24WDWP (24kg, 2,200W)": (2200, 100, 1, 12, 0.8),
    "삼성 그랑데 세탁기 WF21T6000KW (21kg, 2,200W)": (2200, 100, 1, 12, 0.8),
    "삼성 그랑데 AI 세탁기 WF80H21BDW (21kg, 2,200W)": (2200, 100, 1, 12, 1.5),
}

SAMPLE = [
    "원룸 벽걸이 에어컨 (6평형)",
    "냉장고 (소형 2도어)",
    "데스크톱 PC",
    "전자레인지",
    "와이파이 공유기",
    "TV & 셋톱박스",
]


def preset_row(name: str) -> dict:
    p, duty, hours, days, sb = PRESETS.get(name, (0, 100, 0, 30, 0))
    return {
        "기기명": name,
        "소비전력(W)": float(p),
        "가동률(%)": float(duty),
        "하루 사용(h)": float(hours),
        "월 사용일수": float(days),
        "대기전력(W)": float(sb),
        "대기차단": False,
    }


def sample_df() -> pd.DataFrame:
    return pd.DataFrame([preset_row(n) for n in SAMPLE])[APPLIANCE_COLS]


def empty_df() -> pd.DataFrame:
    return pd.DataFrame({
        c: pd.Series(dtype="object" if c == "기기명" else ("bool" if c == "대기차단" else "float"))
        for c in APPLIANCE_COLS
    })


if "base_df" not in st.session_state:
    st.session_state.base_df = sample_df()
    st.session_state.editor_ver = 0


def replace_table(new_df: pd.DataFrame) -> None:
    st.session_state.base_df = new_df
    st.session_state.editor_ver += 1


# =========================================================
# 3. 헤더 & 탭 구성
# =========================================================
st.markdown(
    '<p class="hero-title">⚡ 전력질주 — 스마트 에너지 & 역률 종합 시뮬레이터</p>'
    '<p class="hero-sub">1인 가구·자취생 맞춤형 전기요금/대기전력 분석부터 상업·기업용 역률 개선 시뮬레이션까지 한눈에 확인하세요.</p>',
    unsafe_allow_html=True,
)

tab_home, tab_reverse, tab_pf, tab_help = st.tabs(
    [
        "🏠 [가정용] 우리집 가전 & 요금",
        "🔄 [가정용] 요금으로 누진단계 역산",
        "🏢 [상업/기업용] 역률 개선 시뮬레이터",
        "📘 계산 기준 · 출처",
    ]
)

# =========================================================
# TAB 1. [가정용] 우리집 전기요금
# =========================================================
with tab_home:
    st.info(
        "💡 **가정용 모드 (1인 가구·자취생 기본 세팅):** 주택용 전력은 역률 요금 가감제가 적용되지 않으므로, "
        "**누진세 구간 관리**와 **대기전력 차단**이 실질적인 요금 절감의 핵심입니다."
    )

    with st.container(border=True):
        c1, c2, c3, c4, c5, c6 = st.columns([1.4, 1.5, 0.8, 1.0, 1.0, 1.2])

        voltage_keys = list(RATES)
        voltage = c1.selectbox(
            "계약 종별 (전압)",
            voltage_keys,
            index=0,
            format_func=lambda k: RATES[k]["label"],
            help="단독·다세대·원룸은 주로 '저압', 아파트는 주로 '고압' 요금이 적용됩니다.",
        )

        season_keys = list(SEASONS)
        curr_mon = datetime.now().month
        default_idx = 2 if curr_mon in (7, 8) else (1 if curr_mon in (12, 1, 2) else 0)
        season = c2.selectbox(
            "적용 시기",
            season_keys,
            index=default_idx,
            format_func=lambda k: SEASONS[k]["label"],
            help="하계(7~8월)는 누진 구간이 300·450kWh로 넓어지며, 하계·동계 1,000kWh 초과 시 슈퍼유저 요금이 부과됩니다.",
        )
        days = int(c3.number_input("청구 일수", min_value=28, max_value=31, value=30, step=1))
        budget = int(
            c4.number_input("목표 예산 (원)", min_value=5000, max_value=500000, value=30000, step=5000)
        )
        last_year_kwh = float(
            c5.number_input(
                "작년 동월 사용량 (kWh)",
                min_value=0.0,
                max_value=2000.0,
                value=210.0,
                step=10.0,
                help="작년 같은 달 고지서의 사용량(kWh)을 입력하면 이번 달과 비교 및 에너지캐시백을 계산합니다.",
            )
        )
        block_all = c6.toggle(
            "🔌 대기전력 전면 차단",
            value=False,
            help="외출 시 플러그를 뽑거나 멀티탭 스위치를 꺼서 모든 기기의 대기전력을 100% 차단한다고 가정합니다.",
        )

    summary = st.container()

    st.markdown("#### 🛒 우리집 가전 목록 (1인 가구 기본 세팅)")
    st.caption(
        "표를 직접 클릭해 수정할 수 있습니다. 맨 아래 빈 행에 새 가전을 입력하고, 삭제하려면 행 왼쪽을 체크한 뒤 Delete 키를 누르세요. "
        "‘가동률’은 에어컨·냉장고처럼 컴프레서가 켜졌다 꺼지는 기기만 낮추어 설정합니다."
    )
    edited = st.data_editor(
        st.session_state.base_df,
        key=f"editor_{st.session_state.editor_ver}",
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "기기명": st.column_config.TextColumn("기기명"),
            "소비전력(W)": st.column_config.NumberColumn(
                "소비전력(W)", min_value=0, max_value=20000, step=10, format="%g"
            ),
            "가동률(%)": st.column_config.NumberColumn(
                "가동률(%)", min_value=0, max_value=100, step=5, format="%g",
                help="사용 시간 중 실제로 전력을 최대 소비하는 비율(컴프레서 가동 비율 등). 일반 기기는 100",
            ),
            "하루 사용(h)": st.column_config.NumberColumn(
                "하루 사용(h)", min_value=0, max_value=24, step=0.5, format="%g"
            ),
            "월 사용일수": st.column_config.NumberColumn(
                "월 사용일수", min_value=0, max_value=31, step=1, format="%g",
                help="세탁기·청소기처럼 매일 쓰지 않는 기기는 한 달간 실제 사용한 일수를 입력하세요.",
            ),
            "대기전력(W)": st.column_config.NumberColumn(
                "대기전력(W)", min_value=0, max_value=500, step=0.5, format="%g"
            ),
            "대기차단": st.column_config.CheckboxColumn(
                "대기전력 차단", help="체크하면 해당 기기의 플러그를 뽑아 대기전력을 차단한다고 가정합니다."
            ),
        },
        **_stretch_kw(st.data_editor),
    )
    df = clean_df(edited)

    with st.expander("➕ 자주 쓰는 가전 & 실측 조사 모델 추가 · 💾 CSV 저장/불러오기"):
        a1, a2 = st.columns([3, 1])
        pick = a1.selectbox("가전 프리셋 선택", list(PRESETS), label_visibility="collapsed")
        if a2.button("목록에 추가", **_stretch_kw(st.button)):
            replace_table(
                pd.concat([df, pd.DataFrame([preset_row(pick)])], ignore_index=True)[APPLIANCE_COLS]
            )
            st.rerun()
        st.caption(
            "💡 올해 판매량 상위 가전(삼성 비스포크·무풍 에어컨·그랑데, LG 디오스·트롬) 실측 데이터와 1인 가구 대표 가전이 포함되어 있습니다. "
            "세탁기 등 스마트 가전은 Wi-Fi 원격 제어(네트워크 대기) 활성화 시 대기전력이 1~3W 수준으로 증가할 수 있습니다."
        )

        b1, b2, b3 = st.columns(3)
        b1.download_button(
            "📥 CSV로 목록 저장",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name="my_appliances.csv",
            mime="text/csv",
            help="현재 입력한 가전 목록을 CSV 파일로 저장해 두었다가 다음에 다시 불러올 수 있습니다.",
            **_stretch_kw(st.download_button),
        )
        if b2.button("🔄 1인가구 기본값으로 초기화", **_stretch_kw(st.button)):
            replace_table(sample_df())
            st.rerun()
        if b3.button("🗑️ 전체 비우기", **_stretch_kw(st.button)):
            replace_table(empty_df())
            st.rerun()

        up = st.file_uploader("저장해 둔 CSV 파일 불러오기", type="csv")
        if up is not None:
            sig = f"{up.name}:{up.size}"
            if st.session_state.get("last_upload") != sig:
                loaded = None
                try:
                    loaded = pd.read_csv(up)
                except Exception:
                    st.error("CSV를 읽지 못했습니다. ‘CSV로 목록 저장’으로 내려받은 파일인지 확인해 주세요.")
                if loaded is not None:
                    if "기기명" not in loaded.columns or "소비전력(W)" not in loaded.columns:
                        st.error("‘기기명’, ‘소비전력(W)’ 열이 포함된 파일이어야 합니다.")
                    else:
                        st.session_state.last_upload = sig
                        replace_table(clean_df(loaded))
                        st.rerun()

    if df.empty:
        with summary:
            st.info("등록된 가전이 없습니다. 아래 표에 직접 입력하거나 ‘자주 쓰는 가전 추가’에서 기기를 추가해 보세요.")
    else:
        d = analyze_appliances(df, days, block_all, season, voltage)
        total_kwh = float(d["월 사용량(kWh)"].sum())
        use_kwh = float(d["사용 kWh"].sum())
        standby_kwh = float(d["대기 kWh"].sum())
        raw_standby_kwh = float(d["대기 kWh(전체)"].sum())

        bill = calculate_bill(total_kwh, season, voltage)
        l1, l2 = SEASONS[season]["limits"]
        tier = bill["tier"]
        is_super = bill["is_super"]

        # 대기전력 비용 및 전체 차단 시 절감 가능액 계산
        standby_cost = bill["total"] - bill_total(use_kwh, season, voltage)
        unblocked_bill = bill_total(use_kwh + raw_standby_kwh, season, voltage)
        potential_standby_save = unblocked_bill - bill_total(use_kwh, season, voltage)
        diff = bill["total"] - budget

        # 평균 및 작년 비교 데이터 계산
        avg_1p_kwh = SEASONS[season]["avg_1p"]
        avg_all_kwh = SEASONS[season]["avg_all"]
        last_year_bill = bill_total(last_year_kwh, season, voltage)
        avg_1p_bill = bill_total(avg_1p_kwh, season, voltage)
        avg_all_bill = bill_total(avg_all_kwh, season, voltage)
        cashback_won, save_rate_yoy = calc_energy_cashback(last_year_kwh, total_kwh)

        with summary:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(
                "예상 전기요금",
                won(bill["total"]),
                delta=f"{diff:+,}원 (예산 대비)",
                delta_color="inverse",
            )
            m2.metric(
                "월 사용량",
                f"{total_kwh:,.1f} kWh",
                delta=f"{total_kwh - last_year_kwh:+,.1f} kWh (작년 동월 대비)",
                delta_color="inverse",
            )

            if is_super:
                m3.metric("누진 단계", "3단계 (슈퍼유저)", delta="1,000kWh 초과 특별할증", delta_color="off")
            elif tier < 3:
                nxt = (l1, l2)[tier - 1]
                m3.metric(
                    "누진 단계",
                    f"{tier}단계",
                    delta=f"다음 단계까지 {nxt - total_kwh:,.1f}kWh 여유",
                    delta_color="off",
                )
            else:
                if SEASONS[season]["super"]:
                    m3.metric(
                        "누진 단계",
                        "3단계",
                        delta=f"슈퍼유저(1,000)까지 {1000 - total_kwh:,.0f}kWh",
                        delta_color="off",
                    )
                else:
                    m3.metric("누진 단계", "3단계", delta="최고 누진 단계", delta_color="off")

            if block_all:
                m4.metric(
                    "대기전력 차단 절감액",
                    f"-{won(potential_standby_save)}/월",
                    delta=f"연 {potential_standby_save * 12:,}원 절약",
                    delta_color="normal",
                )
            else:
                m4.metric(
                    "현재 대기전력 요금",
                    f"{won(standby_cost)}/월",
                    delta=f"연 {standby_cost * 12:,}원 낭비 중",
                    delta_color="off",
                )

            if diff > 0:
                st.error(f"🚨 목표 예산 {budget:,}원보다 **{diff:,}원** 초과할 것으로 예상됩니다.")
            else:
                st.success(f"🟢 목표 예산 {budget:,}원 이내입니다! (**{-diff:,}원** 여유)")

            # 누진 구간 가로 게이지
            gauge = go.Figure()
            xmax = max(l2 * 1.35, total_kwh * 1.1)
            for x0, x1, color, name in [
                (0, l1, "#22c55e", f"1단계 (~{l1}kWh)"),
                (l1, l2, "#f59e0b", f"2단계 ({l1+1}~{l2}kWh)"),
                (l2, xmax, "#ef4444", f"3단계 ({l2}kWh 초과)"),
            ]:
                gauge.add_trace(
                    go.Bar(
                        x=[x1 - x0],
                        y=[""],
                        base=[x0],
                        orientation="h",
                        marker_color=color,
                        text=[name],
                        textposition="inside",
                        insidetextanchor="middle",
                        hovertemplate=f"{name}<extra></extra>",
                        showlegend=False,
                    )
                )
            gauge.add_shape(
                type="line", x0=total_kwh, x1=total_kwh, y0=-0.5, y1=0.5,
                line=dict(color="#0f172a", width=4)
            )
            gauge.add_annotation(
                x=total_kwh, y=0.5, text=f"현재 {total_kwh:,.1f}kWh", showarrow=False, yshift=12
            )
            gauge.update_layout(
                barmode="overlay",
                height=105,
                margin=dict(l=0, r=0, t=26, b=0),
                xaxis=dict(range=[0, xmax], title=None),
                yaxis=dict(visible=False),
            )
            show_chart(gauge)

        # ---- 기기별 분석 + 원그래프 + 누진세 곡선 + 절감 가이드 ----
        st.divider()
        left, right = st.columns([1.28, 1], gap="large")

        with left:
            st.markdown("##### 📊 기기별 전력 사용량 및 비중 분석")
            c_chart1, c_chart2 = st.columns(2)
            s = d.sort_values("월 사용량(kWh)")

            with c_chart1:
                st.markdown("**1) 전력 사용 비중 (원그래프)**")
                pie = go.Figure(
                    go.Pie(
                        labels=s["기기명"],
                        values=s["월 사용량(kWh)"],
                        hole=0.42,
                        textinfo="percent+label",
                    )
                )
                pie.update_layout(
                    height=300,
                    margin=dict(l=10, r=10, t=15, b=25),
                    showlegend=False,
                )
                show_chart(pie)

            with c_chart2:
                st.markdown("**2) 기기별 사용 vs 대기전력 (kWh)**")
                bar = go.Figure()
                bar.add_bar(
                    y=s["기기명"], x=s["사용 kWh"], orientation="h", name="사용 전력", marker_color="#2563eb"
                )
                bar.add_bar(
                    y=s["기기명"], x=s["대기 kWh"], orientation="h", name="대기 전력", marker_color="#f59e0b"
                )
                bar.update_layout(
                    barmode="stack",
                    height=300,
                    margin=dict(l=10, r=15, t=35, b=40),
                    xaxis_title="월 사용량 (kWh)",
                    legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center"),
                )
                show_chart(bar)

            st.markdown("##### 📈 누진세 위험도 곡선 (1단계 단일단가 대비 요금 급증 비교)")
            st.caption(
                "붉은 실선은 실제 누진세가 적용된 요금 곡선이며, 회색 점선은 누진제가 없을 때(1단계 단가 유지 시)의 가상 선입니다. "
                "구간이 넘어갈수록 기울기가 급격히 가팔라집니다."
            )
            show_chart(make_progressive_curve(total_kwh, season, voltage, height=370))

            st.dataframe(
                d.sort_values("끄면 절약(원/월)", ascending=False)[
                    ["기기명", "월 사용량(kWh)", "비중(%)", "끄면 절약(원/월)"]
                ],
                hide_index=True,
                column_config={
                    "월 사용량(kWh)": st.column_config.NumberColumn(format="%.1f"),
                    "비중(%)": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
                    "끄면 절약(원/월)": st.column_config.NumberColumn(
                        format="%d원", help="해당 기기를 사용하지 않을 때 줄어드는 월 전기요금(누진 단계 변동 반영)"
                    ),
                },
                **_stretch_kw(st.dataframe),
            )

        with right:
            # ---- 나의 사용량 vs 평균 가구 & 작년 동월 비교 ----
            st.markdown("##### 👥 나의 사용량 vs 평균 가구 · 작년 동월 비교")
            comp_df = pd.DataFrame({
                "구분": ["1인 가구 평균", "작년 동월 (나)", "이번 달 (나)", "전국 가구 평균"],
                "사용량(kWh)": [avg_1p_kwh, last_year_kwh, total_kwh, avg_all_kwh],
                "요금(원)": [avg_1p_bill, last_year_bill, bill["total"], avg_all_bill],
                "색상": ["#64748b", "#3b82f6", "#ef4444" if total_kwh > avg_1p_kwh else "#10b981", "#94a3b8"],
            })

            comp_fig = go.Figure()
            comp_fig.add_trace(
                go.Bar(
                    x=comp_df["구분"],
                    y=comp_df["사용량(kWh)"],
                    marker_color=comp_df["색상"],
                    text=[
                        f"{k:.0f}kWh<br>({b:,}원)"
                        for k, b in zip(comp_df["사용량(kWh)"], comp_df["요금(원)"])
                    ],
                    textposition="outside",
                    hovertemplate="%{x}<br>사용량: %{y:.1f} kWh<extra></extra>",
                )
            )
            max_comp_y = max(comp_df["사용량(kWh)"]) * 1.28 if max(comp_df["사용량(kWh)"]) > 0 else 100
            comp_fig.update_layout(
                yaxis=dict(title="월 사용량 (kWh)", range=[0, max_comp_y]),
                height=265,
                margin=dict(l=10, r=10, t=20, b=10),
                showlegend=False,
            )
            show_chart(comp_fig)

            diff_1p_kwh = total_kwh - avg_1p_kwh
            diff_yoy_kwh = total_kwh - last_year_kwh
            diff_yoy_bill = bill["total"] - last_year_bill

            cmp1, cmp2 = st.columns(2)
            cmp1.metric(
                "1인 가구 평균 대비",
                f"{diff_1p_kwh:+,.1f} kWh",
                delta=f"요금 {bill['total'] - avg_1p_bill:+,}원 차이",
                delta_color="inverse",
            )
            cmp2.metric(
                "작년 동월 대비",
                f"{diff_yoy_kwh:+,.1f} kWh",
                delta=f"요금 {diff_yoy_bill:+,}원 ({-save_rate_yoy:+.1f}%)" if last_year_kwh > 0 else "작년 데이터 없음",
                delta_color="inverse",
            )

            if cashback_won > 0:
                st.success(
                    f"💰 **한전 에너지캐시백 달성 예상!** 작년 동월 대비 **{save_rate_yoy:.1f}%({last_year_kwh - total_kwh:.1f}kWh)** 절감하여 "
                    f"다음 달 요금에서 약 **{cashback_won:,}원** 추가 캐시백 환급을 받을 수 있습니다."
                )

            st.markdown("---")
            st.markdown("##### 💡 이렇게 줄여 보세요 (맞춤 절감 가이드)")
            st.caption("※ 냉장고·와이파이 공유기 등 24시간 상시 구동이 필요한 기기는 시간 단축 및 차단 추천에서 자동 제외됩니다.")

            tips = []
            # 24시간 필수 가동 기기 제외한 데이터프레임
            adjustable_df = d[~d["상시가동"]].copy()

            standby_rank = (
                adjustable_df[(adjustable_df["대기 kWh"] > 0)]
                .sort_values("대기 kWh", ascending=False)
                .head(3)
            )
            if standby_kwh > 0 and not standby_rank.empty:
                names = ", ".join(f"{r['기기명']}({r['대기 kWh']:.1f}kWh)" for _, r in standby_rank.iterrows())
                tips.append(
                    f"**대기전력**으로 매달 **{standby_kwh:.1f}kWh**, 약 **{standby_cost:,}원**이 새고 있습니다 (연간 **{standby_cost * 12:,}원**). "
                    f"개별 스위치 멀티탭이나 스마트 플러그로 **{names}**부터 차단해 보세요."
                )
            else:
                tips.append(
                    f"대기전력을 모두 차단하여 매달 **{raw_standby_kwh:.1f}kWh**(약 **{potential_standby_save:,}원**)를 절약하고 있습니다! 👍"
                )

            hour_tips = []
            for _, r in adjustable_df.sort_values("끄면 절약(원/월)", ascending=False).iterrows():
                kph = kwh_per_hour(r, days, block_all)
                if r["하루 사용(h)"] >= 1 and kph > 0:
                    save = bill["total"] - bill_total(total_kwh - kph, season, voltage)
                    hour_tips.append((r["기기명"], save))
                if len(hour_tips) == 3:
                    break
            for name, save in hour_tips:
                tips.append(f"**{name}** 하루 1시간만 덜 써도 월 약 **{save:,}원**(연 **{save * 12:,}원**)이 절약됩니다.")

            if tier < 3:
                nxt = (l1, l2)[tier - 1]
                remain = nxt - total_kwh
                if remain < nxt * 0.25:
                    tips.append(
                        f"⚠️ **{tier + 1}단계 진입까지 {remain:.1f}kWh** 남았습니다. 구간을 넘으면 단가가 "
                        f"**{RATES[voltage]['energy'][tier]:g}원/kWh**로 뛰고 기본요금도 **{RATES[voltage]['base'][tier]:,}원**으로 인상됩니다."
                    )
            else:
                tips.append(
                    f"🔥 최고 단가 구간(3단계, **{bill['applied_rate']:g}원/kWh**)입니다. 소비전력이 큰 냉난방·전열 기기부터 사용 시간을 줄이면 요금 절감 폭이 매우 큽니다."
                )

            for t in tips:
                st.markdown(f"- {t}")

            # 커피/치킨 환산 절감 가이드
            annual_standby_save = potential_standby_save * 12
            top_hour_save = (hour_tips[0][1] * 12) if hour_tips else 0
            total_annual_save = annual_standby_save + top_hour_save
            coffee_cnt = total_annual_save / COFFEE_PRICE
            chicken_cnt = total_annual_save / CHICKEN_PRICE

            st.markdown(
                f"""
                <div class="conv-box">
                    <b>☕🍗 커피 · 치킨 환산 절감 가이드 (연간 기준)</b><br/>
                    • 대기전력 전면 차단 시: 연 <b>{annual_standby_save:,}원</b> 절약
                    (☕ 아메리카노 <b>{annual_standby_save / COFFEE_PRICE:.1f}잔</b>)<br/>
                    • 대기전력 차단 + <b>{hour_tips[0][0] if hour_tips else '주요 기기'}</b> 하루 1시간 단축 시:
                    연 <b>{total_annual_save:,}원</b> 절약!<br/>
                    👉 1년이면 <b>아메리카노 약 {coffee_cnt:.1f}잔</b> 또는 <b>치킨 약 {chicken_cnt:.1f}마리</b> 값을 아낄 수 있습니다!
                </div>
                """,
                unsafe_allow_html=True,
            )

            with st.expander("🧾 이번 달 예상 요금 상세 내역"):
                parts = pd.DataFrame({
                    "항목": [
                        "기본요금",
                        "전력량요금",
                        "기후환경요금 (9원/kWh)",
                        "연료비조정액 (+5원/kWh)",
                        "부가가치세 (10%)",
                        "전력산업기반기금 (3.7%)",
                        "최종 청구 합계 (10원 미만 절사)",
                    ],
                    "금액(원)": [
                        bill["base"], bill["energy"], bill["climate"],
                        bill["fuel"], bill["vat"], bill["fund"], bill["total"],
                    ],
                })
                st.dataframe(
                    parts,
                    hide_index=True,
                    column_config={"금액(원)": st.column_config.NumberColumn(format="%d원")},
                    **_stretch_kw(st.dataframe),
                )

            st.markdown("#### 🎯 목표 예산 맞추기 시뮬레이션")
            max_kwh = max_kwh_for_budget(budget, season, voltage)
            gap = total_kwh - max_kwh

            # 24시간 구동 기기를 제외한 조절 가능 기기 목록
            adj_names = adjustable_df["기기명"].tolist()
            if not adj_names:
                st.info("사용 시간을 조절할 수 있는 일반 가전이 없습니다 (24시간 상시 가동 기기는 제외됨).")
            else:
                pick_dev = st.selectbox("사용 시간을 조절해 볼 기기 선택 (24시간 상시 가동 기기 제외)", adj_names, key="budget_dev")
                row = d[d["기기명"] == pick_dev].iloc[0]
                kph = kwh_per_hour(row, days, block_all)
                cur_h = float(row["하루 사용(h)"])

                if gap <= 0:
                    st.success(f"목표 예산 {budget:,}원 기준으로 한 달에 약 **{-gap:,.1f}kWh**를 더 사용할 수 있습니다.")
                    if kph > 0:
                        new_h = min(24.0, cur_h + (-gap) / kph)
                        st.write(
                            f"→ **{pick_dev}**을(를) 하루 최대 **{new_h:.1f}시간**까지 사용해도 예산 이내입니다 (현재 하루 {cur_h:g}시간)."
                        )
                else:
                    st.warning(f"목표 예산 {budget:,}원에 맞추려면 한 달 사용량에서 **{gap:,.1f}kWh**를 줄여야 합니다.")
                    if kph > 0:
                        new_h = cur_h - gap / kph
                        if new_h >= 0:
                            st.write(
                                f"→ **{pick_dev}** 사용 시간을 하루 **{cur_h:g}시간 → {new_h:.1f}시간**으로 줄이면 목표 예산에 들어옵니다!"
                            )
                        else:
                            st.write(
                                f"→ **{pick_dev}** 하나만 줄여서는 부족합니다. 소비전력이 큰 다른 기기도 함께 줄여 보세요."
                            )


# =========================================================
# TAB 2. [가정용] 요금으로 누진단계 자동 역산 탭
# =========================================================
with tab_reverse:
    st.markdown("### 🔄 전기요금 → 사용량(kWh) & 누진단계 자동 역산기")
    st.caption(
        "고지서나 관리비 내역서의 전기요금 청구액(원)만 알고 있을 때, 이분 탐색(Binary Search) 알고리즘으로 "
        "이번 달 전력 사용량(kWh)과 적용된 누진 단계를 정확하게 역산합니다."
    )

    rev_col1, rev_col2 = st.columns([1, 1.5], gap="large")
    with rev_col1:
        with st.container(border=True):
            st.markdown("#### 1️⃣ 고지서 요금 정보 입력")
            target_bill = st.number_input(
                "청구된 전기요금 (원)",
                min_value=1000,
                max_value=2000000,
                value=35000,
                step=1000,
                help="고지서에 찍힌 최종 청구 금액(원)을 입력하세요.",
            )
            voltage_rev = st.selectbox(
                "계약 종별 (전압)",
                list(RATES),
                index=0,
                format_func=lambda k: RATES[k]["label"],
                key="rev_volt",
            )
            rev_season = st.selectbox(
                "요금 적용 시기",
                list(SEASONS),
                format_func=lambda k: SEASONS[k]["label"],
                key="rev_season",
            )

        est_kwh = max_kwh_for_budget(target_bill, rev_season, voltage_rev)
        est_data = calculate_bill(est_kwh, rev_season, voltage_rev)
        l1_r, l2_r = SEASONS[rev_season]["limits"]

        st.markdown("#### 2️⃣ 추정 요금 상세 구성표")
        rev_parts = pd.DataFrame({
            "요금 항목": [
                "기본요금",
                "전력량요금",
                "기후환경요금",
                "연료비조정액",
                "부가가치세 (10%)",
                "전력산업기반기금 (3.7%)",
                "역산 청구 합계",
            ],
            "추정 금액(원)": [
                est_data["base"], est_data["energy"], est_data["climate"],
                est_data["fuel"], est_data["vat"], est_data["fund"], est_data["total"],
            ],
        })
        st.dataframe(
            rev_parts,
            hide_index=True,
            column_config={"추정 금액(원)": st.column_config.NumberColumn(format="%d원")},
            **_stretch_kw(st.dataframe),
        )

    with rev_col2:
        st.markdown("#### 🧮 역산 분석 결과")
        rm1, rm2, rm3 = st.columns(3)
        rm1.metric("추정 월 사용량", f"{est_kwh:,.1f} kWh")

        tier_r = est_data["tier"]
        is_super_r = est_data["is_super"]
        applied_rate_r = est_data["applied_rate"]

        if is_super_r:
            tier_str = "🔥 3단계 (슈퍼유저)"
            remain_str = "1,000kWh 초과 최고 할증"
        elif tier_r == 1:
            tier_str = "1단계 (🟢 안전)"
            remain_str = f"2단계까지 {l1_r - est_kwh:,.1f}kWh 여유"
        elif tier_r == 2:
            tier_str = "2단계 (🟡 주의)"
            remain_str = f"3단계까지 {l2_r - est_kwh:,.1f}kWh 여유"
        else:
            tier_str = "3단계 (🔴 누진폭탄)"
            remain_str = (
                f"슈퍼유저까지 {1000 - est_kwh:,.1f}kWh"
                if SEASONS[rev_season]["super"]
                else "최고 누진 구간 적용 중"
            )

        rm2.metric("적용 누진 단계", tier_str, delta=remain_str, delta_color="off")
        rm3.metric("최종 구간 단가", f"{applied_rate_r:g} 원/kWh", delta=f"기본요금 {est_data['base']:,}원", delta_color="off")

        st.info(
            f"입력하신 **{target_bill:,}원**을 기준으로 역산한 결과, 이번 달 전력 사용량은 약 **{est_kwh:,.1f} kWh**로 추정됩니다.\n\n"
            f"해당 사용량은 **{RATES[voltage_rev]['label']}** 기준 **{tier_str}** 구간에 속하며, "
            f"적용된 최고 구간 전력량 요금 단가는 **{applied_rate_r:g}원/kWh**입니다."
        )

        show_chart(make_progressive_curve(est_kwh, rev_season, voltage_rev, height=360))


# =========================================================
# TAB 3. [상업/기업용] 역률 개선 시뮬레이터
# =========================================================
with tab_pf:
    st.info(
        "🏢 **상업/기업용 모드:** 한전의 역률 요금 가감 제도는 계약전력 **20kW 이상 일반용·산업용·교육용** 고객 등에게 적용됩니다. "
        "부하 시나리오를 선택하거나 설비 조건을 입력하면 필요 콘덴서 용량($Q_c$)과 전기요금·전력손실 절감 효과를 즉시 시뮬레이션합니다."
    )

    scenario_name = st.selectbox(
        "🏭 가상 부하 시나리오 프리셋 선택 (선택 시 아래 기본값이 자동 설정됩니다)",
        list(LOAD_SCENARIOS),
        index=0,
    )
    scen = LOAD_SCENARIOS[scenario_name]

    col_in, col_out = st.columns([1, 1.5], gap="large")

    with col_in:
        st.subheader("⚙️ 부하 조건 및 계약 정보")
        supply_list = ["3상 380V", "3상 220V", "단상 220V"]
        supply = st.selectbox(
            "공급 방식",
            supply_list,
            index=supply_list.index(scen["supply"]),
            key=f"sup_{scenario_name}",
        )
        phases, v_v = {"3상 380V": (3, 380.0), "3상 220V": (3, 220.0), "단상 220V": (1, 220.0)}[supply]
        p_kw = st.number_input(
            "유효전력 P (kW)",
            min_value=1.0,
            value=float(scen["p_kw"]),
            step=10.0,
            key=f"pkw_{scenario_name}",
            help="실제 부하가 소비하여 일을 하는 유효전력입니다.",
        )
        pf1_pct = st.slider(
            "현재 역률 (%)", 50, 98, int(scen["pf1"]), key=f"pf1_{scenario_name}"
        )
        pf2_pct = st.slider(
            "목표 역률 (%)",
            min_value=pf1_pct + 1,
            max_value=100,
            value=min(max(int(scen["pf2"]), pf1_pct + 1), 100),
            key=f"pf2_{scenario_name}",
        )
        pf1, pf2 = pf1_pct / 100.0, pf2_pct / 100.0

        st.markdown("**한전 기본요금 및 설비 단가 설정**")
        contract_kw = st.number_input(
            "계약전력 (kW)",
            min_value=1.0,
            value=float(p_kw),
            step=10.0,
            key=f"ckw_{scenario_name}",
        )
        base_rate = st.number_input(
            "기본요금 단가 (원/kW)",
            min_value=1000,
            value=8310,
            step=100,
            help="일반용/산업용 계약종별 기본요금 단가 예시입니다.",
        )
        step_pct = st.number_input(
            "역률 1%당 기본요금 가감률 (%)",
            min_value=0.1,
            max_value=2.0,
            value=0.2,
            step=0.1,
            help="현행 한전 전기공급약관 제43조 기준은 지상역률 90% 기준 1%당 0.2% 가감(60~95% 구간)입니다.",
        )
        cap_unit = st.number_input(
            "콘덴서 설치 단가 (원/kVAR)",
            min_value=0,
            value=40000,
            step=5000,
            help="설치비 투자 회수기간(Payback) 산정용 단가입니다.",
        )

    # ---- 역률 계산 ----
    th1, th2 = math.acos(pf1), math.acos(pf2)
    q_req = p_kw * (math.tan(th1) - math.tan(th2))
    q1 = p_kw * math.tan(th1)
    q2 = p_kw * math.tan(th2)
    s1, s2 = p_kw / pf1, p_kw / pf2
    k_phase = math.sqrt(3) if phases == 3 else 1.0
    i1 = p_kw * 1000.0 / (k_phase * v_v * pf1)
    i2 = p_kw * 1000.0 / (k_phase * v_v * pf2)
    i_red = (i1 - i2) / i1 * 100.0
    loss_red = (1.0 - (pf1 / pf2) ** 2) * 100.0

    step = step_pct / 100.0
    base_fee = contract_kw * base_rate
    cost1 = base_fee * (1.0 + pf_fee_rate(pf1_pct, step))
    cost2 = base_fee * (1.0 + pf_fee_rate(pf2_pct, step))
    monthly_save = cost1 - cost2

    # 표준 콘덴서 규격 후보
    ceil_c = next((c for c in STD_CAPACITORS if c >= q_req), STD_CAPACITORS[-1])
    floor_c = max([c for c in STD_CAPACITORS if c < q_req], default=0)
    cand = []
    for label, qc in [("권장 규격 (올림)", ceil_c), ("한 단계 아래 규격", floor_c)]:
        if qc <= 0:
            continue
        pf_res, leading = pf_after_capacitor(p_kw, pf1, qc)
        cand.append({
            "구분": label,
            "표준 용량(kVAR)": qc,
            "설치 후 역률(%)": round(pf_res * 100, 1),
            "위상 상태": "⚠️ 진상(과보상)" if leading else "정상(지상)",
            "예상 설치비(원)": int(qc * cap_unit),
        })
    cand_df = pd.DataFrame(cand)

    with col_in:
        st.metric(
            "필요 콘덴서 용량 Q",
            f"{q_req:,.2f} kVAR",
            help="산출 공식: Q = P × (tanθ₁ − tanθ₂)",
        )

    with col_out:
        st.subheader("📈 역률 개선 기술적 · 경제적 효과")
        r1, r2, r3 = st.columns(3)
        r1.metric("선로 전류 감소", f"-{i_red:.1f}%", delta=f"{i1:,.1f}A → {i2:,.1f}A", delta_color="off")
        r2.metric("선로 전력손실(I²R) 감소", f"-{loss_red:.1f}%")
        r3.metric("피상전력(변압기 부담) 감소", f"-{s1 - s2:,.1f} kVA", delta=f"{s1:,.1f} → {s2:,.1f} kVA", delta_color="off")

        r4, r5, r6 = st.columns(3)
        r4.metric("월 기본요금 절감액", won(monthly_save), help="역률 할증 방지 및 할인 적용 차액")
        r5.metric("연간 기본요금 절감액", won(monthly_save * 12))
        if not cand_df.empty and monthly_save > 0:
            pay = cand_df.iloc[0]["예상 설치비(원)"] / monthly_save
            r6.metric("설치비 회수기간", f"{pay:,.1f}개월" if pay < 120 else "10년 이상")
        else:
            r6.metric("설치비 회수기간", "-")

        st.markdown("**표준 진상 콘덴서(SC) 규격 선정표**")
        if cand_df.empty:
            st.info("필요 용량이 표준 최소 규격(5kVAR)보다 작습니다.")
        else:
            st.dataframe(
                cand_df,
                hide_index=True,
                column_config={"예상 설치비(원)": st.column_config.NumberColumn(format="%d원")},
                **_stretch_kw(st.dataframe),
            )
            if (cand_df["위상 상태"] != "정상(지상)").any():
                st.warning(
                    "⚠️ **진상(과보상) 주의:** 콘덴서 용량이 과대하면 야간·경부하 시 진상 역률이 발생하여 선로 전압 상승 및 "
                    "진상역률 요금 할증이 부과될 수 있습니다. 자동역률제어기(APFR)로 부하에 맞춰 뱅크를 단계 투입하는 방식을 권장합니다."
                )

        g1, g2 = st.columns(2)
        with g1:
            tri = go.Figure()
            tri.add_trace(
                go.Scatter(
                    x=[0, p_kw, p_kw, 0], y=[0, 0, q1, 0],
                    mode="lines+markers", name=f"개선 전 (Q₁={q1:.1f}kVAR)", line=dict(color="#ef4444", width=2.5)
                )
            )
            tri.add_trace(
                go.Scatter(
                    x=[0, p_kw, p_kw, 0], y=[0, 0, q2, 0],
                    mode="lines+markers", name=f"개선 후 (Q₂={q2:.1f}kVAR)", line=dict(color="#10b981", width=2.5, dash="dash")
                )
            )
            tri.update_layout(
                title="전력 삼각형 (유효전력 P · 무효전력 Q · 피상전력 S)",
                height=300,
                margin=dict(l=10, r=10, t=40, b=10),
                xaxis_title="유효전력 P (kW)",
                yaxis=dict(title="무효전력 Q (kVAR)", scaleanchor="x"),
                legend=dict(orientation="h", y=-0.28),
            )
            show_chart(tri)

        with g2:
            xs = np.arange(60, 101, 1)
            ys = [base_fee * (1.0 + pf_fee_rate(float(x), step)) for x in xs]
            curve = go.Figure()
            curve.add_trace(
                go.Scatter(x=xs, y=ys, mode="lines", name="월 기본요금", line=dict(color="#2563eb", width=2.5))
            )
            curve.add_vline(x=90, line_dash="dot", line_color="#64748b", annotation_text="기준역률 90%")
            curve.add_trace(
                go.Scatter(
                    x=[pf1_pct, pf2_pct],
                    y=[cost1, cost2],
                    mode="markers+text",
                    text=[f"현재({pf1_pct}%)", f"목표({pf2_pct}%)"],
                    textposition="top center",
                    marker=dict(size=11, color=["#ef4444", "#10b981"]),
                    showlegend=False,
                )
            )
            curve.update_layout(
                title="역률 변화에 따른 월 기본요금 추이",
                height=300,
                margin=dict(l=10, r=10, t=40, b=10),
                xaxis_title="역률 (%)",
                yaxis_title="월 기본요금 (원)",
                showlegend=False,
            )
            show_chart(curve)

        st.markdown(
            f"""
            **💡 역률 개선을 통해 얻을 수 있는 3대 핵심 효과**
            1. **전기요금 직접 절감:** 지상역률 90% 미달 시 부과되는 할증 페널티를 없애고, 90% 초과(최대 95%) 달성 시 기본요금 감면 혜택을 받습니다.
            2. **선로 전력손실($I^2R$) 감소:** 동일한 유효전력($P$)을 공급할 때 무효전류가 줄어들어 배선 발열 및 선로 손실이 **{loss_red:.1f}%** 감소합니다.
            3. **전력 설비 여유 용량 확보:** 피상전력($S$)이 **{s1 - s2:,.1f} kVA** 감소하여 기존 변압기와 차단기 용량으로도 추가 설비를 안전하게 증설할 수 있습니다.
            """
        )

    # =========================================================
    # 역률 구간별 최적 효율 계산 분석
    # =========================================================
    st.divider()
    st.markdown("### 🔬 역률 구간별 최적 효율(가성비) 분석")

    eff_col1, eff_col2 = st.columns([1.35, 1], gap="large")
    eff_df = analyze_pf_interval_efficiency(p_kw, base_fee, step, cap_unit)

    with eff_col1:
        st.markdown("#### 1️⃣ 어느 구간에서 역률 개선 효율(가성비)이 가장 높을까?")
        st.caption(
            "삼각함수($\\tan\\theta$)의 비선형 특성상 구간마다 역률 1%를 올리는 데 필요한 콘덴서 용량이 다릅니다. "
            "수학적으로 $\\cos\\theta = \\sqrt{2/3} \\approx 81.6\\%$ 부근(**80% → 85% 구간**)에서 1%당 필요 용량이 최소가 되어 투자 효율이 가장 높고, "
            "**95% 초과 구간**은 한전 요금 할인이 멈추는 반면 필요 용량은 2배 이상 폭증하여 경제성이 급격히 떨어집니다."
        )
        st.dataframe(
            eff_df,
            hide_index=True,
            column_config={
                "구간 필요 용량 ΔQ (kVAR)": st.column_config.NumberColumn(format="%.2f kVAR"),
                "1%당 필요 용량 (kVAR/1%)": st.column_config.NumberColumn(format="%.2f kVAR"),
                "구간 선로손실 감소율 (%)": st.column_config.NumberColumn(format="-%.1f%%"),
                "월 기본요금 절감 (원)": st.column_config.NumberColumn(format="%d원"),
            },
            **_stretch_kw(st.dataframe),
        )

    with eff_col2:
        st.markdown("#### 📊 구간별 1% 개선당 필요 콘덴서 용량 비교")
        bar_colors = [
            "#10b981" if "80% → 85%" in x else ("#ef4444" if "95% → 100%" in x else "#3b82f6")
            for x in eff_df["역률 개선 구간"]
        ]
        eff_fig = go.Figure()
        eff_fig.add_trace(
            go.Bar(
                x=eff_df["역률 개선 구간"],
                y=eff_df["1%당 필요 용량 (kVAR/1%)"],
                marker_color=bar_colors,
                text=[f"{v:.2f}" for v in eff_df["1%당 필요 용량 (kVAR/1%)"]],
                textposition="outside",
                hovertemplate="구간: %{x}<br>1% 개선당 필요 용량: %{y:.2f} kVAR<extra></extra>",
            )
        )
        max_eff_y = max(eff_df["1%당 필요 용량 (kVAR/1%)"]) * 1.25 if not eff_df.empty else 10
        eff_fig.update_layout(
            yaxis=dict(title="1% 개선당 필요 용량 (kVAR)", range=[0, max_eff_y]),
            xaxis_title="역률 5% 개선 구간",
            height=330,
            margin=dict(l=10, r=10, t=20, b=10),
            showlegend=False,
        )
        show_chart(eff_fig)
        st.success(
            f"💡 **최적 구간 결론:** 현재 설정($P={p_kw:g}\\text{{kW}}$) 기준 **80%→85% 구간**이 1%당 "
            f"**{eff_df.iloc[4]['1%당 필요 용량 (kVAR/1%)']:.2f} kVAR**로 가장 적게 들며, "
            f"한전 요금 할인 상한선인 **95%**까지 개선하는 것이 종합 최적점(Sweet Spot)입니다."
        )


# =========================================================
# TAB 4. 계산 기준 · 출처
# =========================================================
with tab_help:
    st.markdown("### 📘 한국전력 주택용 전력 요금표 및 계산 기준")

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        st.markdown("#### 1. 주택용전력(저압, 기타계절)")
        st.dataframe(
            pd.DataFrame({
                "기본요금 구간": ["200kWh 이하 사용", "201~400kWh 사용", "400kWh 초과 사용"],
                "기본요금(원/호)": ["910", "1,600", "7,300"],
                "전력량요금 구간": ["처음 200kWh까지", "다음 200kWh까지", "400kWh 초과"],
                "전력량요금(원/kWh)": ["120.0", "214.6", "307.3"],
            }),
            hide_index=True,
            **_stretch_kw(st.dataframe),
        )
        st.caption(
            "1. 기타계절 : 1월 1일~6월 30일, 9월 1일~12월 31일\n"
            "2. 슈퍼유저요금 : 동계(12월 1일~2월 말일) 1,000kWh 초과 전력량요금은 736.2원/kWh 적용"
        )

        st.markdown("#### 2. 주택용전력(저압, 하계)")
        st.dataframe(
            pd.DataFrame({
                "기본요금 구간": ["300kWh 이하 사용", "301~450kWh 사용", "450kWh 초과 사용"],
                "기본요금(원/호)": ["910", "1,600", "7,300"],
                "전력량요금 구간": ["처음 300kWh까지", "다음 150kWh까지", "450kWh 초과"],
                "전력량요금(원/kWh)": ["120.0", "214.6", "307.3"],
            }),
            hide_index=True,
            **_stretch_kw(st.dataframe),
        )
        st.caption(
            "1. 하계 : 7월 1일~8월 31일\n"
            "2. 슈퍼유저요금 : 하계(7월 1일~8월 31일) 1,000kWh 초과 전력량요금은 736.2원/kWh 적용"
        )

    with col_t2:
        st.markdown("#### 3. 주택용전력(고압, 기타계절)")
        st.dataframe(
            pd.DataFrame({
                "기본요금 구간": ["200kWh 이하 사용", "201~400kWh 사용", "400kWh 초과 사용"],
                "기본요금(원/호)": ["730", "1,260", "6,060"],
                "전력량요금 구간": ["처음 200kWh까지", "다음 200kWh까지", "400kWh 초과"],
                "전력량요금(원/kWh)": ["105.0", "174.0", "242.3"],
            }),
            hide_index=True,
            **_stretch_kw(st.dataframe),
        )
        st.caption(
            "1. 기타계절 : 1월 1일~6월 30일, 9월 1일~12월 31일\n"
            "2. 슈퍼유저요금 : 동계(12월 1일~2월 말일) 1,000kWh 초과 전력량요금은 601.3원/kWh 적용"
        )

        st.markdown("#### 4. 주택용전력(고압, 하계)")
        st.dataframe(
            pd.DataFrame({
                "기본요금 구간": ["300kWh 이하 사용", "301~450kWh 사용", "450kWh 초과 사용"],
                "기본요금(원/호)": ["730", "1,260", "6,060"],
                "전력량요금 구간": ["처음 300kWh까지", "다음 150kWh까지", "450kWh 초과"],
                "전력량요금(원/kWh)": ["105.0", "174.0", "242.3"],
            }),
            hide_index=True,
            **_stretch_kw(st.dataframe),
        )
        st.caption(
            "1. 하계 : 7월 1일~8월 31일\n"
            "2. 슈퍼유저요금 : 하계(7월 1일~8월 31일) 1,000kWh 초과 전력량요금은 601.3원/kWh 적용"
        )

    st.markdown("---")
    st.markdown("**5. 기기별 월간 전력량 및 대기전력 산출 공식**")
    st.latex(
        r"E_{\mathrm{month}}\,[\mathrm{kWh}] = \frac{P \cdot d \cdot h \cdot n + P_{\mathrm{sb}} \cdot (24N - h \cdot n)}{1000}"
    )
    st.caption(
        "P: 정격 소비전력(W), d: 가동률(0~1), h: 하루 사용시간(h), n: 월 사용일수(일), P_sb: 대기전력(W), N: 월 청구일수(일)"
    )

    st.markdown("**6. 역률 개선 콘덴서 용량 및 선로 손실 공식**")
    st.latex(
        r"Q_c = P\,(\tan\theta_1 - \tan\theta_2)\,\,[\mathrm{kVAR}], \quad "
        r"I = \frac{P \times 1000}{\sqrt{3}\,V\cos\theta}\,\,[\mathrm{A}], \quad "
        r"\Delta P_{\mathrm{loss}}\,[\%] = \left(1 - \left(\frac{\cos\theta_1}{\cos\theta_2}\right)^2\right) \times 100"
    )

    st.markdown("**7. 참고 자료 및 데이터 출처**")
    st.markdown(
        """
        - [한국전력공사 사이버지점 — 전기요금표 및 전기공급약관](https://cyber.kepco.co.kr)
        - [한국에너지공단 효율관리제도 — 가전제품 소비전력 및 대기전력 DB](https://www.energy.or.kr)
        - [공공데이터포털 — 전력 및 에너지 통계 데이터](https://www.data.go.kr)
        """
    )

st.divider()
st.caption("팀 전력질주 (1학년팀) — 본 시뮬레이터의 계산 결과는 참고용 추정값입니다.")