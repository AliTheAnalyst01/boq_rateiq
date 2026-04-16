"""Unit tests for validators.validate_rate and validators.bounds_hint."""

from rateiq.validators import validate_rate, bounds_hint


class TestValidateRate:
    def test_civil_sft_valid(self):
        assert validate_rate("civil_id", "sft", 315.0) is True

    def test_civil_sft_too_low(self):
        assert validate_rate("civil_id", "sft", 14.0) is False

    def test_civil_sft_too_high(self):
        assert validate_rate("civil_id", "sft", 5000.0) is False

    def test_civil_sft_at_lower_bound(self):
        assert validate_rate("civil_id", "sft", 50.0) is True

    def test_civil_sft_at_upper_bound(self):
        assert validate_rate("civil_id", "sft", 3000.0) is True

    def test_hvac_nos_valid_ac_unit(self):
        assert validate_rate("hvac", "nos", 450_000.0) is True

    def test_hvac_nos_too_low(self):
        assert validate_rate("hvac", "nos", 500.0) is False

    def test_ceiling_sft_valid(self):
        assert validate_rate("ceiling", "sft", 450.0) is True

    def test_ceiling_sft_too_low(self):
        assert validate_rate("ceiling", "sft", 50.0) is False

    def test_unknown_category_permissive(self):
        assert validate_rate("unknown_cat", "sft", 1.0) is True

    def test_known_category_unknown_unit_permissive(self):
        assert validate_rate("civil_id", "truck", 5000.0) is True

    def test_zero_rate_rejected(self):
        assert validate_rate("civil_id", "sft", 0.0) is False

    def test_negative_rate_rejected(self):
        assert validate_rate("civil_id", "sft", -100.0) is False

    def test_electrical_point_valid(self):
        assert validate_rate("electrical_elv", "point", 3500.0) is True

    def test_electrical_point_too_high(self):
        assert validate_rate("electrical_elv", "point", 100_000.0) is False


class TestBoundsHint:
    def test_known_pair_returns_string(self):
        hint = bounds_hint("civil_id", "sft")
        assert "Rs." in hint
        assert "50" in hint
        assert "3,000" in hint

    def test_unknown_pair_returns_empty(self):
        hint = bounds_hint("unknown_cat", "sft")
        assert hint == ""

    def test_hvac_nos_returns_correct_range(self):
        hint = bounds_hint("hvac", "nos")
        assert "30,000" in hint
        assert "5,000,000" in hint
