"""Web interface for Porsche Guru.

Usage:
    python -m porsche_guru.web        # serves on $PORT (default 8080)

Each question runs as a background job that the page polls, so a long listing
search never holds a single HTTP request open long enough to hit a proxy timeout.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anthropic

from .cli import DEFAULT_DATA  # also loads .env before the agent reads its settings
from .agent import PorscheGuru
from .data import ModelTable

INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_bytes()
JOB_TTL_SECONDS = 3600
MAX_BODY_BYTES = 64 * 1024
# Strongly probes /health by default; the others cover common platform conventions.
HEALTH_PATHS = {"/health", "/healthz", "/ready"}


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.created = time.time()
        self.status = "running"  # running | done | error
        self.activity: list[str] = []
        self.result: dict | None = None
        self.error: str | None = None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "activity": self.activity,
            "result": self.result,
            "error": self.error,
        }


class App:
    """Holds one PorscheGuru conversation per browser session, plus running jobs."""

    def __init__(self, table: ModelTable, client: anthropic.Anthropic | None = None):
        self.table = table
        self.client = client
        self.sessions: dict[str, PorscheGuru] = {}
        self.busy: set[str] = set()
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def _guru(self, session_id: str) -> PorscheGuru:
        guru = self.sessions.get(session_id)
        if guru is None:
            guru = PorscheGuru(self.table, client=self.client or anthropic.Anthropic())
            self.sessions[session_id] = guru
        return guru

    def start_job(self, session_id: str, question: str) -> Job | None:
        """Start answering `question`; returns None if this session is already busy."""
        with self.lock:
            self._prune()
            if session_id in self.busy:
                return None
            self.busy.add(session_id)
            guru = self._guru(session_id)
            job = Job()
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(session_id, guru, job, question), daemon=True).start()
        return job

    def _run(self, session_id: str, guru: PorscheGuru, job: Job, question: str) -> None:
        guru.on_activity = job.activity.append
        turn_start = len(guru.messages)
        try:
            reply = guru.ask(question)
            job.result = {"text": reply.text, "sources": [{"title": t, "url": u} for t, u in reply.sources]}
            job.status = "done"
        except Exception as e:  # report every failure to the page rather than killing the thread
            job.error = describe_error(e)
            job.status = "error"
            # Drop the whole failed turn so a half-finished tool exchange can't break the next one.
            del guru.messages[turn_start:]
        finally:
            with self.lock:
                self.busy.discard(session_id)

    def reset(self, session_id: str) -> None:
        with self.lock:
            self.sessions.pop(session_id, None)

    def _prune(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        for job_id in [j.id for j in self.jobs.values() if j.created < cutoff]:
            del self.jobs[job_id]


def describe_error(e: Exception) -> str:
    if isinstance(e, TypeError) and "authentication method" in str(e):
        # The SDK raises this when no API key or other credential is configured at all.
        return "The server has no ANTHROPIC_API_KEY set. Add it to the app's environment variables and restart."
    if isinstance(e, anthropic.AuthenticationError):
        return (
            "Anthropic rejected the server's ANTHROPIC_API_KEY (the key is set, but not valid). "
            "Check it in the app's settings; the app logs show its length and first characters."
        )
    if isinstance(e, anthropic.RateLimitError):
        return "Rate limited by the Anthropic API; wait a moment and try again."
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic API error ({e.status_code}): {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return "Couldn't reach the Anthropic API from the server."
    if isinstance(e, anthropic.AnthropicError):
        return f"Anthropic client error: {e}"
    return f"Unexpected server error: {type(e).__name__}: {e}"


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PorscheGuru"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _body(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return None
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return None
            return data if isinstance(data, dict) else None

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(HTTPStatus.OK, INDEX_HTML, "text/html; charset=utf-8")
            elif path in HEALTH_PATHS:
                self._json(HTTPStatus.OK, {"ok": True, "models": len(app.table.rows)})
            elif path.startswith("/api/jobs/"):
                job = app.jobs.get(path.rsplit("/", 1)[-1])
                if job is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown job."})
                else:
                    self._json(HTTPStatus.OK, job.to_json())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

        def do_HEAD(self):
            # Some probes use HEAD; answer health paths and the page without a body.
            path = self.path.split("?", 1)[0]
            ok = path == "/" or path in HEALTH_PATHS
            self.send_response(HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            body = self._body()
            if body is None:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Expected a JSON object."})
                return
            session_id = str(body.get("session_id") or "")[:64]
            if not session_id:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Missing session_id."})
                return

            if path == "/api/ask":
                question = str(body.get("question") or "").strip()
                if not question:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Ask a question first."})
                    return
                job = app.start_job(session_id, question[:4000])
                if job is None:
                    self._json(HTTPStatus.CONFLICT, {"error": "Still working on your last question."})
                else:
                    self._json(HTTPStatus.ACCEPTED, {"job_id": job.id})
            elif path == "/api/reset":
                app.reset(session_id)
                self._json(HTTPStatus.OK, {"ok": True})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

        def log_message(self, fmt, *args):
            # Keep container logs to one line per request, without query strings.
            print(f"{self.address_string()} {fmt % args}", flush=True)

    return Handler


def normalize_api_key() -> str:
    """Clean up ANTHROPIC_API_KEY as pasted into a deploy form, and describe it for the logs.

    Strips surrounding whitespace, line breaks and quotes, which a pasted value often picks up
    and which make the API reject an otherwise valid key. Returns a description that never
    includes the key itself.
    """
    raw = os.environ.get("ANTHROPIC_API_KEY", "")
    key = raw.strip().strip("'\"").strip()
    if not key:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        return "ANTHROPIC_API_KEY is not set; questions will fail until it is."
    os.environ["ANTHROPIC_API_KEY"] = key
    notes = []
    if key != raw:
        notes.append("removed surrounding spaces/quotes")
    if key.startswith("sk-ant-admin"):
        notes.append("this is an Admin API key, which cannot call Claude; use a regular API key")
    elif not key.startswith("sk-ant-api"):
        notes.append("does not start with 'sk-ant-api', so it is probably not an Anthropic API key")
    if any(c.isspace() for c in key):
        notes.append("contains spaces or line breaks inside it")
    # Only the generic prefix is logged (e.g. 'sk-ant-api03-'), never the secret part.
    shape = f"ANTHROPIC_API_KEY is set ({len(key)} characters, starts '{key[:13]}')"
    return shape + (": " + "; ".join(notes) if notes else "")


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    table = ModelTable(DEFAULT_DATA)
    print(normalize_api_key(), flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(App(table)))
    print(f"Porsche Guru: {len(table.rows)} models loaded, serving on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
