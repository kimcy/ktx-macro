# ktx-macro

지정한 날짜·구간·시간대에서 KTX **지정석**(일반실/특실)이 나올 때까지 조회하고, 나오면 예약까지 시도하는 스크립트입니다. 결제는 하지 않습니다. 예약 후 코레일톡/홈페이지에서 직접 결제하세요.

## 현재 동작

- KTX / KTX-산천만 대상 (무궁화호 제외)
- **입석, 입석+좌석은 예약하지 않음.** 지정석이 뜰 때만 예약
- 출발 시각 범위 + 도착 시각 상한(`--arrive-before`) 필터
- 지정석이 나올 때까지 반복 조회 (기본 간격 8초, `--max-attempts 0`이면 무제한)
- 일반실 우선, 없으면 특실
- 성인 1명 기준

구버전 `korail2`는 코레일이 `MACRO ERROR`로 로그인을 막습니다. 지금은 코레일톡 앱 API를 쓰는 [`korail-mobile-api`](https://github.com/yakisoba0728/korail-mobile-api)를 사용합니다.

[k-skill](https://github.com/NomaDamas/k-skill)의 `ktx-booking`은 현재 **공식 시간표 조회 전용**이라 로그인·실시간 좌석·예약은 하지 않습니다.

## 설치

```bash
git clone https://github.com/comflife/ktx-macro.git
cd ktx-macro
pip install -r requirements.txt
```

Python 3.11 이상이 필요합니다.

## 계정 설정

프로젝트 폴더에 `.env`를 만듭니다. 이 파일은 `.gitignore`에 들어 있어 **git에 올라가지 않습니다.**

```
KORAIL_ID=회원번호_또는_이메일_또는_01012345678
KORAIL_PW=비밀번호
```

- 휴대폰은 `01012345678`처럼 숫자만, 또는 `010-1234-5678` 모두 됩니다 (실행 시 숫자만 쓰도록 정규화)
- 비밀번호에 `$`가 있어도 그대로 읽습니다
- 예시 파일은 `.env.example`입니다. 실제 아이디/비밀번호는 `.env`에만 넣으세요

## 실행

```bash
python ktx_macro.py --date 20260827 --dep 대전 --arr 서울 --start-time 160000 --end-time 163000 --arrive-before 180000
```

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--date` | 날짜 `YYYYMMDD` | 필수 |
| `--dep` / `--arr` | 출발역 / 도착역 | 필수 |
| `--start-time` | 출발 시작 시각 `HHMMSS` 또는 `HH:MM` | `080000` |
| `--end-time` | 출발 종료 시각 | `235959` |
| `--arrive-before` | 이 시각 **전** 도착만 | 없음 |
| `--interval` | 조회 간격(초) | `8` |
| `--max-attempts` | 최대 조회 횟수. `0`이면 지정석이 나올 때까지 | `0` |
| `--search-only` | 예약하지 않고 한 번만 조회 | 꺼짐 |

조회만 하려면:

```bash
python ktx_macro.py --date 20260827 --dep 대전 --arr 서울 --start-time 160000 --end-time 163000 --arrive-before 180000 --search-only
```

간격을 바꾸려면 `--interval 3`처럼 붙이면 됩니다. 너무 짧게 치면 차단될 수 있습니다.

중지: `Ctrl+C`

예약되면 비프음이 나고, **구입기한 안에 코레일톡/웹에서 결제**해야 합니다. 결제하지 않으면 예약이 풀립니다.

## 주의

- 개인 사용, 본인 계정, 본인 책임입니다. 코레일 약관에 걸릴 수 있습니다.
- 결제는 자동화하지 않습니다 (예약까지).
- 코레일 안티매크로가 바뀌면 다시 실패할 수 있습니다.

## 참고

- https://github.com/yakisoba0728/korail-mobile-api
- https://github.com/NomaDamas/k-skill (현재 KTX는 공개 시간표 조회만)
- https://github.com/carpedm20/korail2 (구버전, 로그인 차단됨)
