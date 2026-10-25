"""검색 결과를 잠깐 기억하는 작은 캐시 — 같은 검색어(입력을 고치다 되돌린 경우, 여러
사람이 같은 작품을 찾는 경우)가 오면 카카오·유튜브 뮤직·TMDB에 다시 묻지 않음. 그만큼 빨리
뜨고, 카카오 하루 한도와 유튜브가 우리 서버 IP를 봇으로 볼 위험이 줄어듦.

서버 한 대·프로세스 하나라 메모리 안에 둠(Redis 없이 — ratelimit.py와 같은 판단).
엔드포인트는 스레드에서 돌아서(FastAPI의 동기 함수) 잠금을 씀. 오래된 것부터 버림.
"""
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._items: "OrderedDict[Hashable, tuple]" = OrderedDict()
        self._lock = threading.Lock()

    def get_or_set(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        """key로 기억해 둔 값이 ttl 안이면 그대로, 아니면 compute()를 불러 기억하고 돌려줌.
        compute()가 예외를 내면(검색 실패) 기억하지 않고 그대로 올려 보냄 — 잠깐의 실패가
        ttl 동안 굳지 않게."""
        now = time.monotonic()
        with self._lock:
            hit = self._items.get(key)
            if hit and now - hit[0] < self.ttl:
                self._items.move_to_end(key)
                return hit[1]
        value = compute()  # 잠금 밖에서 — 외부 호출이 느려도 다른 검색을 막지 않게
        with self._lock:
            self._items[key] = (now, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
