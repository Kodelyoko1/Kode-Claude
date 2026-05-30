#!/usr/bin/env python3
"""FastAPI server for Media Buyer webhooks (Meta leadgen, Shopify orders, CAPI)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "media_buyer.ingestion:app",
        host=os.getenv("MB_HOST", "0.0.0.0"),
        port=int(os.getenv("MB_PORT", "8087")),
        log_level=os.getenv("MB_LOG_LEVEL", "info"),
        reload=os.getenv("MB_RELOAD", "").lower() in ("1", "true"),
    )
