"""An evaluator audit ledger, not a candidate state-store implementation."""
from pathlib import Path
import sqlite3
import time

from .models import ProcessRequest


class AuditStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL, job_id TEXT NOT NULL,
                task_id TEXT NOT NULL, record_id TEXT NOT NULL,
                attempt INTEGER NOT NULL, input_hash TEXT NOT NULL,
                started_at REAL NOT NULL, finished_at REAL,
                status TEXT NOT NULL, actual_ms INTEGER NOT NULL,
                value INTEGER, receipt TEXT
            );
            CREATE INDEX IF NOT EXISTS requests_identity
            ON requests(run_id,job_id,task_id,record_id);
            CREATE INDEX IF NOT EXISTS requests_run ON requests(run_id,id);
            CREATE TABLE IF NOT EXISTS peaks (
                run_id TEXT PRIMARY KEY, peak_active INTEGER NOT NULL
            );
        """)
        # Simulator restart is outside timed evaluation. Never pretend unfinished
        # work completed; keep its previous audit evidence for diagnosis.
        self.db.execute(
            "UPDATE requests SET status='abandoned',finished_at=? WHERE status='accepted'",
            (time.time(),),
        )

    def prior_hash(self, req: ProcessRequest):
        row = self.db.execute(
            "SELECT input_hash FROM requests WHERE run_id=? AND job_id=? AND task_id=? AND record_id=? LIMIT 1",
            (req.run_id, req.job_id, req.task_id, req.record.record_id),
        ).fetchone()
        return row[0] if row else None

    def begin(self, req: ProcessRequest, actual_ms: int, overloaded: bool) -> int:
        now = time.time()
        row = self.db.execute(
            "INSERT INTO requests(run_id,job_id,task_id,record_id,attempt,input_hash,started_at,finished_at,status,actual_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (req.run_id, req.job_id, req.task_id, req.record.record_id,
             req.attempt, req.record.fingerprint(), now, now if overloaded else None,
             "overloaded" if overloaded else "accepted", actual_ms),
        )
        return row.lastrowid

    def finish(self, request_id: int, status: str, value=None, receipt=None):
        self.db.execute(
            "UPDATE requests SET finished_at=?,status=?,value=?,receipt=? WHERE id=?",
            (time.time(), status, value, receipt, request_id),
        )

    def peak(self, run_id: str, active: int):
        self.db.execute(
            "INSERT INTO peaks VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET peak_active=MAX(peak_active,excluded.peak_active)",
            (run_id, active),
        )

    def audit(self, run_id: str, job_id: str | None, after: int, limit: int):
        if job_id is None:
            rows = self.db.execute(
                "SELECT * FROM requests WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM requests WHERE run_id=? AND job_id=? AND id>? ORDER BY id LIMIT ?",
                (run_id, job_id, after, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def metrics(self, run_id: str):
        counts = {row[0]: row[1] for row in self.db.execute(
            "SELECT status,COUNT(*) FROM requests WHERE run_id=? GROUP BY status", (run_id,)
        )}
        latency = [r[0] * 1000 for r in self.db.execute(
            "SELECT finished_at-started_at FROM requests WHERE run_id=? AND status IN ('ok','transient_error','deadline_exceeded') ORDER BY 1",
            (run_id,),
        )]
        dup = self.db.execute(
            "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM requests WHERE run_id=? AND status!='overloaded' "
            "GROUP BY job_id,task_id,record_id,attempt HAVING COUNT(*)>1)", (run_id,),
        ).fetchone()[0]
        peak = self.db.execute("SELECT peak_active FROM peaks WHERE run_id=?", (run_id,)).fetchone()
        total = sum(counts.values())
        return {
            "requests_total": total,
            "admitted_requests": total-counts.get("overloaded", 0),
            "responses_by_outcome": counts,
            "overload_rejections": counts.get("overloaded", 0),
            "duplicate_admissions": dup,
            "peak_active": peak[0] if peak else 0,
            "mean_service_ms": round(sum(latency)/len(latency), 3) if latency else None,
            "p95_service_ms": round(latency[min(len(latency)-1, int((len(latency)-1)*.95))], 3) if latency else None,
        }

    def close(self):
        self.db.close()
