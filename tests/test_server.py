"""Tests for the HTTP layer (braiaudit.server).

`run_audit` is replaced by a fake that emits progress and returns a real
assembled report, so these run fast and offline. The one exception is the
SSRF test, which deliberately uses the real engine: the point there is that
the guard holds end to end, through the API.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import time

import pytest

fastapi = pytest.importorskip("fastapi")  # the `web` extra is optional
from fastapi.testclient import TestClient  # noqa: E402

from braiaudit import server  # noqa: E402
from braiaudit.report import assemble_report  # noqa: E402
from braiaudit.schemas import validate  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIAUDIT_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("BRAIAUDIT_MAX_RENDER_PAGES", "0")
    with TestClient(server.app) as c:  # context manager runs the lifespan
        yield c


def _fake_run_audit(domain, options=None, on_page=None, **_):
    for i, url in enumerate((f"https://{domain}/", f"https://{domain}/about")):
        on_page({"url": url, "depth": i, "outcome": "analysed", "pages_crawled": i + 1,
                 "pages_analysed": i + 1, "pages_rendered": 0, "queued": 1 - i,
                 "skills_engaged": ["pipeline", "website-observer"]})
    return assemble_report(site=domain, findings_by_url={}, pages_crawled=2,
                           analysable_pages=2, skills_engaged={"pipeline", "website-observer"})


def _wait(client, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/audits/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"audit {job_id} did not finish in {timeout}s")


def test_audit_lifecycle_from_post_to_report(client, monkeypatch):
    monkeypatch.setattr(server, "run_audit", _fake_run_audit)

    r = client.post("/api/audits", json={"domain": "https://Example.com/pricing"})
    assert r.status_code == 202
    job = _wait(client, r.json()["id"])

    assert job["status"] == "done"
    assert job["domain"] == "example.com"  # normalised from the pasted URL
    assert job["finished_at"]
    validate(job["report"], "audit-report")


def test_progress_streams_in_order_and_ends_with_done(client, monkeypatch):
    monkeypatch.setattr(server, "run_audit", _fake_run_audit)
    job_id = client.post("/api/audits", json={"domain": "example.com"}).json()["id"]

    events = []
    with client.stream("GET", f"/api/audits/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    assert [e["type"] for e in events] == ["status", "page", "page", "done"]
    assert events[1]["url"] == "https://example.com/"
    assert events[2]["pages_analysed"] == 2


def test_a_failing_audit_ends_the_job_instead_of_hanging(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(server, "run_audit", boom)
    job = _wait(client, client.post("/api/audits", json={"domain": "example.com"}).json()["id"])
    assert job["status"] == "failed"
    assert "engine exploded" in job["error"]


@pytest.mark.parametrize("domain", ["", "   ", "not a domain", "localhost", "http://"])
def test_input_that_is_not_a_crawlable_domain_is_rejected(client, domain):
    assert client.post("/api/audits", json={"domain": domain}).status_code == 422


def test_unknown_ids_are_404_on_every_route(client):
    for path in ("/api/audits/nope", "/api/audits/nope/events",
                 "/api/audits/nope/representation"):
        assert client.get(path).status_code == 404, path


def test_representation_is_honest_without_a_provider(client, monkeypatch):
    monkeypatch.setattr(server, "run_audit", _fake_run_audit)
    job = _wait(client, client.post("/api/audits", json={"domain": "example.com"}).json()["id"])

    rep = client.get(f"/api/audits/{job['id']}/representation").json()
    assert rep["verified_facts"]["official_domain"]["status"] == "verified"
    assert rep["summary"]["questions_evaluated"] == 0
    assert all(q["status"] == "not_evaluated" for q in rep["questions"])


def test_representation_waits_for_the_audit(client):
    with sqlite3.connect(server._db_path()) as db:
        db.execute("INSERT INTO jobs (id, domain, status, created_at) "
                   "VALUES ('busy', 'example.com', 'running', 'now')")
    assert client.get("/api/audits/busy/representation").status_code == 409


def test_restart_fails_jobs_that_can_never_finish(tmp_path, monkeypatch):
    """A job left 'running' by a dead process would spin a client forever."""
    db_file = tmp_path / "jobs.db"
    monkeypatch.setenv("BRAIAUDIT_DB", str(db_file))
    with sqlite3.connect(db_file) as db:
        db.execute(server._SCHEMA)
        db.execute("INSERT INTO jobs (id, domain, status, created_at) "
                   "VALUES ('orphan', 'example.com', 'running', 'then')")

    with TestClient(server.app) as c:
        job = c.get("/api/audits/orphan").json()
    assert job["status"] == "failed"
    assert "restarted" in job["error"]


def test_ssrf_guard_holds_through_the_api(client):
    """The real engine, no fake: a form submission naming an internal address
    must finish as an unassessed audit with nothing fetched — never as a
    report built from whatever answered on 127.0.0.1."""
    job = _wait(client, client.post("/api/audits", json={"domain": "127.0.0.1"}).json()["id"])

    assert job["status"] == "done"
    report = job["report"]
    assert report["summary"]["readiness_score"] is None
    assert report["meta"]["crawl"]["pages_crawled"] == 0
    assert "No Page Yielded Content This Audit Could Analyse" in {
        lim["title"] for lim in report["audit_limitations"]
    }


def test_spa_route_cannot_read_outside_dist(tmp_path, monkeypatch):
    """The catch-all serves files from disk, so it is a traversal target."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("APP SHELL")
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    monkeypatch.setenv("BRAIAUDIT_WEB_DIST", str(dist))
    monkeypatch.setenv("BRAIAUDIT_DB", str(tmp_path / "jobs.db"))

    mod = importlib.reload(server)  # _DIST is read at import
    try:
        with TestClient(mod.app) as c:
            assert c.get("/audit/abc123").text == "APP SHELL"  # client route
            leaked = c.get("/%2e%2e/secret.txt").text
            assert "TOP SECRET" not in leaked
            assert c.get("/api/unknown").status_code == 404  # API never shadowed
    finally:
        monkeypatch.delenv("BRAIAUDIT_WEB_DIST")
        importlib.reload(server)


def test_per_client_hourly_limit(client, monkeypatch):
    """Each accepted audit costs a crawl on the server's IP; one client must
    not be able to queue them without bound."""
    monkeypatch.setattr(server, "run_audit", _fake_run_audit)
    monkeypatch.setenv("BRAIAUDIT_AUDITS_PER_HOUR", "2")
    server._recent.clear()

    codes = [client.post("/api/audits", json={"domain": "example.com"}).status_code
             for _ in range(3)]
    assert codes == [202, 202, 429]

    limited = client.post("/api/audits", json={"domain": "example.com"})
    assert "audits this hour" in limited.json()["detail"]
    assert int(limited.headers["retry-after"]) > 0


def test_invalid_input_does_not_spend_the_quota(client, monkeypatch):
    monkeypatch.setattr(server, "run_audit", _fake_run_audit)
    monkeypatch.setenv("BRAIAUDIT_AUDITS_PER_HOUR", "1")
    server._recent.clear()

    assert client.post("/api/audits", json={"domain": "not a domain"}).status_code == 422
    assert client.post("/api/audits", json={"domain": "example.com"}).status_code == 202


def test_full_queue_refuses_new_audits(client, monkeypatch):
    monkeypatch.setenv("BRAIAUDIT_MAX_QUEUE", "1")
    server._recent.clear()
    with sqlite3.connect(server._db_path()) as db:
        db.execute("INSERT INTO jobs (id, domain, status, created_at) "
                   "VALUES ('busy', 'example.com', 'running', 'now')")

    r = client.post("/api/audits", json={"domain": "example.com"})
    assert r.status_code == 503
    assert "queue is full" in r.json()["detail"]
