import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import pytest

from processor.engine import Engine
from processor.models import ProcessRequest, Record
from processor.settings import Settings
from tools.bench import evaluate
from tools.generate import generate, invalid_archive, member
from tools.verify import check_progress, read_expected, stable_result, verify_result


def golden(tmp_path):
    """A static golden transcript. This is not an executable candidate backend."""
    record = Record(record_id="same-id", payload={"text": "fixture"}, work_ms=25, timeout_ms=100)
    expected = {"batch-001.jsonl": {record.record_id: record}}
    engine = Engine(Settings(db_path=str(tmp_path/"fixture.db"), failure_rate=0, jitter_fraction=0))
    req = ProcessRequest(run_id="test-run", job_id="job-1", task_id="batch-001.jsonl", attempt=1, record=record)
    value, receipt = engine.result(req)
    engine.store.close()
    audit = [{"id": 1, "run_id": "test-run", "job_id": "job-1", "task_id": "batch-001.jsonl", "record_id": record.record_id,
              "attempt": 1, "input_hash": record.fingerprint(), "status": "ok", "started_at": 1., "finished_at": 2.,
              "actual_ms": 25, "value": value, "receipt": receipt}]
    result = {"job_id": "job-1", "status": "SUCCEEDED", "files": [{"task_id": "batch-001.jsonl", "status": "SUCCEEDED",
              "records_total": 1, "records_succeeded": 1, "records_failed": 0, "value_sum": value,
              "records": [{"record_id": record.record_id, "status": "SUCCEEDED", "attempts": 1, "value": value, "receipt": receipt}]}],
              "totals": {"files": 1, "records": 1, "succeeded": 1, "failed": 0, "value_sum": value}}
    return expected, result, audit


def test_generator_is_reproducible(tmp_path):
    a, b = tmp_path/"a.zip", tmp_path/"b.zip"
    generate(a, files=2, records_per_file=4, seed=73)
    generate(b, files=2, records_per_file=4, seed=73)
    assert a.read_bytes() == b.read_bytes()
    expected = read_expected(a)
    assert len(expected) == 2 and sum(map(len, expected.values())) == 8
    assert set(expected["batch-001.jsonl"]) == set(expected["batch-002.jsonl"])


@pytest.mark.parametrize("kind", ["empty", "unsafe-path", "duplicate-record", "malformed-json", "duplicate-file", "invalid-record"])
def test_invalid_archives_are_detected(tmp_path, kind):
    path = tmp_path/"bad.zip"
    invalid_archive(path, kind)
    with pytest.raises(ValueError):
        read_expected(path)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path/"bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member("batch-001.jsonl"), '{"record_id":"a","record_id":"b","payload":{},"work_ms":25,"timeout_ms":100}')
    with pytest.raises(ValueError):
        read_expected(path)


def test_verifier_accepts_evidenced_success(tmp_path):
    expected, result, audit = golden(tmp_path)
    assert verify_result(expected, result, audit, "job-1") == []


@pytest.mark.parametrize("mutation", ["skip", "duplicate", "receipt", "aggregate", "fake-failure", "changed-input", "no-call", "extra-call", "skip-attempt"])
def test_verifier_rejects_invalid_shortcuts(tmp_path, mutation):
    expected, result, audit = golden(tmp_path)
    record = result["files"][0]["records"][0]
    if mutation == "skip":
        result["files"][0]["records"] = []
    elif mutation == "duplicate":
        result["files"][0]["records"].append(copy.deepcopy(record))
    elif mutation == "receipt":
        record["receipt"] = "fabricated"
    elif mutation == "aggregate":
        result["totals"]["value_sum"] += 1
    elif mutation == "fake-failure":
        record.clear()
        record.update(record_id="same-id", status="FAILED", attempts=3, error_code="ATTEMPTS_EXHAUSTED")
    elif mutation == "changed-input":
        audit[0]["input_hash"] = "different"
    elif mutation == "no-call":
        audit = []
    elif mutation == "extra-call":
        audit.append({**audit[0], "record_id": "unexpected"})
    elif mutation == "skip-attempt":
        audit[0]["attempt"] = record["attempts"] = 2
    assert verify_result(expected, result, audit, "job-1")


def test_genuine_exhausted_failures_are_valid(tmp_path):
    expected, result, audit = golden(tmp_path)
    original = audit[0]
    audit = [{**original, "id": i, "attempt": i, "status": "transient_error", "started_at": i*2., "finished_at": i*2.+1,
              "value": None, "receipt": None} for i in range(1, 4)]
    result["status"] = "FAILED"
    file = result["files"][0]
    file.update(status="FAILED", records_succeeded=0, records_failed=1, value_sum=0)
    file["records"] = [{"record_id": "same-id", "status": "FAILED", "attempts": 3, "error_code": "ATTEMPTS_EXHAUSTED"}]
    result["totals"].update(succeeded=0, failed=1, value_sum=0)
    assert verify_result(expected, result, audit, "job-1") == []


def test_duplicate_delivery_does_not_duplicate_output(tmp_path):
    expected, result, audit = golden(tmp_path)
    assert verify_result(expected, result, audit + [{**audit[0], "id": 2}], "job-1") == []


def test_progress_invariants():
    previous = {"status": "RUNNING", "ingestion_complete": True, "files_total": 1, "files_terminal": 0,
                "records_total": 2, "records_succeeded": 1, "records_failed": 0, "records_running": 1, "records_pending": 0}
    assert check_progress(previous) == []
    assert check_progress({**previous, "status": "SUCCEEDED"})
    assert check_progress({**previous, "records_succeeded": 0, "records_running": 2}, previous)
    assert check_progress({**previous, "records_total": 3, "records_pending": 1}, previous)
    terminal = {**previous, "status": "SUCCEEDED", "files_terminal": 1, "records_succeeded": 2, "records_running": 0}
    assert check_progress(terminal, previous, terminal=True) == []


def test_result_order_is_not_significant(tmp_path):
    _, result, _ = golden(tmp_path)
    assert stable_result(result) == stable_result(copy.deepcopy(result))


@pytest.mark.parametrize("corrupt", [False, True])
def test_full_benchmark_against_static_http_transcript(tmp_path, monkeypatch, corrupt):
    expected, result, audit = golden(tmp_path)
    path = tmp_path/"fixture.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member("batch-001.jsonl"), next(iter(expected["batch-001.jsonl"].values())).model_dump_json()+"\n")
    snapshot = {"job_id": "job-1", "status": "SUCCEEDED", "ingestion_complete": True,
                "files_total": 1, "files_terminal": 1, "records_total": 1, "records_succeeded": 1,
                "records_failed": 0, "records_running": 0, "records_pending": 0}
    if corrupt:
        result["files"][0]["records"][0]["receipt"] = "forged"
    metrics_calls = 0
    async def handler(req):
        nonlocal metrics_calls
        if req.url.path == "/health":
            data = {"status": "ok"}
        elif req.url.path == "/admin/metrics":
            metrics_calls += 1
            data = {"requests_total": 0 if metrics_calls == 1 else 1}
        elif req.url.path == "/admin/audit":
            data = {"items": audit if req.url.params.get("after") == "0" else [], "next_after": 1}
        elif req.url.path == "/jobs":
            assert req.headers["X-Run-ID"] == "test-run"
            assert b'name="file"' in await req.aread()
            return httpx.Response(202, json={"job_id": "job-1"})
        elif req.url.path == "/jobs/job-1":
            data = snapshot
        elif req.url.path == "/jobs/job-1/result":
            data = result
        else:
            return httpx.Response(404)
        return httpx.Response(200, json=data)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    args = SimpleNamespace(run_id="test-run", candidate_url=["http://candidate"], processor_url="http://processor",
        input=[path], invalid=[], output=tmp_path/"report.json", admin_token="secret", http_timeout=5,
        deadline=10, poll_seconds=.01, settle_seconds=0)
    report = asyncio.run(evaluate(args))
    assert report["correct"] is not corrupt
    assert (report["valid_makespan_seconds"] is not None) is not corrupt
