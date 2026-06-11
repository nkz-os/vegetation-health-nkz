#!/bin/sh
# Run the backend suite with per-file process isolation.
#
# Several test modules stub sys.modules at import time (rasterio, scipy,
# app.tasks, app.database...) without cleanup, and once app.main is imported
# against those stubs it cannot be restored in-process. Running each file in
# its own pytest process is the only reliable isolation. All files green
# individually == suite green.
set -e
cd "$(dirname "$0")"
for f in tests/test_*.py; do
  echo "=== $f"
  python -m pytest "$f" -q --tb=short
done
echo "All test files passed."
