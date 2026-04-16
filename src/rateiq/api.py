from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import tempfile
import uuid
from pathlib import Path
import logging

from .pipeline import initialize_all, process_boq_file

logger = logging.getLogger(__name__)

app = FastAPI(
    title="RateIQ — BOQ Rate Suggestion API",
    description="Upload a BOQ Excel file, get rates filled automatically",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = None


@app.on_event("startup")
async def startup():
    """
    INPUT:  nothing — runs automatically when server starts
    OUTPUT: nothing — sets global agent variable
    Loads all models once: BM25, reranker, Qdrant connection.
    Takes ~10-15 seconds on first run.
    """
    global agent
    logger.info("Starting RateIQ API — loading models...")
    agent = initialize_all()
    logger.info("RateIQ API ready.")


@app.get("/health")
def health():
    """
    INPUT:  nothing
    OUTPUT: JSON {"status": "ok", "agent_ready": bool}
    Quick check that server and agent are running.
    """
    return {"status": "ok", "agent_ready": agent is not None, "version": "1.0.0"}


MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_EMPTY_ROWS = 50


@app.post("/fill-boq")
async def fill_boq(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    INPUT:  Excel file (.xlsx) uploaded via multipart form
            File must have columns: DESCRIPTION, QTY, UNIT
            RATE column can be empty — agent will fill it
            Max file size: 10 MB. Max empty-rate rows: 50.

    OUTPUT: Excel file (.xlsx) download with added columns:
            SUGGESTED_RATE  — PKR number recommended by agent
            CONFIDENCE      — high / medium / low
            EXPLANATION     — 2-3 sentence reason with sources
            SOURCES         — comma-separated past tender files
            MARKET_SOURCE   — website where market rate found
            MARKET_RATE     — current market rate if found
            GAP_PCT         — % difference hist vs market
            GAP_VERDICT     — CONFIRMED / MINOR_GAP / MAJOR_GAP

    EXAMPLE:
        curl -X POST http://localhost:8000/fill-boq \\
             -F "file=@test_boq_input.xlsx" \\
             --output filled_boq.xlsx
    """
    if agent is None:
        raise HTTPException(503, "Agent not ready. Wait and retry.")

    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Only .xlsx or .xls files accepted.")

    content = await file.read()

    # Guard 1: file size
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File too large: {len(content) / 1024 / 1024:.1f} MB. Maximum is 10 MB.",
        )

    tmp_input = Path(tempfile.mktemp(suffix=".xlsx"))
    tmp_output = Path(tempfile.mktemp(suffix=".xlsx"))

    try:
        tmp_input.write_bytes(content)

        # Guard 2: empty-row count — parse first, then count before running agent
        import pandas as pd
        try:
            preview_df = pd.read_excel(tmp_input, nrows=200)
            # Find RATE column (case-insensitive)
            rate_col = next(
                (c for c in preview_df.columns if str(c).strip().lower() in ("rate", "rates", "unit rate", "unit price")),
                None,
            )
            if rate_col:
                empty_count = int(preview_df[rate_col].isna().sum() + (preview_df[rate_col] == 0).sum())
                if empty_count > MAX_EMPTY_ROWS:
                    raise HTTPException(
                        422,
                        f"File has {empty_count} empty-rate rows. Maximum is {MAX_EMPTY_ROWS}. "
                        "Split the BOQ into smaller batches.",
                    )
        except HTTPException:
            raise
        except Exception:
            pass  # If preview fails, let the main pipeline handle it

        result = process_boq_file(
            input_path=tmp_input, output_path=tmp_output, agent=agent
        )

        if not tmp_output.exists():
            raise HTTPException(500, "Processing failed — no output file.")

        background_tasks.add_task(tmp_output.unlink, missing_ok=True)
        return FileResponse(
            path=str(tmp_output),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"rateiq_filled_{file.filename}",
            background=background_tasks,
            headers={
                "X-Rows-Total": str(result.get("total_rows", 0)),
                "X-Rows-Filled": str(result.get("filled", 0)),
                "X-Rows-Skipped": str(result.get("skipped", 0)),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"fill_boq error: {e}", exc_info=True)
        raise HTTPException(500, f"Processing error: {str(e)}")
    finally:
        if tmp_input.exists():
            tmp_input.unlink()
        # tmp_output cleaned up via background_tasks after FileResponse is sent


@app.post("/rate-item")
async def rate_item(payload: dict):
    """
    INPUT:  JSON body:
            {
              "description": "Providing and laying Brick Work 4.5 Thick...",
              "qty": "250",
              "unit": "Sft"
            }
    
    OUTPUT: JSON with full recommendation:
            {
              "suggested_rate": 320.0,
              "confidence": "medium",
              "explanation": "Based on BOQ-01.xlsx (Rs.315)...",
              "source_refs": ["BOQ - 01 .xlsx", "ID Works (G.F).xlsx"],
              "work_category": "civil_id",
              "market_rate": null,
              "market_source": null,
              "gap_pct": 0.0,
              "gap_verdict": "NO_MARKET_DATA"
            }
    
    EXAMPLE:
        curl -X POST http://localhost:8000/rate-item \
             -H "Content-Type: application/json" \
             -d '{"description": "Brick Work 4.5 Thick", 
                  "qty": "100", "unit": "Sft"}'
    """
    if agent is None:
        raise HTTPException(503, "Agent not ready.")

    description = payload.get("description", "").strip()
    if not description:
        raise HTTPException(400, "description is required.")

    qty = str(payload.get("qty", ""))
    unit = str(payload.get("unit", ""))

    try:
        rec = agent.run(description, qty, unit)

        return JSONResponse(
            {
                "suggested_rate": rec.suggested_rate,
                "confidence": rec.confidence,
                "explanation": rec.explanation,
                "source_refs": rec.source_refs,
                "work_category": rec.work_category,
                "search_keywords": rec.search_keywords,
                "market_rate": (
                    rec.market_rate.contractor_rate if rec.market_rate else None
                ),
                "market_source": (
                    rec.market_rate.source_name if rec.market_rate else None
                ),
                "market_source_url": (
                    rec.market_rate.source_url if rec.market_rate else None
                ),
                "market_raw_text": (
                    rec.market_rate.raw_price_text if rec.market_rate else None
                ),
                "gap_pct": (rec.gap_analysis.gap_pct if rec.gap_analysis else None),
                "gap_verdict": (rec.gap_analysis.verdict if rec.gap_analysis else None),
                "hist_source": (
                    rec.gap_analysis.hist_source if rec.gap_analysis else "unknown"
                ),
                "crag_verdict": (
                    rec.gap_analysis.crag_verdict if rec.gap_analysis else "unknown"
                ),
                "market_rejected": (
                    rec.gap_analysis.market_rejected if rec.gap_analysis else False
                ),
            }
        )

    except Exception as e:
        logger.error(f"rate_item error: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@app.get("/search")
async def search_items(q: str, limit: int = 10):
    """
    INPUT:  q string — description to search for in historical BOQ data
            limit int — max results to return (default 10)
    OUTPUT: JSON list of matched historical chunks with rates and source files

    EXAMPLE:
        curl "http://localhost:8000/search?q=brick+work+4.5+inch&limit=10"
    """
    if agent is None:
        raise HTTPException(503, "Agent not ready.")
    try:
        results = agent._searcher.search(query=q, top_k=min(limit, 20))
        return JSONResponse({
            "results": [
                {
                    "source_file": r.chunk.source_file,
                    "section_title": r.chunk.section_title,
                    "description": r.chunk.description_full or r.chunk.description_short,
                    "qty": r.chunk.qty,
                    "rate": r.chunk.rate,
                    "unit": r.chunk.unit_norm,
                    "reranker_score": round(r.reranker_score, 2),
                    "rrf_score": round(r.rrf_score, 4),
                    "ranking_method": r.ranking_method,
                }
                for r in results
                if r.chunk.rate and r.chunk.rate > 0   # skip unpriced section headers
            ]
        })
    except Exception as e:
        logger.error(f"search error: {e}", exc_info=True)
        raise HTTPException(500, str(e))


@app.post("/rate-batch")
async def rate_batch(payload: dict):
    """
    INPUT:  JSON body:
            {
              "items": [
                {"description": "...", "qty": "100", "unit": "Sft"},
                {"description": "...", "qty": "5",   "unit": "Nos"},
              ]
            }
            Max 50 items per request.

    OUTPUT: JSON list — one result per item (same order):
            {
              "results": [
                {"suggested_rate": 320.0, "confidence": "medium", ...},
                {"suggested_rate": 17000.0, "confidence": "medium", ...},
              ],
              "total": 2,
              "processed": 2,
              "errors": 0
            }
    """
    if agent is None:
        raise HTTPException(503, "Agent not ready.")

    items = payload.get("items", [])
    if not items:
        raise HTTPException(400, "items list is required.")
    if len(items) > 50:
        raise HTTPException(400, "Max 50 items per batch.")

    results = []
    errors = 0

    for item in items:
        try:
            rec = agent.run(
                item.get("description", ""),
                str(item.get("qty", "")),
                str(item.get("unit", "")),
            )
            results.append(
                {
                    "suggested_rate": rec.suggested_rate,
                    "confidence": rec.confidence,
                    "explanation": rec.explanation,
                    "source_refs": rec.source_refs,
                    "work_category": rec.work_category,
                    "market_rate": (
                        rec.market_rate.contractor_rate if rec.market_rate else None
                    ),
                    "market_source": (
                        rec.market_rate.source_name if rec.market_rate else None
                    ),
                    "gap_verdict": (
                        rec.gap_analysis.verdict if rec.gap_analysis else None
                    ),
                    "hist_source": (
                        rec.gap_analysis.hist_source if rec.gap_analysis else "unknown"
                    ),
                    "crag_verdict": (
                        rec.gap_analysis.crag_verdict if rec.gap_analysis else "unknown"
                    ),
                    "market_rejected": (
                        rec.gap_analysis.market_rejected if rec.gap_analysis else False
                    ),
                    "error": None,
                }
            )
        except Exception as e:
            errors += 1
            results.append(
                {
                    "suggested_rate": None,
                    "confidence": "low",
                    "explanation": f"Error: {str(e)}",
                    "source_refs": [],
                    "error": str(e),
                }
            )

    return JSONResponse(
        {
            "results": results,
            "total": len(items),
            "processed": len(items) - errors,
            "errors": errors,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "rateiq.api:app", host="0.0.0.0", port=8000, reload=False, log_level="info"
    )
