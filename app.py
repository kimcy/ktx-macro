"""KTX 매크로 Streamlit UI. 왕복(가는 편 / 오는 편)을 한 번에 조회·예약한다.

실행: streamlit run app.py
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from datetime import date, datetime, time as dtime, timedelta

import streamlit as st
from dotenv import load_dotenv

load_dotenv(interpolate=False)

from korail_mobile_api import KorailPassengerCounts  # noqa: E402

import ktx_macro  # noqa: E402
from ktx_macro import KTXMacro, Leg, fetch_station_names, format_passengers  # noqa: E402

st.set_page_config(page_title="KTX 매크로", page_icon="🚄", layout="wide")


# ---------------------------------------------------------------------------
# 백그라운드 실행 상태
# ---------------------------------------------------------------------------
class RunState:
    def __init__(self) -> None:
        self.logs: deque[str] = deque(maxlen=800)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "idle"  # idle / running / done / partial / stopped / error / no_result
        self.legs: list[Leg] = []
        self.error: str | None = None
        self.started_at: datetime | None = None
        self.summary: list[str] = []

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class DequeHandler(logging.Handler):
    def __init__(self, sink: deque[str]) -> None:
        super().__init__()
        self.sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.append(self.format(record))
        except Exception:
            pass


def worker(
    state: RunState,
    korail_id: str,
    korail_pw: str,
    passengers: KorailPassengerCounts,
    legs: list[Leg],
    options: dict,
) -> None:
    handler = DequeHandler(state.logs)
    macro_logger = logging.getLogger("ktx_macro")
    macro_logger.addHandler(handler)
    macro = None
    try:
        macro = KTXMacro(korail_id, korail_pw, passengers=passengers)
        macro.run_legs(legs, stop_event=state.stop_event, **options)
        reserved = sum(1 for leg in legs if leg.done)
        if reserved == len(legs):
            state.status = "done"
        elif reserved > 0:
            state.status = "partial"
        elif state.stop_event.is_set():
            state.status = "stopped"
        else:
            state.status = "no_result"
    except Exception as exc:  # 로그인 실패 등
        state.error = str(exc)
        state.status = "error"
        macro_logger.error("실행 중단: %s", exc)
    finally:
        if macro is not None:
            macro.close()
        macro_logger.removeHandler(handler)


def get_state() -> RunState:
    if "run_state" not in st.session_state:
        st.session_state.run_state = RunState()
    return st.session_state.run_state


def hhmmss(value: dtime) -> str:
    return value.strftime("%H%M%S")


# ---------------------------------------------------------------------------
# 사이드바: 계정
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("코레일 계정")
    korail_id = st.text_input("아이디 (휴대폰 / 이메일 / 회원번호)", value=os.getenv("KORAIL_ID", ""))
    korail_pw = st.text_input("비밀번호", value=os.getenv("KORAIL_PW", ""), type="password")
    st.caption(".env 파일에 KORAIL_ID / KORAIL_PW 를 넣어두면 자동으로 채워집니다. 입력값은 저장되지 않습니다.")
    st.divider()
    st.caption("예약만 하고 결제는 하지 않습니다. 예약 후 구입기한 안에 코레일톡이나 홈페이지에서 결제하세요.")

# ---------------------------------------------------------------------------
# 본문: 공통 조건
# ---------------------------------------------------------------------------
st.title("🚄 KTX 지정석 자동 예약")
st.caption(
    "지정한 시간대에 KTX 일반실/특실 지정석이 나올 때까지 반복 조회하고, 나오면 예약을 잡습니다. "
    "가는 편과 오는 편을 함께 켜면 한 세션에서 번갈아 조회하고, 먼저 잡힌 쪽은 두고 남은 쪽을 계속 찾습니다."
)

state = get_state()
running = state.running

@st.cache_data(ttl=24 * 3600, show_spinner="역 목록을 불러오는 중...")
def load_station_names() -> list[str]:
    """코레일 역 목록. 주요역이 앞에 오고 나머지는 가나다순. 하루 동안 캐시."""
    return fetch_station_names()


station_names = load_station_names()


def station_index(name: str) -> int:
    return station_names.index(name) if name in station_names else 0


def swap_stations() -> None:
    """출발역 <-> 도착역. 위젯이 그려지기 전(콜백)에 세션 상태를 바꿔야 한다."""
    ss = st.session_state
    ss["dep_station"], ss["arr_station"] = ss.get("arr_station"), ss.get("dep_station")


s1, s2, s3 = st.columns([3, 3, 1])
with s1:
    dep = st.selectbox(
        "출발역",
        station_names,
        index=station_index("수서"),
        key="dep_station",
        disabled=running,
        accept_new_options=True,
        help="입력해서 검색할 수 있습니다. 목록에 없는 역은 직접 입력해도 됩니다.",
    ) or ""
with s2:
    arr = st.selectbox(
        "도착역",
        station_names,
        index=station_index("부산"),
        key="arr_station",
        disabled=running,
        accept_new_options=True,
        help="입력해서 검색할 수 있습니다. 목록에 없는 역은 직접 입력해도 됩니다.",
    ) or ""
with s3:
    st.write("")
    st.write("")
    st.button(
        "⇄",
        help="출발역과 도착역 바꾸기",
        disabled=running,
        use_container_width=True,
        on_click=swap_stations,
    )

dep_name = dep.strip() or "출발역"
arr_name = arr.strip() or "도착역"


def leg_inputs(key: str, title: str, default_date: date, default_on: bool) -> dict:
    """구간 하나의 입력 위젯. 왼쪽/오른쪽 컬럼 안에서 호출한다."""
    enabled = st.checkbox("이 구간 예약", value=default_on, key=f"{key}_on", disabled=running)
    locked = running or not enabled
    travel_date = st.date_input(
        "날짜", value=default_date, min_value=date.today(), key=f"{key}_date", disabled=locked
    )
    t1, t2 = st.columns(2)
    with t1:
        start_time = st.time_input(
            "출발 시작", value=dtime(8, 0), step=timedelta(minutes=10), key=f"{key}_start", disabled=locked
        )
    with t2:
        end_time = st.time_input(
            "출발 종료", value=dtime(20, 0), step=timedelta(minutes=10), key=f"{key}_end", disabled=locked
        )
    use_deadline = st.checkbox("도착 기한 지정", value=False, key=f"{key}_dl_on", disabled=locked)
    arrive_before = st.time_input(
        "이 시각 전 도착만",
        value=dtime(18, 0),
        step=timedelta(minutes=10),
        key=f"{key}_dl",
        disabled=locked or not use_deadline,
    )
    return dict(
        enabled=enabled,
        title=title,
        date=travel_date,
        start_time=start_time,
        end_time=end_time,
        arrive_before=arrive_before if use_deadline else None,
    )


left, right = st.columns(2, gap="large")
with left:
    st.subheader(f"가는 편: {dep_name} → {arr_name}")
    outbound = leg_inputs("out", "가는 편", date.today() + timedelta(days=1), True)
with right:
    st.subheader(f"오는 편: {arr_name} → {dep_name}")
    inbound = leg_inputs("in", "오는 편", date.today() + timedelta(days=2), True)

st.subheader("인원 (두 구간 공통)")
p1, p2, p3, p4, p5 = st.columns(5)
with p1:
    n_adult = st.number_input("어른", min_value=0, max_value=9, value=2, disabled=running)
with p2:
    n_teen = st.number_input("청소년", min_value=0, max_value=9, value=0, disabled=running)
with p3:
    n_child = st.number_input("어린이 (만 6~12세, 좌석)", min_value=0, max_value=9, value=1, disabled=running)
with p4:
    n_senior = st.number_input("경로", min_value=0, max_value=9, value=0, disabled=running)
with p5:
    n_infant = st.number_input("유아 (만 6세 미만, 좌석 없음)", min_value=0, max_value=9, value=0, disabled=running)

with st.expander("고급 설정"):
    a1, a2, a3 = st.columns(3)
    with a1:
        interval = st.number_input("조회 간격 (초)", min_value=3, max_value=120, value=8, disabled=running)
    with a2:
        max_attempts = st.number_input(
            "최대 조회 횟수 (0 = 나올 때까지)", min_value=0, max_value=100000, value=0, disabled=running
        )
    with a3:
        search_only = st.checkbox("조회만 (예약 안 함)", value=False, disabled=running)
    st.caption("간격을 너무 짧게 하면 코레일에서 차단될 수 있습니다. 왕복이면 한 주기에 두 번 조회합니다.")

# ---------------------------------------------------------------------------
# 시작 / 중지
# ---------------------------------------------------------------------------
b1, b2, _ = st.columns([1, 1, 4])
start_clicked = b1.button("▶ 시작", type="primary", disabled=running, use_container_width=True)
stop_clicked = b2.button("■ 중지", disabled=not running, use_container_width=True)

if stop_clicked and running:
    state.stop_event.set()
    state.logs.append("중지 요청을 보냈습니다. 진행 중인 조회가 끝나면 멈춥니다.")
    st.rerun()

if start_clicked and not running:
    errors: list[str] = []
    if not korail_id.strip() or not korail_pw:
        errors.append("코레일 아이디와 비밀번호를 입력하세요.")
    if not dep.strip() or not arr.strip():
        errors.append("출발역과 도착역을 입력하세요.")
    if dep.strip() and dep.strip() == arr.strip():
        errors.append("출발역과 도착역이 같습니다.")

    leg_specs = [(outbound, dep.strip(), arr.strip()), (inbound, arr.strip(), dep.strip())]
    active = [(spec, d, a) for spec, d, a in leg_specs if spec["enabled"]]
    if not active:
        errors.append("가는 편 또는 오는 편 중 하나 이상을 켜세요.")
    for spec, _, _ in active:
        if spec["start_time"] > spec["end_time"]:
            errors.append(f"{spec['title']}: 출발 시작 시각이 종료 시각보다 늦습니다.")
    if outbound["enabled"] and inbound["enabled"]:
        if inbound["date"] < outbound["date"]:
            errors.append("오는 편 날짜가 가는 편 날짜보다 빠릅니다.")
        elif inbound["date"] == outbound["date"] and inbound["start_time"] < outbound["start_time"]:
            errors.append("같은 날 왕복인데 오는 편 출발 시작이 가는 편 출발 시작보다 빠릅니다.")

    passengers = None
    try:
        passengers = KorailPassengerCounts(
            adult=int(n_adult),
            teenager=int(n_teen),
            child=int(n_child),
            infant=int(n_infant),
            senior=int(n_senior),
        )
    except ValueError as exc:
        errors.append(f"인원 설정 오류: {exc} (총 1~9명)")
    if passengers is not None and passengers.infant > passengers.adult + passengers.senior:
        errors.append("동반 유아는 보호자(어른/경로) 1명당 1명까지입니다.")

    if errors:
        for msg in errors:
            st.error(msg)
    else:
        legs = [
            Leg(
                name=spec["title"],
                date=spec["date"].strftime("%Y%m%d"),
                dep=d,
                arr=a,
                start_time=hhmmss(spec["start_time"]),
                end_time=hhmmss(spec["end_time"]),
                arrive_before=hhmmss(spec["arrive_before"]) if spec["arrive_before"] else None,
            )
            for spec, d, a in active
        ]
        new_state = RunState()
        new_state.started_at = datetime.now()
        new_state.legs = legs
        new_state.summary = [
            f"**{spec['title']}** {spec['date']:%Y-%m-%d} {d} → {a} | 출발 {spec['start_time']:%H:%M}~{spec['end_time']:%H:%M}"
            + (f" | 도착 {spec['arrive_before']:%H:%M} 전" if spec["arrive_before"] else "")
            for spec, d, a in active
        ] + [f"**인원** {format_passengers(passengers)}"]
        new_state.status = "running"
        options = dict(interval=int(interval), max_attempts=int(max_attempts), search_only=bool(search_only))
        new_state.thread = threading.Thread(
            target=worker,
            args=(new_state, korail_id.strip(), korail_pw, passengers, legs, options),
            daemon=True,
        )
        new_state.thread.start()
        st.session_state.run_state = new_state
        st.rerun()

# ---------------------------------------------------------------------------
# 상태 / 로그 (실행 중에는 1초마다 갱신)
# ---------------------------------------------------------------------------
st.divider()


def render_leg_results(legs: list[Leg]) -> None:
    if not legs:
        return
    cols = st.columns(len(legs))
    for col, leg in zip(cols, legs):
        with col:
            if leg.done:
                st.success(f"✅ {leg.name} 예약 완료 ({leg.dep} → {leg.arr} {leg.date})")
                d = getattr(leg.result, "payment_deadline_date", None)
                t = getattr(leg.result, "payment_deadline_time", None)
                if d and t:
                    st.markdown(f"**구입기한:** {d} {ktx_macro.format_hhmm(t)}")
                elif getattr(leg.result, "payment_deadline_message", None):
                    st.markdown(f"**구입기한:** {leg.result.payment_deadline_message}")
                with st.expander("예약 응답 원문"):
                    st.write(leg.result)
            else:
                st.info(f"⏳ {leg.name} 아직 예약 안 됨 ({leg.dep} → {leg.arr} {leg.date})")


def render_status_and_logs() -> None:
    state = get_state()
    running_now = state.running

    if state.summary:
        st.markdown("  \n".join(state.summary))

    if running_now:
        elapsed = datetime.now() - (state.started_at or datetime.now())
        st.info(f"⏳ 실행 중... (경과 {int(elapsed.total_seconds())}초)")
    elif state.status == "done":
        st.success("✅ 모든 구간 예약 완료. 코레일톡/홈페이지에서 구입기한 안에 결제하세요.")
    elif state.status == "partial":
        st.warning("일부 구간만 예약됐습니다. 예약된 구간은 구입기한 안에 결제하세요.")
    elif state.status == "stopped":
        st.warning("⏹ 사용자가 중지했습니다.")
    elif state.status == "no_result":
        st.warning("조회를 마쳤지만 예약하지 않았습니다. (조회만 모드이거나 최대 횟수 도달)")
    elif state.status == "error":
        st.error(f"❌ 오류: {state.error}")

    if state.legs:
        render_leg_results(state.legs)

    log_text = "\n".join(state.logs) if state.logs else "아직 로그가 없습니다."
    st.code(log_text, language=None, line_numbers=False)

    # 실행이 끝난 순간 전체 화면을 한 번 다시 그려 버튼 상태를 맞춘다.
    if not running_now and st.session_state.get("_was_running", False):
        st.session_state._was_running = False
        st.rerun()
    st.session_state._was_running = running_now


if running:
    st.fragment(run_every=1.0)(render_status_and_logs)()
else:
    render_status_and_logs()
