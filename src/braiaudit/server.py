"""HTTP layer over the audit engine: start an audit, watch it run, read it.

A thin wrapper by design — every decision about what counts as a finding
still lives in the pipeline and the ontology. This module only schedules
`run_audit`, relays its progress, and stores the finished report.

Run with `uvicorn braiaudit.server:app` (needs the `web` extra).

Single-process and local-first: jobs persist in SQLite, live progress lives
in memory, and one worker thread runs audits in order, which also bounds
how many headless browsers can be alive at once.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import uuid
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from braiaudit import assistant, fetch
from braiaudit.pipeline import AuditOptions, run_audit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    domain      TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    report_json TEXT,
    error       TEXT
)
"""

# A bare hostname, optionally with a port: what the audit can actually crawl.
# Rejects the free text a form will receive ("my site", "") with a clear 422
# instead of queueing an audit of a host that cannot exist.
_HOSTNAME = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+(:\d{1,5})?")

_TERMINAL = ("done", "failed")

# One worker: audits run in order, and at most one headless browser is alive.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit")

# Live progress per job. Appended from the worker thread, read by the SSE
# stream; list.append is atomic under the GIL, so no lock is needed.
# ponytail: in-process only and never pruned — fine for one local server,
# needs a broker (Redis pub/sub) and a TTL the day this runs multi-worker.
_events: dict[str, list[dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_path() -> str:
    # Read per call, not at import, so tests and deployments can point it
    # anywhere without reloading the module.
    return os.environ.get("BRAIAUDIT_DB", "braiaudit.db")


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    # A connection per call keeps this safe across the request threads and
    # the worker thread without shared-connection locking.
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _update(job_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _db() as db:
        db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


def _job(job_id: str) -> dict[str, Any]:
    with _db() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no audit with id {job_id!r}")
    return dict(row)


def _options() -> AuditOptions:
    # Rendering dominates runtime and memory; a host can set this to 0 and
    # render-dependent checks degrade to an honest coverage gap.
    return AuditOptions(max_render_pages=int(os.environ.get("BRAIAUDIT_MAX_RENDER_PAGES", "3")))


def _run_job(job_id: str, domain: str) -> None:
    events = _events.setdefault(job_id, [])
    _update(job_id, status="running")
    events.append({"type": "status", "status": "running"})
    try:
        report = run_audit(
            domain,
            options=_options(),
            on_page=lambda snapshot: events.append({"type": "page", **snapshot}),
        )
    except Exception as exc:  # noqa: BLE001 — any failure must end the job, not hang it
        error = f"{type(exc).__name__}: {exc}"
        _update(job_id, status="failed", error=error, finished_at=_now())
        events.append({"type": "failed", "error": error})
        return
    _update(job_id, status="done", report_json=json.dumps(report), finished_at=_now())
    events.append({"type": "done"})


def _normalise_domain(raw: str) -> str:
    host = fetch.site_label(raw.strip()) if raw.strip() else ""
    if not _HOSTNAME.fullmatch(host):
        raise HTTPException(
            status_code=422, detail=f"{raw!r} is not a domain this audit can crawl"
        )
    return host


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    with _db() as db:
        db.execute(_SCHEMA)
        # A job the previous process was running can never finish now. Left
        # as "running" it would spin a client forever; say what happened.
        db.execute(
            "UPDATE jobs SET status = 'failed', finished_at = ?, "
            "error = 'the server restarted before this audit finished' "
            "WHERE status IN ('queued', 'running')",
            (_now(),),
        )
    yield


app = FastAPI(title="Brand AI Readiness Audit", lifespan=_lifespan)


class AuditRequest(BaseModel):
    domain: str


@app.post("/api/audits", status_code=202)
def create_audit(request: AuditRequest) -> dict[str, str]:
    domain = _normalise_domain(request.domain)
    job_id = uuid.uuid4().hex[:12]
    with _db() as db:
        db.execute(
            "INSERT INTO jobs (id, domain, status, created_at) VALUES (?, ?, 'queued', ?)",
            (job_id, domain, _now()),
        )
    _events[job_id] = []
    _executor.submit(_run_job, job_id, domain)
    return {"id": job_id}


@app.get("/api/audits/{job_id}")
def get_audit(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    report_json = job.pop("report_json")
    return {**job, "report": json.loads(report_json) if report_json else None}


@app.get("/api/audits/{job_id}/events")
async def audit_events(job_id: str) -> StreamingResponse:
    _job(job_id)  # 404 before opening a stream

    async def stream() -> AsyncIterator[str]:
        sent = 0
        while True:
            events = _events.get(job_id)
            if events is None:
                # Finished before this process started: there is no live
                # history to replay, only the outcome the database recorded.
                job = _job(job_id)
                if job["status"] in _TERMINAL:
                    yield _sse({"type": job["status"], "error": job["error"]})
                return
            while sent < len(events):
                event = events[sent]
                sent += 1
                yield _sse(event)
                if event["type"] in _TERMINAL:
                    return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/audits/{job_id}/representation")
def get_representation(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="the audit has not finished")
    # No provider configured: every question comes back honestly
    # not_evaluated, alongside the facts the audit did verify.
    return assistant.evaluate_representation(json.loads(job["report_json"]))


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


# --- The built frontend, when present ----------------------------------------
# Served from the same origin so production needs no CORS. Registered last:
# the catch-all must never shadow an API route.
_DIST = Path(
    os.environ.get("BRAIAUDIT_WEB_DIST", Path(__file__).resolve().parents[2] / "web" / "dist")
)

if _DIST.is_dir():

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        root = _DIST.resolve()
        file = (root / path).resolve()
        # Resolve-then-contain: a "../" path must not read outside dist/.
        if path and file.is_relative_to(root) and file.is_file():
            return FileResponse(file)
        # Client-side routes (/audit/abc123) all load the app shell.
        return FileResponse(root / "index.html")
