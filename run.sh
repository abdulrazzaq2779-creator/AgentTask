#!/usr/bin/env bash
# FailureLoop one-command launcher: FastAPI backend + React frontend.
set -e
cd "$(dirname "$0")"

export PATH="/c/Program Files/nodejs:$PATH"   # Windows Git-Bash PATH quirk

echo "==> Installing Python deps (first run only)..."
python -m pip install -q -r requirements.txt 2>/dev/null || true

echo "==> Installing frontend deps (first run only)..."
(cd frontend && npm install --silent 2>/dev/null || npm install) &

echo "==> Starting API on http://localhost:8000 ..."
python -m uvicorn failureloop.api:app --port 8000 &
API_PID=$!
sleep 3

echo "==> Starting frontend on http://localhost:5173 ..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

trap "kill $API_PID $FRONTEND_PID 2>/dev/null" EXIT
echo ""
echo "  🔁 FailureLoop is running:"
echo "     Frontend  →  http://localhost:5173"
echo "     API docs  →  http://localhost:8000/docs"
echo ""
echo "  (Ctrl+C stops both)"
wait
