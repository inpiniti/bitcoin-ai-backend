"""
#65 Gemini API 다중 키 로드밸런싱

환경변수 GEMINI_API_KEYS 에 콤마로 여러 키를 등록.
  예) GEMINI_API_KEYS=key1,key2,key3

- 라운드로빈으로 키 순환
- 429 Rate Limit 시 해당 키 cooldown 동안 스킵
- 모든 키 소진 시 NoAvailableKeyError 발생
"""
import os
import time
import threading
import logging
from typing import Optional

logger = logging.getLogger("gemini_key_manager")


class NoAvailableKeyError(Exception):
    """사용 가능한 Gemini API 키가 없을 때"""
    pass


class GeminiKeyManager:
    """
    콤마로 구분된 Gemini API 키를 라운드로빈으로 분배.
    Rate Limit(429) 발생 키는 cooldown 동안 자동 스킵.
    """

    def __init__(self, keys_str: str):
        """
        Args:
            keys_str: 콤마 구분 API 키 문자열 (예: "key1,key2,key3")
        """
        keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        if not keys:
            raise ValueError("GEMINI_API_KEYS가 비어 있습니다. 최소 1개 이상의 키가 필요합니다.")

        self._keys: list[str] = keys
        self._index: int = 0
        self._lock = threading.Lock()
        # key → 사용 가능해지는 시각 (unix timestamp)
        self._rate_limited_until: dict[str, float] = {}

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def next_key(self) -> str:
        """
        다음 사용 가능한 키를 라운드로빈으로 반환.

        Raises:
            NoAvailableKeyError: 모든 키가 rate limit 상태일 때
        """
        with self._lock:
            now = time.time()
            n = len(self._keys)

            for _ in range(n):
                key = self._keys[self._index % n]
                self._index += 1

                available_at = self._rate_limited_until.get(key, 0)
                if now >= available_at:
                    return key
                else:
                    logger.debug(
                        f"[KeyManager] {key[:8]}... rate limited, "
                        f"남은 {available_at - now:.1f}s"
                    )

            raise NoAvailableKeyError(
                f"모든 Gemini API 키({n}개)가 rate limit 상태입니다."
            )

    def mark_rate_limited(self, key: str, cooldown_seconds: int = 60) -> None:
        """
        특정 키를 cooldown_seconds 동안 사용 불가로 표시.

        Args:
            key: rate limit 된 API 키
            cooldown_seconds: 재사용까지 대기 시간 (기본 60초)
        """
        with self._lock:
            self._rate_limited_until[key] = time.time() + cooldown_seconds
            logger.warning(
                f"[KeyManager] {key[:8]}... rate limited → {cooldown_seconds}s 후 복구"
            )

    @classmethod
    def from_env(cls, env_var: str = "GEMINI_API_KEYS") -> "GeminiKeyManager":
        """
        환경변수에서 키 매니저 생성.

        Args:
            env_var: 환경변수 이름 (기본: GEMINI_API_KEYS)

        Raises:
            ValueError: 환경변수가 없거나 비어 있을 때
        """
        keys_str = os.environ.get(env_var, "").strip()
        if not keys_str:
            raise ValueError(
                f"환경변수 {env_var} 가 설정되지 않았습니다. "
                "HuggingFace Space Secrets에 등록하세요."
            )
        manager = cls(keys_str)
        logger.info(f"[KeyManager] {manager.key_count}개 Gemini 키 로드 완료")
        return manager


# ── 싱글턴 (뉴스 분석 서비스에서 공유) ──────────────────────────────────────

_instance: Optional[GeminiKeyManager] = None


def get_key_manager() -> GeminiKeyManager:
    """
    앱 전역 싱글턴 GeminiKeyManager 반환.
    GEMINI_API_KEYS 환경변수 우선, 없으면 GEMINI_API_KEY 단일 키 시도.
    """
    global _instance
    if _instance is None:
        keys_str = os.environ.get("GEMINI_API_KEYS", "").strip()
        if not keys_str:
            # 단일 키 하위호환
            single = os.environ.get("GEMINI_API_KEY", "").strip()
            if not single:
                raise ValueError(
                    "GEMINI_API_KEYS 또는 GEMINI_API_KEY 환경변수를 설정하세요."
                )
            keys_str = single
        _instance = GeminiKeyManager(keys_str)
        logger.info(f"[KeyManager] 싱글턴 초기화: {_instance.key_count}개 키")
    return _instance
