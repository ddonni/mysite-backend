"""IP별 횟수 제한 — 로그인 없이 누구나 부를 수 있는 엔드포인트(방 만들기, 의견 보내기,
책·음악·영화 검색)가 같이 씀. 프로세스 메모리 안의 고정 창(window) 카운터라 Redis 없이
돌아감(서버 한 대, uvicorn 프로세스 하나라 이걸로 충분).

한도는 "한 사람"이 아니라 "공인 IP 하나" 기준이라는 점에 주의 — 학원·회사처럼 여러 PC가
공유기(NAT) 뒤에서 공인 IP 하나로 나가면 다 같은 IP로 보임. 그래서 각 한도는 한 곳에서
30명쯤 같이 써도 남게 잡음(main.py·search_routes.py의 각 상수 설명).
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    # Nginx terminates TLS and proxies to uvicorn on 127.0.0.1, so
    # request.client.host is the proxy, not the visitor. Nginx *overwrites*
    # X-Forwarded-For with $remote_addr (not appends), which is what makes
    # trusting the first entry safe — an appended header would let a visitor
    # spoof it. Fall back to request.client.host for local/test runs where
    # that header is absent.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request, log: Dict[str, List[datetime]], limit: int, window: timedelta, detail: str) -> None:
    """log(IP → 최근 요청 시각들)에 이번 요청을 적고, window 안에 limit번을 넘으면 429."""
    ip = client_ip(request)
    now = datetime.now(timezone.utc)
    # 창(window)이 지난 IP는 통째로 지움 — 안 그러면 한 번이라도 요청한
    # IP마다 빈 목록이 프로세스가 살아 있는 내내 쌓여감.
    for other in [k for k, ts in log.items() if not ts or now - ts[-1] >= window]:
        del log[other]
    recent = [t for t in log.get(ip, []) if now - t < window]
    if len(recent) >= limit:
        raise HTTPException(status_code=429, detail=detail)
    recent.append(now)
    log[ip] = recent
