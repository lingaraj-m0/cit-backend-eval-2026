import asyncio
import hashlib
import hmac
from collections import Counter

from .models import ProcessRequest, canonical
from .settings import Settings
from .store import AuditStore


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = AuditStore(settings.db_path)
        self.active = 0
        self.active_by_run = Counter()
        self.pending = set()

    def draw(self, req: ProcessRequest, purpose: str) -> float:
        # Independent of arrival order, worker count, run_id, and generated job_id.
        # Replaying a logical attempt has the same outcome, not a new dice roll.
        key = canonical([self.settings.seed, req.task_id, req.record.record_id,
                         req.record.fingerprint(), req.attempt, purpose])
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    def plan(self, req: ProcessRequest):
        multiplier = 1 + (2*self.draw(req, "jitter")-1)*self.settings.jitter_fraction
        actual_ms = max(1, round(req.record.work_ms*multiplier))
        if actual_ms > req.record.timeout_ms:
            return actual_ms, "deadline_exceeded", 504
        if self.draw(req, "failure") < self.settings.failure_rate:
            return actual_ms, "transient_error", 503
        return actual_ms, "ok", 200

    def result(self, req: ProcessRequest):
        value = int(hashlib.sha256(canonical(req.record.payload).encode("utf-8")).hexdigest()[:12], 16) % 1000000
        signed = canonical([req.run_id, req.job_id, req.task_id, req.record.record_id,
                            req.record.fingerprint(), value])
        receipt = hmac.new(self.settings.signing_key.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return value, receipt

    async def process(self, req: ProcessRequest):
        prior = self.store.prior_hash(req)
        if prior is not None and prior != req.record.fingerprint():
            return 409, {"error": "INPUT_CHANGED", "retryable": False}, {}
        actual_ms, outcome, code = self.plan(req)
        # Admission, audit insertion and increment do not await. One event loop,
        # one server process: no other request can interleave these operations.
        overloaded = self.active >= self.settings.capacity
        request_id = self.store.begin(req, actual_ms, overloaded)
        if overloaded:
            return 429, {"error": "OVERLOADED", "retryable": True, "attempt_consumed": False}, {
                "Retry-After": str(self.settings.retry_after_seconds)
            }
        self.active += 1
        self.active_by_run[req.run_id] += 1
        self.store.peak(req.run_id, self.active_by_run[req.run_id])
        task = asyncio.create_task(self._execute(req, request_id, actual_ms, outcome, code))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        # A disconnected caller does not necessarily stop remote computation.
        return await asyncio.shield(task)

    async def _execute(self, req, request_id, actual_ms, outcome, code):
        try:
            await asyncio.sleep(min(actual_ms, req.record.timeout_ms)/1000)
            if code == 200:
                value, receipt = self.result(req)
                self.store.finish(request_id, outcome, value, receipt)
                body = {"record_id": req.record.record_id, "attempt": req.attempt,
                        "value": value, "receipt": receipt}
            else:
                self.store.finish(request_id, outcome)
                body = {"error": outcome.upper(), "record_id": req.record.record_id,
                        "attempt": req.attempt, "retryable": True, "attempt_consumed": True}
            return code, body, {}
        except asyncio.CancelledError:
            self.store.finish(request_id, "abandoned")
            raise
        finally:
            self.active -= 1
            self.active_by_run[req.run_id] -= 1
            if not self.active_by_run[req.run_id]:
                del self.active_by_run[req.run_id]

    async def close(self):
        tasks = list(self.pending)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.store.close()
