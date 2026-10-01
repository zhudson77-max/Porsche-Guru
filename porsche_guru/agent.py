"""The Porsche Guru agent: Claude + your model table + live web search for listings."""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from .data import OPS, ModelTable

MODEL = os.environ.get("PORSCHE_GURU_MODEL", "claude-opus-5-5")
# Opus 5.5 defaults to "medium"; listing research benefits from a bit more digging.
EFFORT = os.environ.get("PORSCHE_GURU_EFFORT", "high")
MAX_TOOL_ROUNDS = 25

SYSTEM_PROMPT = """You are Porsche Guru, an expert assistant for Porsche buyers and enthusiasts.

You have two sources of information:
1. The user's Porsche model table, through the describe_model_table and query_model_table tools.
   Use it first for factual model specs (engine, horsepower, 0-60, weight, and so on). Call
   describe_model_table once before your first query so you know the exact column names. When
   you cite specs from the table, say they come from the user's table.

   About the table: it covers 911 variants from 1964 to the 992, sourced from auto-data.net (the
   `link` column is each row's source page). Generation codes such as 992, 991 II, 997 and 964
   appear in the `generation` column; the trim, displacement and gearbox are in the `engine`
   column (e.g. "GT3 4.0 (510 Hp) PDK"), and most trims have separate manual and PDK rows.
   Values are metric: speeds in km/h, torque in Nm, weights in kg, dimensions in mm, and fuel
   consumption in L/100 km. Convert to US units for the user (mph, lb-ft, lb, inches, mpg),
   keeping the metric figure in parentheses where helpful. The table has no prices, so get
   prices from the web. Its data has occasional errors and gaps (some blank cells, a few
   wrong drivetrain or layout values); if a value looks implausible, say so rather than
   repeating it.
2. The live web, through web_search and web_fetch, for current for-sale listings, market
   prices, and anything the table doesn't cover.

When the user asks about listings:
- Pin down model, generation/years, key specs (transmission, color, mileage, options) and price
  range from their request. Ask a short clarifying question only if the request is too vague to
  search usefully.
- Search dedicated car marketplaces such as Porsche Finder (finder.porsche.com), Bring a Trailer,
  Cars & Bids, PCARMARKET, Rennlist classifieds, CarGurus, Autotrader, Cars.com and
  DuPont Registry. Fetch listing pages when search snippets lack price, mileage or location.
- Present results as a compact table: year/model, key specs, mileage, price (or current bid /
  sold price for auctions, labelled as such), location, and the listing URL.
- Only report listings you actually found in this session, with the link. Never invent a listing,
  price or URL. Say plainly when a listing's status, price or details couldn't be verified, and
  note that listings change quickly.
- Flag anything notable: price versus the typical market, rare options, auction end dates, and
  red flags in a listing.

Today's date is {today}."""

TOOLS = [
    {
        "name": "describe_model_table",
        "description": (
            "Return the Porsche model table's column names, row count, and example values for "
            "each column. Call this before query_model_table to learn the exact column names."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "query_model_table",
        "description": (
            "Look up rows in the user's Porsche model table. Every filter must match, so filters "
            "are combined with AND. Numeric operators (gt, gte, lt, lte) pull the number out of a "
            "cell, so '$122,095' and '388 hp' both compare as numbers. 'contains' is a "
            "case-insensitive substring match. To match any of several values, make separate calls. "
            "Rows are wide, so always list just the columns you need in `columns`."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "array",
                    "description": "Conditions on columns. Empty array returns all rows.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "op": {"type": "string", "enum": list(OPS)},
                            "value": {"type": "string"},
                        },
                        "required": ["column", "op", "value"],
                        "additionalProperties": False,
                    },
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columns to return. Empty array returns all columns.",
                },
                "sort_by": {"type": "string", "description": "Column to sort by, or empty string for none."},
                "descending": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Max rows to return (e.g. 25)."},
            },
            "required": ["filters", "columns", "sort_by", "descending", "limit"],
            "additionalProperties": False,
        },
    },
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10},
]


@dataclass
class Reply:
    text: str
    sources: list[tuple[str, str]] = field(default_factory=list)  # (title, url)


class PorscheGuru:
    def __init__(
        self,
        table: ModelTable,
        client: anthropic.Anthropic | None = None,
        on_activity: Callable[[str], None] | None = None,
    ):
        self.table = table
        self.client = client or anthropic.Anthropic()
        self.on_activity = on_activity or (lambda _msg: None)
        self.messages: list[dict] = []
        self.system = SYSTEM_PROMPT.format(today=dt.date.today().isoformat())

    def _run_tool(self, name: str, tool_input: dict) -> str:
        if name == "describe_model_table":
            self.on_activity("Reading model table columns")
            return json.dumps(self.table.describe())
        if name == "query_model_table":
            self.on_activity(f"Querying model table {tool_input.get('filters') or '(all rows)'}")
            result = self.table.query(
                filters=tool_input.get("filters"),
                columns=tool_input.get("columns") or None,
                sort_by=tool_input.get("sort_by") or None,
                descending=bool(tool_input.get("descending")),
                limit=int(tool_input.get("limit") or 25),
            )
            return json.dumps(result)
        raise ValueError(f"Unknown tool {name}")

    def _report_server_tools(self, content) -> None:
        for block in content:
            if block.type == "server_tool_use":
                if block.name == "web_search":
                    self.on_activity(f"Searching the web: {block.input.get('query', '')}")
                elif block.name == "web_fetch":
                    self.on_activity(f"Reading {block.input.get('url', '')}")

    def ask(self, question: str) -> Reply:
        self.messages.append({"role": "user", "content": question})
        turn_start = len(self.messages) - 1

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=self.system,
                tools=TOOLS,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": EFFORT},
                cache_control={"type": "ephemeral"},
                # On a safety-classifier decline, retry server-side on Anthropic's recommended model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )

            if response.stop_reason == "refusal":
                # Drop the unanswered turn so the conversation can carry on.
                del self.messages[turn_start:]
                return Reply("Sorry, I can't help with that request.")

            self._report_server_tools(response.content)
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                # Server-side web tools hit their iteration limit; re-sending resumes them.
                continue

            if response.stop_reason == "max_tokens":
                return self._reply_from(response.content, note="(Response was cut off at the length limit.)")

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                return self._reply_from(response.content)

            results = []
            for tu in tool_uses:
                try:
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": self._run_tool(tu.name, tu.input)})
                except (KeyError, ValueError) as e:
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(e), "is_error": True})
            self.messages.append({"role": "user", "content": results})

        return Reply("I hit my research step limit before finishing. Try narrowing the request.")

    @staticmethod
    def _reply_from(content, note: str = "") -> Reply:
        parts, sources, seen = [], [], set()
        for block in content:
            if block.type != "text":
                continue
            parts.append(block.text)
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append((getattr(c, "title", None) or url, url))
        text = "".join(parts).strip()
        if note:
            text = f"{text}\n\n{note}"
        return Reply(text, sources)
