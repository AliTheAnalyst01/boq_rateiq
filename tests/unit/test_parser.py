"""Unit tests for parser.normalize_unit, parser.clean_rate, parser.find_header_row."""

import numpy as np
import pandas as pd
import pytest

from rateiq.parser import normalize_unit, clean_rate, find_header_row


class TestNormalizeUnit:
    def test_sft_variants(self):
        for raw in ["sft", "SFT", "Sft", "sqft", "SQFT", "sq.ft", "sq ft", "sqf"]:
            assert normalize_unit(raw) == "sft", f"Failed for {raw!r}"

    def test_nos_variants(self):
        for raw in ["nos", "no", "no.", "NOS", "No.", "number", "numbers"]:
            assert normalize_unit(raw) == "nos", f"Failed for {raw!r}"

    def test_rft_variants(self):
        for raw in ["rft", "RFT", "runnft", "r.ft"]:
            assert normalize_unit(raw) == "rft", f"Failed for {raw!r}"

    def test_ls_variants(self):
        for raw in ["ls", "l.s", "lump sum", "lumpsum", "l/s"]:
            assert normalize_unit(raw) == "ls", f"Failed for {raw!r}"

    def test_empty_returns_unit(self):
        assert normalize_unit("") == "unit"

    def test_none_returns_unit(self):
        assert normalize_unit(None) == "unit"

    def test_trailing_dot_stripped(self):
        assert normalize_unit("Sft.") == "sft"


class TestCleanRate:
    def test_numeric_float_passthrough(self):
        assert clean_rate(315.0) == 315.0

    def test_numeric_int_converted(self):
        assert clean_rate(315) == 315.0

    def test_string_with_rs_prefix(self):
        assert clean_rate("Rs. 1,35,000") == 135000.0

    def test_string_with_commas(self):
        assert clean_rate("1,200") == 1200.0

    def test_string_plain_number(self):
        assert clean_rate("450") == 450.0

    def test_ofm_returns_none(self):
        assert clean_rate("OFM") is None

    def test_nan_returns_none(self):
        assert clean_rate(float("nan")) is None

    def test_none_returns_none(self):
        assert clean_rate(None) is None

    def test_zero_returns_none(self):
        assert clean_rate(0) is None

    def test_dash_returns_none(self):
        assert clean_rate("-") is None

    def test_empty_string_returns_none(self):
        assert clean_rate("") is None


class TestFindHeaderRow:
    def test_finds_header_at_row_0(self):
        df = pd.DataFrame([
            ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
            [1, "Brick Work 4.5 thick", 100, "Sft", 315, 31500],
        ])
        assert find_header_row(df) == 0

    def test_finds_header_at_row_3(self):
        df = pd.DataFrame([
            ["Project Name: ABC Tower", None, None, None, None, None],
            ["Client: XYZ Builders", None, None, None, None, None],
            [None, None, None, None, None, None],
            ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"],
            [1, "Gypsum ceiling 12mm", 500, "Sft", 450, 225000],
        ])
        assert find_header_row(df) == 3

    def test_returns_none_when_no_header(self):
        df = pd.DataFrame([[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]])
        assert find_header_row(df) is None

    def test_handles_alternate_description_alias(self):
        df = pd.DataFrame([
            ["Sr.", "Particulars", "Qty", "Unit", "Rate"],
            [1, "Tile flooring", 200, "Sft", 850],
        ])
        assert find_header_row(df) == 0

    def test_empty_dataframe_returns_none(self):
        df = pd.DataFrame()
        assert find_header_row(df) is None
