"""Interactive command-line chat with Porsche Guru.

Usage:
    python -m porsche_guru [--data path/to/models.csv]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anthropic

from .data import ModelTable


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=value lines from a .env file. Variables already set in the shell win."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# Load before importing the agent, which reads PORSCHE_GURU_* settings at import time.
load_dotenv()

from .agent import PorscheGuru  # noqa: E402

DEFAULT_DATA = os.environ.get(
    "PORSCHE_GURU_DATA", str(Path(__file__).resolve().parent.parent / "data" / "porsche_911.csv")
)

BANNER = """Porsche Guru — ask about models in your table, or have me hunt down listings.
  e.g. "Compare the 992 GT3 and the 991.2 GT3 from my table"
       "Find 992 GT3 manuals under $230k with less than 10k miles"
Type 'reset' to start a new conversation, 'quit' to exit.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="porsche-guru")
    parser.add_argument("--data", default=DEFAULT_DATA, help="CSV file with Porsche model info")
    args = parser.parse_args(argv)

    try:
        table = ModelTable(args.data)
    except FileNotFoundError:
        print(f"Model table not found: {args.data}", file=sys.stderr)
        return 1

    guru = PorscheGuru(table, on_activity=lambda msg: print(f"  · {msg}", flush=True))
    print(BANNER)
    print(f"Loaded {len(table.rows)} models from {args.data}\n")

    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            return 0
        if question.lower() == "reset":
            guru.messages.clear()
            print("Conversation cleared.\n")
            continue

        try:
            reply = guru.ask(question)
        except anthropic.AuthenticationError:
            print("Authentication failed: set ANTHROPIC_API_KEY.", file=sys.stderr)
            return 1
        except anthropic.RateLimitError:
            print("Rate limited by the API; wait a moment and try again.\n", file=sys.stderr)
            continue
        except anthropic.APIStatusError as e:
            print(f"API error ({e.status_code}): {e.message}\n", file=sys.stderr)
            continue
        except anthropic.APIConnectionError:
            print("Couldn't reach the Anthropic API; check your connection.\n", file=sys.stderr)
            continue

        print(f"\nguru> {reply.text}\n")
        if reply.sources:
            print("Sources:")
            for title, url in reply.sources:
                print(f"  - {title}: {url}")
            print()


if __name__ == "__main__":
    sys.exit(main())
