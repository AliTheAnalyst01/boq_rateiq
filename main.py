import signal
import os
import uvicorn
from pathlib import Path


def main():
    from src.rateiq.logging_config import setup_logging
    from src.rateiq.logging_config import get_logger

    # Setup production logging
    log_path = Path("logs/rateiq.log")
    setup_logging(log_file=log_path)
    logger = get_logger("main")

    if os.name == "nt":
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    logger.info("=" * 60)
    logger.info("Starting RateIQ Server")
    logger.info("=" * 60)

    uvicorn.run(
        "src.rateiq.api:app", host="0.0.0.0", port=8000, reload=True, log_level="info"
    )


if __name__ == "__main__":
    main()
