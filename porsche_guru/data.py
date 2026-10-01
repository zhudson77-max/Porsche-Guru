"""Loads the Porsche model table (CSV) and answers filtered queries against it.

The table schema is not fixed: whatever columns the CSV has become queryable.
Numeric-looking values ("$112,000", "443 hp", "3.2s") are compared numerically.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")

OPS = ("eq", "neq", "contains", "gt", "gte", "lt", "lte")


def to_number(value: str) -> float | None:
    """Extract the first number from a cell like "$112,000" or "443 hp"."""
    match = _NUMBER.search(value or "")
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


class ModelTable:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self.path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            self.columns = [c.strip() for c in (reader.fieldnames or [])]
            self.rows = [
                {k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in reader
            ]

    def _resolve_column(self, name: str) -> str:
        """Match a column name case-insensitively, ignoring spaces/underscores."""
        norm = lambda s: re.sub(r"[\s_\-]", "", s).lower()
        for col in self.columns:
            if norm(col) == norm(name):
                return col
        raise KeyError(f"Unknown column {name!r}. Available: {', '.join(self.columns)}")

    def describe(self, max_examples: int = 8) -> dict:
        """Column names plus a few distinct example values for each."""
        summary = {}
        for col in self.columns:
            seen: list[str] = []
            for row in self.rows:
                v = row.get(col, "")
                if v and v not in seen:
                    seen.append(v)
                if len(seen) >= max_examples:
                    break
            summary[col] = seen
        return {"row_count": len(self.rows), "columns": summary}

    @staticmethod
    def _matches(cell: str, op: str, value: str) -> bool:
        if op == "contains":
            return value.lower() in cell.lower()
        if op in ("eq", "neq"):
            a, b = to_number(cell), to_number(value)
            # Compare numerically only when both sides are purely numeric-ish.
            if a is not None and b is not None and not re.search(r"[A-Za-z]", value):
                equal = a == b
            else:
                equal = cell.lower() == value.lower()
            return equal if op == "eq" else not equal
        a, b = to_number(cell), to_number(value)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]

    def query(
        self,
        filters: list[dict] | None = None,
        columns: list[str] | None = None,
        sort_by: str | None = None,
        descending: bool = False,
        limit: int = 25,
    ) -> dict:
        rows = self.rows
        for f in filters or []:
            op = f.get("op", "contains")
            if op not in OPS:
                raise ValueError(f"Unknown op {op!r}. Use one of {OPS}")
            col = self._resolve_column(f["column"])
            rows = [r for r in rows if self._matches(r.get(col, ""), op, str(f["value"]))]

        if sort_by:
            col = self._resolve_column(sort_by)

            def key(r):
                n = to_number(r.get(col, ""))
                # Rows without a numeric value sort last; fall back to text order.
                return (n is None, n if n is not None else 0, r.get(col, "").lower())

            rows = sorted(rows, key=key, reverse=descending)

        if columns:
            keep = [self._resolve_column(c) for c in columns]
            rows = [{c: r.get(c, "") for c in keep} for r in rows]

        return {"total_matches": len(rows), "rows": rows[: max(1, limit)]}
