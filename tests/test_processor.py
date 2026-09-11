import asyncio
import json

from fastapi.testclient import TestClient
import pytest

from processor.app import create_app
from processor.engine import Engine
from processor.models import ProcessRequest, Record
from processor.settings import Settings
from processor.store import AuditStore


def request(**overrides):
    body = {"run_id": "run-1", "job_id": "job-1", "task_id": "batch-001.jsonl", "attempt": 1,
            "record": {"record_id": "row-1", "payload": {"text": "hello"},
                       "work_ms": 25, "timeout_ms": 100, "max_attempts": 3}}
    body.update(overrides)
    return ProcessRequest.model_validate(body)


def test_success_replay_is_stable_but_not_deduplicated(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db"), failure_rate=0, jitter_fraction=0))
        a = await engine.process(request())
        b = await engine.process(request())
        assert a == b and a[0] == 200
        stats = engine.store.metrics("run-1")
        assert stats["admitted_requests"] == 2
        assert stats["duplicate_admissions"] == 1
        assert engine.active == 0
        await engine.close()
    asyncio.run(scenario())


def test_global_capacity_and_retry_after(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db"), capacity=2, failure_rate=0, jitter_fraction=0))
        slow = {"record_id": "slow", "payload": {}, "work_ms": 80, "timeout_ms": 200}
        a = asyncio.create_task(engine.process(request(record=slow)))
        b = asyncio.create_task(engine.process(request(run_id="run-2", record=slow)))
        await asyncio.sleep(.005)
        assert engine.active == 2
        code, body, headers = await engine.process(request(job_id="job-2"))
        assert code == 429 and headers["Retry-After"] == "1"
        assert body["attempt_consumed"] is False and engine.active == 2
        await asyncio.gather(a, b)
        assert engine.active == 0
        await engine.close()
    asyncio.run(scenario())


def test_transient_error(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db"), failure_rate=1, jitter_fraction=0))
        code, body, _ = await engine.process(request())
        assert code == 503 and body["attempt_consumed"] is True
        assert engine.active == 0
        await engine.close()
    asyncio.run(scenario())


def test_deadline_is_not_success(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db"), failure_rate=0, jitter_fraction=0))
        req = request(record={"record_id": "late", "payload": {}, "work_ms": 100, "timeout_ms": 25})
        code, body, _ = await engine.process(req)
        assert code == 504 and body["error"] == "DEADLINE_EXCEEDED"
        assert engine.store.metrics("run-1")["responses_by_outcome"] == {"deadline_exceeded": 1}
        await engine.close()
    asyncio.run(scenario())


def test_caller_cancellation_does_not_release_server_capacity(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db"), failure_rate=0, jitter_fraction=0))
        req = request(record={"record_id": "slow", "payload": {}, "work_ms": 70, "timeout_ms": 200})
        caller = asyncio.create_task(engine.process(req))
        await asyncio.sleep(.005)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert engine.active == 1
        await asyncio.sleep(.09)
        assert engine.active == 0
        assert engine.store.metrics("run-1")["responses_by_outcome"] == {"ok": 1}
        await engine.close()
    asyncio.run(scenario())


def test_input_change_is_rejected(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db"), failure_rate=0, jitter_fraction=0))
        await engine.process(request())
        changed = request().model_dump()
        changed["record"]["payload"] = {"text": "different"}
        assert (await engine.process(ProcessRequest.model_validate(changed)))[0] == 409
        assert engine.store.metrics("run-1")["requests_total"] == 1
        await engine.close()
    asyncio.run(scenario())


def test_schedule_and_job_ids_do_not_change_failure_sequence(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db")))
        for attempt in range(1, 4):
            assert engine.plan(request(attempt=attempt)) == engine.plan(request(run_id="other-run", job_id="other-job", attempt=attempt))
        assert engine.result(request()) != engine.result(request(job_id="other-job"))
        await engine.close()
    asyncio.run(scenario())


def test_repeated_record_ids_in_different_files_have_distinct_receipts(tmp_path):
    async def scenario():
        engine = Engine(Settings(db_path=str(tmp_path/"audit.db")))
        assert engine.result(request())[1] != engine.result(request(task_id="other.jsonl"))[1]
        await engine.close()
    asyncio.run(scenario())


def test_shutdown_records_abandoned_requests(tmp_path):
    async def scenario():
        path = str(tmp_path/"audit.db")
        engine = Engine(Settings(db_path=path, failure_rate=0))
        caller = asyncio.create_task(engine.process(request(record={"record_id": "slow", "payload": {}, "work_ms": 1000, "timeout_ms": 2000})))
        await asyncio.sleep(.005)
        await engine.close()
        with pytest.raises(asyncio.CancelledError):
            await caller
        store = AuditStore(path)
        assert store.metrics("run-1")["responses_by_outcome"] == {"abandoned": 1}
        store.close()
    asyncio.run(scenario())


def test_http_contract_and_admin_isolation(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path/"audit.db"), capacity=7, failure_rate=0, jitter_fraction=0))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        schema = client.get("/openapi.json").json()
        assert "/admin/metrics" not in schema["paths"]
        assert "capacity" not in json.dumps(schema).lower()
        response = client.post("/v1/process", json=request().model_dump())
        assert response.status_code == 200
        assert len(response.json()["receipt"]) == 64
        assert client.get("/admin/metrics", params={"run_id": "run-1"}).status_code == 401
        headers = {"X-Admin-Token": "local-admin-change-me"}
        response = client.get("/admin/audit", params={"run_id": "run-1"}, headers=headers)
        assert response.status_code == 200 and len(response.json()["items"]) == 1
        assert client.get("/admin/audit", params={"run_id": "run-1", "limit": 1001}, headers=headers).status_code == 422
        assert client.post("/v1/process", content=b"x"*65537).status_code == 413
        assert client.post("/v1/process", json={}).status_code == 422


@pytest.mark.parametrize("values", [{"capacity": 0}, {"failure_rate": float("nan")}, {"jitter_fraction": 1}, {"mode": "evaluation"}])
def test_invalid_configuration(values):
    with pytest.raises(ValueError):
        Settings(**values)


@pytest.mark.parametrize("field,value", [("work_ms", 0), ("timeout_ms", -1), ("max_attempts", 0), ("record_id", "../bad"), ("work_ms", "25")])
def test_invalid_record(field, value):
    record = request().record.model_dump()
    record[field] = value
    with pytest.raises(ValueError):
        Record.model_validate(record)


def test_attempt_cannot_exceed_limit():
    with pytest.raises(ValueError):
        request(attempt=4)


def test_nonfinite_and_oversize_payloads_rejected():
    with pytest.raises(ValueError):
        request(record={"record_id": "x", "payload": {"n": float("nan")}, "work_ms": 25, "timeout_ms": 50})
    with pytest.raises(ValueError):
        request(record={"record_id": "x", "payload": {"text": "a"*17000}, "work_ms": 25, "timeout_ms": 50})
