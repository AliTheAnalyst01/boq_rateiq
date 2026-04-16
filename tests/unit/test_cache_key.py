"""Unit tests for market_search._cache_key normalization."""

from rateiq.market_search import _cache_key


class TestCacheKey:
    def test_different_units_produce_different_keys(self):
        key_sft = _cache_key("gypsum ceiling 12mm", "sft", "ceiling")
        key_nos = _cache_key("gypsum ceiling 12mm", "nos", "ceiling")
        assert key_sft != key_nos

    def test_different_categories_produce_different_keys(self):
        key_civil = _cache_key("brick work 4.5", "sft", "civil_id")
        key_finish = _cache_key("brick work 4.5", "sft", "finishing")
        assert key_civil != key_finish

    def test_short_words_filtered_out(self):
        # Words <= 2 chars are skipped; "brick work heavy" should be the 3 meaningful words
        key = _cache_key("a of in brick work heavy", "sft", "civil_id")
        assert "brick" in key

    def test_case_insensitive(self):
        key_lower = _cache_key("brick work 4.5", "sft", "civil_id")
        key_upper = _cache_key("BRICK WORK 4.5", "sft", "civil_id")
        assert key_lower == key_upper

    def test_key_format_contains_pipe_separators(self):
        key = _cache_key("brick work 4.5", "sft", "civil_id")
        assert "|" in key
        parts = key.split("|")
        assert len(parts) == 3
        assert parts[1] == "sft"
        assert parts[2] == "civil_id"

    def test_deterministic(self):
        key1 = _cache_key("brick work 4.5 inch", "sft", "civil_id")
        key2 = _cache_key("brick work 4.5 inch", "sft", "civil_id")
        assert key1 == key2
