"""
RateIQ BOQ Parser Module
Read and clean Excel BOQ files.
Converts raw Excel → clean BOQChunk objects.
"""

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .models import BOQChunk

logger = logging.getLogger(__name__)

# ── Column name aliases ──────────────────────────────────────────────────────
_SNO_ALIASES = {
    "s.no.",
    "s.no",
    "sno",
    "s/no",
    "sr",
    "sr.",
    "sr.no",
    "item",
    "no.",
    "no",
}
_DESC_ALIASES = {
    "description",
    "desc",
    "particulars",
    "item description",
    "description of work",
    "work description",
}
_QTY_ALIASES = {"qty", "quantity", "quantities"}
_UNIT_ALIASES = {"unit", "units", "uom", "u/m"}
_RATE_ALIASES = {"rate", "rates", "unit rate", "unit price", "price"}
_AMOUNT_ALIASES = {"amount", "total", "total amount", "amt", "value"}

# ── Unit normalization map ───────────────────────────────────────────────────
_UNIT_MAP: dict[str, str] = {
    "sft": "sft",
    "sqft": "sft",
    "sq.ft": "sft",
    "sq ft": "sft",
    "sq.ft.": "sft",
    "sqf": "sft",
    "rft": "rft",
    "runnft": "rft",
    "r.ft": "rft",
    "nos": "nos",
    "no": "nos",
    "no.": "nos",
    "number": "nos",
    "numbers": "nos",
    "ls": "ls",
    "l.s": "ls",
    "lump sum": "ls",
    "lumpsum": "ls",
    "l/s": "ls",
    "kg": "kg",
    "kgs": "kg",
    "ton": "ton",
    "tons": "ton",
    "tonne": "ton",
    "lb": "lb",
    "lbs": "lb",
    "mtr": "mtr",
    "m": "mtr",
    "meter": "mtr",
    "metre": "mtr",
    "rm": "mtr",
    "cft": "cft",
    "cuft": "cft",
    "cu.ft": "cft",
    "point": "point",
    "points": "point",
    "set": "set",
    "sets": "set",
    "pair": "pair",
    "pairs": "pair",
    "day": "day",
    "days": "day",
    "month": "month",
    "months": "month",
}


def find_header_row(df: pd.DataFrame) -> int | None:
    """
    INPUT:  pandas DataFrame — raw Excel sheet read with header=None
    OUTPUT: int — row index where actual column headers are (S.No, Description, Rate)
            None if not found after scanning first 20 rows

    EXAMPLE:
        >>> input:  DataFrame where row 3 contains ["S.No", "Description", "Qty", ...]
        >>> output: 3
    """
    try:
        for i in range(min(20, len(df))):
            row_vals = [
                str(v).strip().lower() for v in df.iloc[i].tolist() if pd.notna(v)
            ]
            has_sno = any(v in _SNO_ALIASES for v in row_vals)
            has_desc = any(any(alias in v for alias in _DESC_ALIASES) for v in row_vals)
            has_rate = any(v in _RATE_ALIASES for v in row_vals)
            if has_desc and (has_sno or has_rate):
                return i
        return None
    except Exception as exc:
        logger.error("find_header_row failed: %s", exc)
        return None


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    INPUT:  pandas DataFrame — sheet read with detected header row
    OUTPUT: pandas DataFrame — same data but columns renamed to standard:
            SNO, DESCRIPTION, QTY, UNIT, RATE, AMOUNT

    EXAMPLE:
        >>> input:  DataFrame with columns ["S.No", "Particulars", "Qty", "Sft", "Rate"]
        >>> output: DataFrame with columns ["SNO", "DESCRIPTION", "QTY", "UNIT", "RATE"]
    """
    try:
        rename_map: dict[str, str] = {}
        for col in df.columns:
            col_lower = str(col).strip().lower()
            if col_lower in _SNO_ALIASES:
                rename_map[col] = "SNO"
            elif any(alias in col_lower for alias in _DESC_ALIASES):
                rename_map[col] = "DESCRIPTION"
            elif col_lower in _QTY_ALIASES:
                rename_map[col] = "QTY"
            elif col_lower in _UNIT_ALIASES:
                rename_map[col] = "UNIT"
            elif col_lower in _RATE_ALIASES and "RATE" not in rename_map.values():
                rename_map[col] = "RATE"
            elif col_lower in _AMOUNT_ALIASES:
                rename_map[col] = "AMOUNT"

        df = df.rename(columns=rename_map)

        # Ensure mandatory columns exist
        for required in ("DESCRIPTION", "RATE"):
            if required not in df.columns:
                df[required] = np.nan

        return df
    except Exception as exc:
        logger.error("normalize_columns failed: %s", exc)
        return df


def classify_row(row: pd.Series, sno_col: str, rate_col: str, desc_col: str) -> str:
    """
    INPUT:  one row from DataFrame + column name strings for SNO, RATE, DESC
    OUTPUT: string — one of: "line_item", "section_header",
                             "spec_detail", "empty"

    EXAMPLE:
        >>> input:  row with S.No=1, Description="Brick Work", Rate=315.0
        >>> output: "line_item"

        >>> input:  row with S.No=NaN, Description="SECTION A - BRICKWORK", Rate=NaN
        >>> output: "section_header"
    """
    try:
        desc = str(row.get(desc_col, "")).strip()
        rate_val = row.get(rate_col, np.nan)
        sno_val = row.get(sno_col, np.nan)

        all_empty = all(pd.isna(v) or str(v).strip() == "" for v in row.tolist())
        if all_empty:
            return "empty"

        has_rate = pd.notna(rate_val) and str(rate_val).strip() not in ("", "nan")
        has_sno = pd.notna(sno_val) and str(sno_val).strip() not in ("", "nan")

        if has_rate and len(desc) > 3:
            return "line_item"

        if not has_rate and len(desc) > 5:
            upper_ratio = sum(1 for c in desc if c.isupper()) / max(len(desc), 1)
            if upper_ratio > 0.5 or desc.isupper():
                return "section_header"
            if has_sno:
                return "spec_detail"
            return "section_header"

        return "empty"
    except Exception as exc:
        logger.error("classify_row failed: %s", exc)
        return "empty"


def normalize_unit(unit: str) -> str:
    """
    INPUT:  string — raw unit from Excel e.g. "Sft", "SFT", "Sft.", "NOS"
    OUTPUT: string — normalized lowercase: "sft", "nos", "rft", "ls", etc.
            Returns "unit" if cannot be normalized.

    EXAMPLE:
        >>> input:  "Sft."
        >>> output: "sft"

        >>> input:  "SQFT"
        >>> output: "sft"
    """
    try:
        if not unit or pd.isna(unit):
            return "unit"
        cleaned = str(unit).strip().lower().rstrip(".")
        if cleaned in _UNIT_MAP:
            return _UNIT_MAP[cleaned]
        # Fuzzy partial match
        for alias, normalized in _UNIT_MAP.items():
            if alias in cleaned or cleaned in alias:
                return normalized
        return cleaned if cleaned else "unit"
    except Exception as exc:
        logger.error("normalize_unit failed: %s", exc)
        return "unit"


def clean_rate(val) -> float | None:
    """
    INPUT:  any — raw cell value e.g. "Rs. 1,35,000" or 315.0 or "OFM" or NaN
    OUTPUT: float — cleaned numeric rate, or None if not parseable

    EXAMPLE:
        >>> input:  "Rs. 1,35,000"
        >>> output: 135000.0

        >>> input:  "OFM"
        >>> output: None
    """
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        if isinstance(val, (int, float)):
            return float(val) if val > 0 else None
        text = str(val).strip()
        if not text or text.lower() in ("nan", "n/a", "ofm", "-", "", "n.a", "tbd"):
            return None
        # Remove currency symbols and commas
        cleaned = re.sub(r"[rR][sS]\.?\s*", "", text)
        cleaned = cleaned.replace(",", "").strip()
        # Extract first numeric sequence
        match = re.search(r"(\d+\.?\d*)", cleaned)
        if match:
            rate = float(match.group(1))
            return rate if rate > 0 else None
        return None
    except Exception as exc:
        logger.error("clean_rate failed for value '%s': %s", val, exc)
        return None


def parse_boq_file(filepath: Path) -> tuple[list[dict], str | None]:
    """
    INPUT:  Path — path to one .xlsx or .xls file
    OUTPUT: tuple of:
            - list of dicts (raw row data, not yet BOQChunk)
            - error string or None if success

    EXAMPLE:
        >>> input:  Path("data/raw/BOQ-01.xlsx")
        >>> output: ([{"description_short": "Brick Work", "rate": 315.0, ...}], None)
    """
    rows: list[dict] = []
    try:
        # Detect engine
        engine = "xlrd" if str(filepath).endswith(".xls") else "openpyxl"
        xl = pd.ExcelFile(filepath, engine=engine)

        for sheet_name in xl.sheet_names:
            try:
                raw_df = pd.read_excel(
                    filepath, sheet_name=sheet_name, header=None, engine=engine
                )
                if raw_df.empty or len(raw_df) < 3:
                    continue

                header_row = find_header_row(raw_df)
                if header_row is None:
                    logger.debug(
                        "No header found in %s / %s", filepath.name, sheet_name
                    )
                    continue

                df = pd.read_excel(
                    filepath, sheet_name=sheet_name, header=header_row, engine=engine
                )
                df = normalize_columns(df)

                sno_col = "SNO" if "SNO" in df.columns else df.columns[0]
                desc_col = (
                    "DESCRIPTION" if "DESCRIPTION" in df.columns else df.columns[1]
                )
                rate_col = "RATE" if "RATE" in df.columns else "RATE"
                unit_col = "UNIT" if "UNIT" in df.columns else None
                qty_col = "QTY" if "QTY" in df.columns else None

                current_section = ""

                for _, row in df.iterrows():
                    row_type = classify_row(row, sno_col, rate_col, desc_col)

                    if row_type == "section_header":
                        current_section = str(row.get(desc_col, "")).strip()
                        continue

                    if row_type != "line_item":
                        continue

                    desc = str(row.get(desc_col, "")).strip()
                    rate = clean_rate(row.get(rate_col))
                    if not desc or rate is None:
                        continue

                    unit_raw = str(row.get(unit_col, "")) if unit_col else ""
                    qty_raw = str(row.get(qty_col, "")) if qty_col else ""
                    unit = normalize_unit(unit_raw)

                    rows.append(
                        {
                            "description_short": desc[:200],
                            "description_full": desc,
                            "section_title": current_section,
                            "rate": rate,
                            "unit_norm": unit,
                            "qty": qty_raw,
                            "source_file": filepath.name,
                            "sheet_name": str(sheet_name),
                            "rate_per_unit_label": f"Rs. {rate:,.0f} per {unit}",
                        }
                    )

            except Exception as sheet_exc:
                logger.warning(
                    "Sheet '%s' in %s failed: %s", sheet_name, filepath.name, sheet_exc
                )
                continue

        return rows, None

    except Exception as exc:
        err = f"{filepath.name}: {exc}"
        logger.error("parse_boq_file failed: %s", err)
        return [], err


def load_all_chunks(chunks_csv: Path) -> list[BOQChunk]:
    """
    INPUT:  Path — path to data/processed/boq_chunks.csv
    OUTPUT: list[BOQChunk] — all 1429 processed chunks ready for use

    EXAMPLE:
        >>> input:  Path("data/processed/boq_chunks.csv")
        >>> output: [BOQChunk(description_short="BRICKWORK 4 1/2\"", rate=315.0, ...), ...]
    """
    try:
        df = pd.read_csv(chunks_csv)
        chunks: list[BOQChunk] = []

        for _, row in df.iterrows():
            try:
                chunk = BOQChunk(
                    chunk_id=str(row.get("chunk_id", "")),
                    description_short=str(row.get("description_short", "")),
                    description_full=str(row.get("description_full", "")),
                    section_title=str(row.get("section_title", "")),
                    work_category=str(row.get("work_category", "")),
                    rate=float(row.get("rate", 0.0)),
                    unit_norm=str(row.get("unit_norm", "unit")),
                    qty=str(row.get("qty", "")),
                    source_file=str(row.get("source_file", "")),
                    sheet_name=str(row.get("sheet_name", "")),
                    rate_per_unit_label=str(row.get("rate_per_unit_label", "")),
                    embedding_text=str(row.get("embedding_text", "")),
                )
                chunks.append(chunk)
            except Exception as row_exc:
                logger.warning("Skipping malformed CSV row: %s", row_exc)
                continue

        logger.info("Loaded %d BOQChunks from %s", len(chunks), chunks_csv)
        return chunks

    except Exception as exc:
        logger.error("load_all_chunks failed: %s", exc)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from .config import settings

    chunks = load_all_chunks(settings.CHUNKS_CSV)
    logger.info("Smoke test: loaded %d chunks", len(chunks))
    if chunks:
        c = chunks[0]
        logger.info(
            "First chunk: %s | rate=%s | unit=%s",
            c.description_short,
            c.rate,
            c.unit_norm,
        )
