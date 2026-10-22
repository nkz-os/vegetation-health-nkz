#!/usr/bin/env python3
"""
Validate Phase 5 DataHub adapter response: Arrow IPC stream.

Usage:
  curl -s -H "Authorization: Bearer $JWT" \
    "http://localhost:8000/api/timeseries/entities/ENTITY_ID/data?attribute=NDVI&start_time=2025-01-01T00:00:00Z&end_time=2026-12-31T23:59:59Z&format=arrow" \
    -o out.arrow
  python scripts/validate_arrow_timeseries_response.py out.arrow

  Or from stdin:
  curl -s ... | python scripts/validate_arrow_timeseries_response.py -

Exit code: 0 if schema and types are correct, 1 otherwise.
"""

import sys
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.ipc
except ImportError:
    print("pyarrow required: pip install pyarrow", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_arrow_timeseries_response.py <file.arrow|->", file=sys.stderr)
        return 1

    src = sys.argv[1]
    if src == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(src).read_bytes()

    if not data:
        print("Empty input (expected Arrow IPC stream or 204 No Content).", file=sys.stderr)
        return 1

    try:
        reader = pa.ipc.open_stream(data)
        table = reader.read_all()
    except Exception as e:
        print(f"Failed to decode Arrow IPC: {e}", file=sys.stderr)
        return 1

    # Contract: timestamp (float64), value (float64)
    if "timestamp" not in table.column_names or "value" not in table.column_names:
        print(f"Missing columns. Got: {table.column_names}", file=sys.stderr)
        return 1

    ts_col = table.column("timestamp")
    val_col = table.column("value")

    if ts_col.type != pa.float64():
        print(f"timestamp must be float64, got {ts_col.type}", file=sys.stderr)
        return 1
    if val_col.type != pa.float64():
        print(f"value must be float64, got {val_col.type}", file=sys.stderr)
        return 1

    n = table.num_rows
    print(f"OK: schema valid, {n} rows")

    if n > 0:
        ts_min = float(ts_col[0])
        ts_max = float(ts_col[n - 1])
        # Epoch seconds: 1e9 = 2001-09-09, 1.7e9 = 2023-11-14
        print(f"  timestamp range: {ts_min} .. {ts_max} (epoch seconds)")
        print(f"  value sample (first 3): {[round(float(val_col[i]), 4) for i in range(min(3, n))]}")
        # Sanity: ascending timestamp
        for i in range(1, n):
            if float(ts_col[i]) < float(ts_col[i - 1]):
                print("  ERROR: timestamps not ascending", file=sys.stderr)
                return 1
        print("  timestamps ascending: OK")

    return 0


if __name__ == "__main__":
    sys.exit(main())
