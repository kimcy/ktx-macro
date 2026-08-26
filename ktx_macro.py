#!/usr/bin/env python3
"""KTX 매크로 - 지정 시간대에서 지정석이 나올 때까지 검색 후 예약.

korail2 는 2026년 기준 MACRO ERROR 로 로그인이 막힌다.
현재 코레일톡 API 는 korail-mobile-api (앱 6.5.0 기준) 를 사용한다.
k-skill ktx-booking 은 공개 시간표 조회 전용으로 바뀌어 예약은 하지 않는다.

입석 / 입석+좌석은 예약하지 않는다. 결제는 코레일톡/홈페이지에서 직접 해야 한다.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(interpolate=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from korail_mobile_api import (
    KorailAuthError,
    KorailClient,
    KorailConfig,
    KorailNoResultsError,
    KorailSeatClass,
    KorailSessionExpiredError,
    KorailSoldOutError,
    MutationConsent,
    TrainSearchQuery,
)

ASSIGNED_SEAT_CODE = "11"
KTX_TRAIN_GROUP = "100"
MUGUNGHWA_MARKERS = ("무궁화",)


def parse_time(value: str) -> str:
    """HHMMSS / HHMM / HH:MM / HH:MM:SS 를 HHMMSS 로 정규화."""
    raw = value.replace(":", "").strip()
    if len(raw) == 4:
        raw += "00"
    if not raw.isdigit() or len(raw) != 6:
        raise ValueError(f"시간 형식 오류: {value} (HHMMSS 또는 HH:MM)")
    hh, mm, ss = int(raw[:2]), int(raw[2:4]), int(raw[4:])
    if hh > 23 or mm > 59 or ss > 59:
        raise ValueError(f"잘못된 시각: {value}")
    return raw


def as_hhmmss(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).replace(":", "").strip()
    if not cleaned:
        return None
    if len(cleaned) == 4 and cleaned.isdigit():
        cleaned += "00"
    if len(cleaned) >= 6 and cleaned[:6].isdigit():
        return cleaned[:6]
    return parse_time(cleaned)


def format_hhmm(value: str | None) -> str:
    hhmmss = as_hhmmss(value)
    if not hhmmss:
        return "?"
    return f"{hhmmss[:2]}:{hhmmss[2:4]}"


def is_time_in_range(dep_time: str | None, start: str, end: str) -> bool:
    dep = as_hhmmss(dep_time)
    return dep is not None and start <= dep <= end


def arrives_before(arr_time: str | None, deadline: str) -> bool:
    arr = as_hhmmss(arr_time)
    return arr is not None and arr < deadline


def normalize_login_id(value: str) -> str:
    """휴대폰은 하이픈 없이 숫자만. 이메일은 그대로."""
    value = value.strip()
    if "@" in value:
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("01") and len(digits) in {10, 11}:
        return digits
    return value


def is_mugunghwa(train) -> bool:
    name = f"{getattr(train, 'train_class_name', '') or ''} {getattr(train, 'train_group_name', '') or ''}"
    return any(marker in name for marker in MUGUNGHWA_MARKERS)


def has_assigned_seat(train) -> bool:
    """일반실/특실 지정석만 True. 입석·입석+좌석은 False."""
    return (
        getattr(train, "general_reservation_code", None) == ASSIGNED_SEAT_CODE
        or getattr(train, "special_reservation_code", None) == ASSIGNED_SEAT_CODE
    )


def preferred_seat_class(train) -> KorailSeatClass:
    if getattr(train, "general_reservation_code", None) == ASSIGNED_SEAT_CODE:
        return KorailSeatClass.GENERAL
    return KorailSeatClass.SPECIAL


def seat_status(train) -> str:
    kinds = []
    if getattr(train, "general_reservation_code", None) == ASSIGNED_SEAT_CODE:
        kinds.append("일반실")
    if getattr(train, "special_reservation_code", None) == ASSIGNED_SEAT_CODE:
        kinds.append("특실")
    if kinds:
        return ",".join(kinds) + " 예약가능"
    return "지정석 없음"


def format_train(train) -> str:
    type_name = getattr(train, "train_class_name", "") or "KTX"
    train_no = getattr(train, "train_no", "?")
    dep = format_hhmm(getattr(train, "departure_time", None))
    arr = format_hhmm(getattr(train, "arrival_time", None))
    return f"[{type_name}] {train_no} {dep}→{arr} ({seat_status(train)})"


def notify_success() -> None:
    try:
        import winsound

        winsound.Beep(1000, 600)
        winsound.Beep(1400, 800)
    except Exception:
        print("\a", end="", flush=True)


class KTXMacro:
    def __init__(self, korail_id: str, korail_pw: str):
        self.korail_id = normalize_login_id(korail_id)
        self.korail_pw = korail_pw
        self.client = KorailClient(KorailConfig(enable_dynapath=True))
        self._login()

    def close(self) -> None:
        try:
            self.client.logout()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass

    def _login(self) -> None:
        try:
            session = self.client.login(self.korail_id, self.korail_pw)
        except KorailAuthError as exc:
            raise RuntimeError(f"코레일 로그인 실패: {exc}") from exc
        name = (session.raw or {}).get("strCustNm") or ""
        suffix = f" ({name})" if name else ""
        logger.info("코레일 로그인 성공%s", suffix)

    def _ensure_login(self) -> None:
        if getattr(self.client.session, "current", None) is None:
            logger.warning("세션이 없습니다. 다시 로그인합니다.")
            self._login()

    def search_trains(
        self,
        date: str,
        dep: str,
        arr: str,
        start_time: str,
        end_time: str,
        arrive_before: str | None,
    ):
        start = parse_time(start_time)
        end = parse_time(end_time)
        deadline = parse_time(arrive_before) if arrive_before else None
        query = TrainSearchQuery(
            dep,
            arr,
            date,
            departure_time=start,
            train_group_code=KTX_TRAIN_GROUP,
        )

        trains = []
        continuation = None
        while True:
            try:
                result = self.client.search_trains(query, continuation=continuation)
            except KorailNoResultsError:
                break
            except KorailSessionExpiredError:
                self._login()
                continuation = None
                continue

            page = list(result.trains or [])
            if not page:
                break
            trains.extend(page)
            last_dep = as_hhmmss(getattr(page[-1], "departure_time", None))
            if last_dep and last_dep > end:
                break
            continuation = result.next_page()
            if continuation is None:
                break

        matched = []
        for train in trains:
            if is_mugunghwa(train):
                continue
            if not is_time_in_range(getattr(train, "departure_time", None), start, end):
                continue
            if deadline and not arrives_before(getattr(train, "arrival_time", None), deadline):
                continue
            matched.append(train)
        return matched

    def try_reserve(self, train):
        if not has_assigned_seat(train):
            logger.info("지정석이 없어 예약하지 않음: %s", format_train(train))
            return None
        consent = MutationConsent(allow_reserve=True, dry_run=False)
        try:
            hold = self.client.reserve(
                train,
                consent=consent,
                seat_class=preferred_seat_class(train),
            )
            logger.info("예약 성공: %s", hold)
            return hold
        except KorailSessionExpiredError:
            self._login()
            return self.try_reserve(train)
        except KorailSoldOutError as exc:
            logger.warning("예약 중 매진: %s", exc)
            return None
        except Exception as exc:
            logger.error("예약 실패: %s", exc)
            return None

    def run(
        self,
        date: str,
        dep: str,
        arr: str,
        start_time: str,
        end_time: str,
        arrive_before: str | None,
        interval: int = 8,
        max_attempts: int = 0,
        search_only: bool = False,
    ):
        arrive_txt = f", 도착 {arrive_before} 전" if arrive_before else ""
        attempts_txt = "무제한" if max_attempts <= 0 else f"{max_attempts}회"
        logger.info(
            "검색 시작: %s %s -> %s (출발 %s~%s%s) | 간격 %s초 | 최대 %s",
            date,
            dep,
            arr,
            start_time,
            end_time,
            arrive_txt,
            interval,
            attempts_txt,
        )
        logger.info("입석/입석+좌석은 무시하고, 일반실·특실 지정석만 예약합니다. 무궁화호는 제외합니다.")

        attempt = 0
        while True:
            attempt += 1
            if max_attempts > 0 and attempt > max_attempts:
                logger.warning("최대 시도 횟수 도달. 종료")
                return None

            self._ensure_login()
            logger.info("[%s] 검색 중...", attempt if max_attempts <= 0 else f"{attempt}/{max_attempts}")

            try:
                trains = self.search_trains(date, dep, arr, start_time, end_time, arrive_before)
            except Exception as exc:
                logger.error("검색 중 예외: %s", exc)
                time.sleep(interval)
                continue

            if not trains:
                logger.info("조건에 맞는 열차 없음. 대기...")
            else:
                seated = [train for train in trains if has_assigned_seat(train)]
                for train in trains:
                    logger.info("  %s", format_train(train))

                if search_only:
                    logger.info(
                        "조회만 수행 (--search-only). 지정석 %s편 / 입석·매진 %s편",
                        len(seated),
                        len(trains) - len(seated),
                    )
                    return None

                if seated:
                    target = seated[0]
                    logger.info("지정석 발견, 예약 시도: %s", format_train(target))
                    result = self.try_reserve(target)
                    if result:
                        logger.info("예약 완료. 코레일톡/홈페이지에서 결제하세요.")
                        deadline_date = getattr(result, "payment_deadline_date", None)
                        deadline_time = getattr(result, "payment_deadline_time", None)
                        if deadline_date and deadline_time:
                            logger.info("구입기한: %s %s", deadline_date, format_hhmm(deadline_time))
                        elif getattr(result, "payment_deadline_message", None):
                            logger.info("구입기한: %s", result.payment_deadline_message)
                        notify_success()
                        return result
                    logger.warning("예약 실패. 다음 주기에 다시 시도합니다.")
                else:
                    logger.info("아직 지정석 없음 (입석+좌석/매진). 계속 대기합니다.")

            if search_only:
                return None
            time.sleep(interval)


def load_credentials() -> tuple[str, str]:
    korail_id = os.getenv("KORAIL_ID")
    korail_pw = os.getenv("KORAIL_PW")
    if not korail_id or not korail_pw:
        raise ValueError("KORAIL_ID 와 KORAIL_PW 를 .env 파일에 설정해주세요.")
    return korail_id, korail_pw


def main() -> None:
    parser = argparse.ArgumentParser(description="KTX 시간대 자동 예약 매크로 (지정석만)")
    parser.add_argument("--date", required=True, help="예약 날짜 (YYYYMMDD, 예: 20260827)")
    parser.add_argument("--dep", required=True, help="출발역 (예: 대전)")
    parser.add_argument("--arr", required=True, help="도착역 (예: 서울)")
    parser.add_argument("--start-time", default="080000", help="출발 시작 시각 (HHMMSS 또는 HH:MM)")
    parser.add_argument("--end-time", default="235959", help="출발 종료 시각 (HHMMSS 또는 HH:MM)")
    parser.add_argument(
        "--arrive-before",
        default=None,
        help="이 시각 전 도착만 (HHMMSS 또는 HH:MM). 예: 180000",
    )
    parser.add_argument("--interval", type=int, default=8, help="검색 간격 (초)")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="최대 시도 횟수. 0이면 좌석이 나올 때까지 계속",
    )
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="예약하지 않고 한 번만 조회",
    )
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y%m%d")
    parse_time(args.start_time)
    parse_time(args.end_time)
    if args.arrive_before:
        parse_time(args.arrive_before)

    korail_id, korail_pw = load_credentials()
    macro = KTXMacro(korail_id, korail_pw)
    try:
        macro.run(
            date=args.date,
            dep=args.dep,
            arr=args.arr,
            start_time=args.start_time,
            end_time=args.end_time,
            arrive_before=args.arrive_before,
            interval=args.interval,
            max_attempts=args.max_attempts,
            search_only=args.search_only,
        )
    finally:
        macro.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("사용자 중단")
