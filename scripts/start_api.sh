#!/bin/bash
# scripts/start_api.sh — Start the local FastAPI bridge
# Usage: bash scripts/start_api.sh

cd "$(dirname "$0")/.."
python3.14 -m uvicorn api.server:app --host 127.0.0.1 --port 8000 --reload
