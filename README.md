# BOQ RateIQ

**Automatically fill missing rates in Bill of Quantities Excel files using AI.**

Upload a BOQ with empty rate columns → get back a fully priced BOQ with suggested rates, confidence scores, and sources — in seconds per item.

---

## What Is This?

When construction projects go to tender, estimators manually look up rates for hundreds of line items (brick work, gypsum ceiling, AC units, electrical points, etc.). This takes days and is prone to error.

**BOQ RateIQ** does it automatically:

1. You upload an Excel BOQ with empty rate cells
2. The AI agent classifies each item, searches your historical tender database, and checks live web prices
3. It returns the filled BOQ with a suggested rate, confidence level (high / medium / low), and a plain-English explanation citing its sources

---

## How It Works (Plain English)

```
Your BOQ Excel
      ↓
[Classify Item]   → What kind of work is this? (civil, HVAC, electrical...)
      ↓
[Search History]  → Find similar items from past tenders (1,429 BOQ chunks)
      ↓
[Get Market Rate] → Search current prices online via Tavily web search
      ↓
[Compare & Decide] → Is the historical rate confirmed by the market? Gap too large?
      ↓
[Write Explanation] → "Based on BOQ-01.xlsx (Rs.315/sft) confirmed by market at Rs.340/sft..."
      ↓
Filled BOQ Excel with SUGGESTED_RATE, CONFIDENCE, EXPLANATION, SOURCES
```

All rates are **per-unit** — if Qty=100 Sft and Rate=315, that means Rs.315 per Sft, not Rs.31,500 total.

---

## Key Features

| Feature | Detail |
|---|---|
| Hybrid search | BM25 keyword + Qdrant vector search + cross-encoder reranking |
| Market validation | Tavily web search with 3 fallback queries, Claude extraction |
| Gap analysis | CONFIRMED / MINOR_GAP / MAJOR_GAP vs historical baseline |
| Cost optimized | Anthropic prompt caching (~50% LLM cost reduction per batch) |
| Redis cache | Market rates cached 7 days — same item never searched twice |
| Circuit breaker | If Tavily fails 3x in a row, skips market search gracefully |
| API guards | Max 10 MB file, max 50 empty-rate rows per upload |
| Eval harness | 286-item holdout set, ±15% accuracy measurement |

---

## Quick Start

### Prerequisites

- Docker (for Qdrant + PostgreSQL + Redis)
- Python 3.11+
- API keys: Anthropic, Tavily

### 1. Clone and install

```bash
git clone <repo-url>
cd boq_rateiq
cp .env.example .env          # fill in your API keys
uv sync                       # install all dependencies
```

### 2. Start services

```bash
docker compose up -d          # starts Qdrant + PostgreSQL + Redis
```

### 3. Start the API

```bash
uvicorn rateiq.api:app --host 0.0.0.0 --port 8000
```

### 4. Fill a BOQ

```bash
curl -X POST http://localhost:8000/fill-boq \
     -F "file=@your_boq.xlsx" \
     --output filled_boq.xlsx
```

---

## API Endpoints

### `POST /fill-boq`

Upload an Excel BOQ, get back a filled Excel file.

- **Input:** `.xlsx` file — columns: Description, Qty, Unit, Rate (Rate column can be empty)
- **Output:** Same file + added columns: SUGGESTED_RATE, CONFIDENCE, EXPLANATION, SOURCES, MARKET_SOURCE, GAP_PCT, GAP_VERDICT
- **Limits:** Max 10 MB, max 50 empty-rate rows per upload

```bash
curl -X POST http://localhost:8000/fill-boq \
     -F "file=@test_boq.xlsx" \
     --output filled.xlsx
```

### `POST /rate-item`

Get a rate for a single item (JSON in, JSON out).

```bash
curl -X POST http://localhost:8000/rate-item \
     -H "Content-Type: application/json" \
     -d '{"description": "Brick Work 4.5 inch thick", "qty": "100", "unit": "Sft"}'
```

Response:

```json
{
  "suggested_rate": 320.0,
  "confidence": "high",
  "explanation": "Based on 4 historical BOQ files at Rs.315/sft (avg). Market confirmed at Rs.340/sft — within 7.3% of historical.",
  "gap_verdict": "CONFIRMED",
  "market_rate": 340.0,
  "market_source": "niazibricks.com.pk"
}
```

### `GET /health`

Check if the server is running and the agent is loaded.

---

## Environment Variables

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...

QDRANT_URL=http://localhost:6333
POSTGRES_URL=postgresql://user:pass@localhost:5432/rateiq
REDIS_URL=redis://localhost:6379

# Optional — fallback if Anthropic credits run out
OPENAI_API_KEY=sk-...
USE_OPENAI_FALLBACK=false
```

---

## Project Structure

```
boq_rateiq/
├── src/rateiq/
│   ├── agent.py            # LangGraph pipeline (5 nodes: classify → history → market → gap → output)
│   ├── api.py              # FastAPI endpoints (/fill-boq, /rate-item, /health)
│   ├── cache.py            # Redis market rate cache (7-day TTL, silent fallthrough)
│   ├── circuit_breaker.py  # Protects against Tavily outages
│   ├── gap_detector.py     # CONFIRMED / MINOR_GAP / MAJOR_GAP logic
│   ├── market_search.py    # Tavily search + Claude rate extraction (3 fallback queries)
│   ├── parser.py           # Excel BOQ parser (handles messy Pakistani BOQ formats)
│   ├── searcher.py         # Hybrid BM25 + Qdrant + cross-encoder reranker
│   └── validators.py       # Rate sanity bounds per category/unit pair
├── scripts/
│   └── build_holdout.py    # One-time 80/20 eval split (run ONCE only)
├── tests/
│   ├── unit/               # 68 fast unit tests — no services needed, ~4 seconds
│   ├── eval/               # Accuracy eval against 286-item holdout set (~15 min)
│   └── integration/        # API smoke tests (health, guards, rate-item structure)
└── data/processed/
    └── boq_chunks.csv      # 1,429 historical BOQ rate records
```

---

## Running Tests

```bash
# Fast unit tests — no services needed (~4 seconds)
pytest tests/unit/ -v

# API smoke tests — needs services running
pytest tests/integration/ -v

# Full accuracy eval — needs Qdrant + Postgres + API keys (~15 minutes)
pytest tests/eval/test_accuracy.py -v -s
```

The accuracy eval reports per-category results and fails the test suite if overall accuracy drops below 75% (±15% tolerance of ground truth).

---

## Historical Data

The system searches across **1,429 BOQ line items** from real Pakistani construction tender files:

| Category | Items |
|---|---|
| Civil / Masonry | 648 |
| Special Works (joinery, waterproofing, etc.) | 295 |
| Electrical & ELV | 246 |
| Variation Orders | 158 |
| Other | 37 |
| HVAC | 26 |
| Fire Fighting | 19 |

---

## Tech Stack

- **LangGraph** — 5-node pipeline orchestration
- **Anthropic Claude** — item classification + explanation writing (with prompt caching)
- **Qdrant** — vector database for semantic similarity search
- **PostgreSQL** — historical rate storage and statistics
- **Tavily** — live web search for current market prices
- **FastAPI** — REST API server
- **Redis** — cross-session market rate cache
- **sentence-transformers** — cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`)
- **rank-bm25** — keyword search

---

## Accuracy Measurement

A permanent 286-item holdout set (20% stratified by category, never seen during training) is used to measure accuracy:

- **Target:** 75% of items within ±15% of ground truth rate
- **Run eval:** `pytest tests/eval/test_accuracy.py -v -s`
- **Results:** Saved automatically to `tests/eval/results/YYYY-MM-DD.csv`
