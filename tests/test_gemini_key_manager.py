"""
#65 Gemini 다중 API 키 로드밸런싱 테스트 (TDD)

- 콤마 구분 키 파싱
- 라운드로빈 순환
- 429 에러 시 해당 키 일시 스킵
- 모든 키 소진 시 예외
- 단일 키 하위호환
"""
import time
import pytest

from services.gemini_key_manager import GeminiKeyManager


class TestGeminiKeyManagerParsing:
    def test_single_key(self):
        mgr = GeminiKeyManager("key_a")
        assert mgr.key_count == 1

    def test_comma_separated_keys(self):
        mgr = GeminiKeyManager("key_a,key_b,key_c")
        assert mgr.key_count == 3

    def test_strips_whitespace(self):
        mgr = GeminiKeyManager(" key_a , key_b ")
        assert mgr.key_count == 2

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="GEMINI_API_KEYS"):
            GeminiKeyManager("")

    def test_only_commas_raises(self):
        with pytest.raises(ValueError, match="GEMINI_API_KEYS"):
            GeminiKeyManager(",,,")


class TestGeminiKeyManagerRoundRobin:
    def test_round_robin_order(self):
        mgr = GeminiKeyManager("key_a,key_b,key_c")
        keys = [mgr.next_key() for _ in range(6)]
        assert keys == ["key_a", "key_b", "key_c", "key_a", "key_b", "key_c"]

    def test_single_key_always_returns_same(self):
        mgr = GeminiKeyManager("only_key")
        assert mgr.next_key() == "only_key"
        assert mgr.next_key() == "only_key"

    def test_next_key_returns_string(self):
        mgr = GeminiKeyManager("key_a,key_b")
        key = mgr.next_key()
        assert isinstance(key, str)
        assert key in ("key_a", "key_b")


class TestGeminiKeyManagerRateLimit:
    def test_mark_rate_limited_skips_key(self):
        """rate limit 걸린 키는 다음 호출에서 스킵"""
        mgr = GeminiKeyManager("key_a,key_b,key_c")
        mgr.mark_rate_limited("key_a", cooldown_seconds=60)
        # key_a가 스킵되어야 함
        keys = {mgr.next_key() for _ in range(10)}
        assert "key_a" not in keys
        assert "key_b" in keys or "key_c" in keys

    def test_rate_limited_key_recovers_after_cooldown(self):
        """cooldown 만료 후 키 복구"""
        mgr = GeminiKeyManager("key_a,key_b")
        mgr.mark_rate_limited("key_a", cooldown_seconds=0)
        # cooldown=0 → 즉시 만료
        time.sleep(0.01)
        keys = {mgr.next_key() for _ in range(10)}
        assert "key_a" in keys

    def test_all_keys_rate_limited_raises(self):
        """모든 키 rate limit → NoAvailableKeyError"""
        from services.gemini_key_manager import NoAvailableKeyError
        mgr = GeminiKeyManager("key_a,key_b")
        mgr.mark_rate_limited("key_a", cooldown_seconds=60)
        mgr.mark_rate_limited("key_b", cooldown_seconds=60)
        with pytest.raises(NoAvailableKeyError):
            mgr.next_key()


class TestGeminiKeyManagerDistribution:
    def test_keys_distributed_evenly(self):
        """N개 요청을 N개 키에 균등 분배"""
        mgr = GeminiKeyManager("k1,k2,k3")
        counts = {"k1": 0, "k2": 0, "k3": 0}
        for _ in range(30):
            counts[mgr.next_key()] += 1
        assert counts["k1"] == counts["k2"] == counts["k3"] == 10
