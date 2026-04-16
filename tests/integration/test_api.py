"""
Integration smoke tests for FastAPI endpoints.
REQUIRES: All services running (Qdrant, Postgres, Redis, FastAPI on port 8000).
          openpyxl must be installed (already a project dependency).
RUNTIME:  < 2 minutes.
RUN:      pytest tests/integration/test_api.py -v
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient — no live server needed, but services (Qdrant/Postgres) required."""
    from fastapi.testclient import TestClient
    from rateiq.api import app
    with TestClient(app) as c:
        yield c


def _make_small_excel(n_rows: int = 5) -> bytes:
    """Build an in-memory BOQ Excel with n_rows empty-rate rows."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["S.No", "Description", "Qty", "Unit", "Rate"])
    items = [
        ("Brick Work 4.5 Thick", "100", "Sft"),
        ("Gypsum False Ceiling 12mm", "200", "Sft"),
        ("Split AC 1.5 Ton Installation", "3", "Nos"),
        ("Ceramic Floor Tile 12x12", "150", "Sft"),
        ("Light Point Wiring 2.5mm", "10", "Nos"),
    ]
    for i, (desc, qty, unit) in enumerate(items[:n_rows], start=1):
        ws.append([i, desc, qty, unit, None])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "agent_ready" in data


class TestFillBoqGuards:
    def test_rejects_oversized_file(self, client):
        """11 MB upload must be rejected with 413 before any processing."""
        big_content = b"x" * (11 * 1024 * 1024)
        resp = client.post(
            "/fill-boq",
            files={
                "file": (
                    "big.xlsx",
                    big_content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert resp.status_code == 413

    def test_accepts_valid_5_row_file(self, client):
        """A 5-row file within limits should not be rejected by the guards."""
        excel_bytes = _make_small_excel(5)
        resp = client.post(
            "/fill-boq",
            files={
                "file": (
                    "test.xlsx",
                    excel_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        # 200 = processed, 500 = DB not connected in test env — either is not a guard rejection
        assert resp.status_code in (200, 500)

    def test_rejects_non_excel_file(self, client):
        """Non-Excel files must be rejected with 400."""
        resp = client.post(
            "/fill-boq",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 400


class TestRateItemEndpoint:
    def test_rate_item_returns_expected_structure(self, client):
        """Successful rate-item call must include required response fields."""
        resp = client.post(
            "/rate-item",
            json={"description": "Brick Work 4.5 inch thick", "qty": "100", "unit": "Sft"},
        )
        # 200 = agent ready, 503 = agent not initialized in test env
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert "suggested_rate" in data
            assert "confidence" in data
            assert "explanation" in data
            assert "gap_verdict" in data

    def test_rate_item_rejects_empty_description(self, client):
        """Empty description must be rejected with 400."""
        resp = client.post(
            "/rate-item",
            json={"description": "", "qty": "100", "unit": "Sft"},
        )
        assert resp.status_code == 400
