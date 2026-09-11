"""Benchmark a candidate HTTP backend. This never executes its records for it."""
import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import time
from urllib.parse import quote
import uuid

import httpx
from pydantic import TypeAdapter
from processor.env import load_env
from processor.models import ID
from .verify import check_progress, read_expected, stable_result, verify_result


async def fetch_audit(client, processor_url, run_id, admin_token):
    rows, after = [], 0
    while True:
        response = await client.get(processor_url+"/admin/audit",
            params={"run_id": run_id, "after": after, "limit": 1000},
            headers={"X-Admin-Token": admin_token})
        response.raise_for_status()
        page = response.json()
        batch = page["items"]
        if not batch:
            break
        if page["next_after"] <= after:
            raise ValueError("audit cursor did not advance")
        rows.extend(batch)
        after = page["next_after"]
        if len(rows) > 1000000:
            raise ValueError("audit safety limit exceeded; inspect request storm separately")
    return rows


async def evaluate(args):
    run_id = args.run_id or uuid.uuid4().hex
    TypeAdapter(ID).validate_python(run_id)
    urls = [url.rstrip("/") for url in args.candidate_url]
    endpoint = itertools.cycle(urls)
    processor_url = args.processor_url.rstrip("/")
    fixtures = [(path, read_expected(path)) for path in args.input]
    report = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
              "correct": False, "jobs": [], "errors": [], "poll_transport_errors": 0,
              "metrics": {}, "valid_makespan_seconds": None}
    errors = report["errors"]
    def error(message):
        if message not in errors and len(errors) < 100:
            errors.append(message)
    jobs = []
    timeout = httpx.Timeout(args.http_timeout, connect=min(5, args.http_timeout))
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for url in set(urls+[processor_url]):
            response = await client.get(url+"/health")
            response.raise_for_status()
        response = await client.get(processor_url+"/admin/metrics", params={"run_id": run_id},
                                    headers={"X-Admin-Token": args.admin_token})
        response.raise_for_status()
        if response.json()["requests_total"]:
            raise ValueError("run_id already has processor traffic; use a fresh run_id")
        overall_start = time.perf_counter()
        try:
            async with asyncio.timeout(args.deadline):
                for i, (path, expected) in enumerate(fixtures):
                    start = time.perf_counter()
                    headers = {"X-Run-ID": run_id, "Idempotency-Key": f"{run_id}-{i}"}
                    with path.open("rb") as stream:
                        response = await client.post(next(endpoint)+"/jobs", headers=headers,
                            files={"file": (path.name, stream, "application/zip")})
                    if response.status_code != 202:
                        error(f"{path.name}: initial submission returned {response.status_code}, expected 202")
                        continue
                    job_id = response.json().get("job_id")
                    TypeAdapter(ID).validate_python(job_id)
                    if any(j["job_id"] == job_id for j in jobs):
                        error(f"{path.name}: distinct submissions returned the same job_id")
                        continue
                    item = {"job_id": job_id, "input": str(path), "expected": expected,
                            "start": start, "accepted_seconds": time.perf_counter()-start,
                            "previous": None, "result": None, "done": False}
                    jobs.append(item)
                    print(f"Accepted {path.name}: {job_id}", flush=True)
                    # Same request, potentially through another API instance.
                    with path.open("rb") as stream:
                        repeated = await client.post(next(endpoint)+"/jobs", headers=headers,
                            files={"file": (path.name, stream, "application/zip")})
                    if repeated.status_code not in (200, 202) or repeated.json().get("job_id") != job_id:
                        error(f"{job_id}: repeated submission is not idempotent")
                while any(not j["done"] for j in jobs):
                    for job in jobs:
                        if job["done"]:
                            continue
                        url = next(endpoint)+"/jobs/"+quote(job["job_id"], safe="")
                        try:
                            response = await client.get(url)
                            if response.status_code >= 500:
                                report["poll_transport_errors"] += 1
                                continue
                            if response.status_code != 200:
                                error(f"{job['job_id']}: progress returned {response.status_code}")
                                continue
                            snapshot = response.json()
                            for issue in check_progress(snapshot, job["previous"]):
                                error(f"{job['job_id']}: {issue}")
                            if snapshot.get("job_id") != job["job_id"]:
                                error("progress job_id mismatch")
                            job["previous"] = snapshot
                            if snapshot.get("status") in ("SUCCEEDED", "FAILED"):
                                result_response = await client.get(url+"/result")
                                if result_response.status_code != 200:
                                    error(f"{job['job_id']}: terminal result returned {result_response.status_code}")
                                else:
                                    job["result"] = result_response.json()
                                job["done"] = True
                                job["elapsed_seconds"] = time.perf_counter()-job["start"]
                        except httpx.TransportError:
                            # A brief candidate restart is allowed within the deadline.
                            report["poll_transport_errors"] += 1
                    if any(not j["done"] for j in jobs):
                        await asyncio.sleep(args.poll_seconds)
        except TimeoutError:
            error(f"benchmark deadline of {args.deadline:g}s exceeded")
        measured_end = time.perf_counter()
        if len(jobs) != len(fixtures):
            error("not all input archives were accepted as distinct jobs")
        for path in args.invalid:
            with path.open("rb") as stream:
                response = await client.post(next(endpoint)+"/jobs",
                    headers={"X-Run-ID": run_id, "Idempotency-Key": uuid.uuid4().hex},
                    files={"file": (path.name, stream, "application/zip")})
            if response.status_code not in (400, 413, 422):
                error(f"invalid fixture {path.name} returned {response.status_code}")
        await asyncio.sleep(args.settle_seconds)
        for job in jobs:
            if not job["done"] or job["result"] is None:
                error(f"{job['job_id']}: no terminal result")
                continue
            base = next(endpoint)+"/jobs/"+quote(job["job_id"], safe="")
            repeated = await client.get(base+"/result")
            if repeated.status_code != 200 or stable_result(repeated.json()) != stable_result(job["result"]):
                error(f"{job['job_id']}: final result changed after publication")
            status_response = await client.get(base)
            if status_response.status_code != 200:
                error(f"{job['job_id']}: terminal job disappeared")
            else:
                final = status_response.json()
                for issue in check_progress(final, job["previous"], terminal=True):
                    error(f"{job['job_id']}: {issue}")
                result = job["result"]
                if isinstance(result, dict) and isinstance(result.get("totals"), dict):
                    totals = result["totals"]
                    pairs = {"files_total": "files", "records_total": "records",
                             "records_succeeded": "succeeded", "records_failed": "failed"}
                    if any(final.get(k) != totals.get(v) for k, v in pairs.items()) or final.get("status") != result.get("status"):
                        error(f"{job['job_id']}: progress disagrees with final result")
        audit = await fetch_audit(client, processor_url, run_id, args.admin_token)
        grouped = defaultdict(list)
        known = {job["job_id"] for job in jobs}
        for row in audit:
            if row["job_id"] not in known:
                error("processor received work for an unknown/rejected/duplicate job")
            grouped[row["job_id"]].append(row)
        for job in jobs:
            issues = verify_result(job["expected"], job["result"], grouped[job["job_id"]], job["job_id"])
            for issue in issues:
                error(f"{job['job_id']}: {issue}")
            report["jobs"].append({"job_id": job["job_id"], "input": job["input"],
                "status": job["previous"].get("status") if job["previous"] else None,
                "accepted_seconds": round(job["accepted_seconds"], 4),
                "elapsed_seconds": round(job.get("elapsed_seconds", measured_end-job["start"]), 4),
                "result_valid": not issues})
        response = await client.get(processor_url+"/admin/metrics", params={"run_id": run_id},
                                    headers={"X-Admin-Token": args.admin_token})
        response.raise_for_status()
        report["metrics"] = response.json()
        report["observed_makespan_seconds"] = round(measured_end-overall_start, 4)
        report["correct"] = not errors and bool(jobs)
        if report["correct"]:
            report["valid_makespan_seconds"] = report["observed_makespan_seconds"]
        if jobs:
            report["mean_job_latency_seconds"] = round(sum(j["elapsed_seconds"] for j in report["jobs"])/len(jobs), 4)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".audit.jsonl").write_text(
            "".join(json.dumps(row, separators=(",", ":"))+"\n" for row in audit), encoding="utf-8")
        return report


def parser():
    load_env()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate-url", action="append", required=True, help="repeat for API replicas sharing state")
    p.add_argument("--processor-url", default="http://127.0.0.1:8001")
    p.add_argument("--admin-token", default=os.getenv("PROCESSOR_ADMIN_TOKEN", "local-admin-change-me"))
    p.add_argument("--input", type=Path, nargs="+", required=True)
    p.add_argument("--invalid", type=Path, nargs="*", default=[])
    p.add_argument("--run-id")
    p.add_argument("--deadline", type=float, default=600)
    p.add_argument("--http-timeout", type=float, default=30)
    p.add_argument("--poll-seconds", type=float, default=.2)
    p.add_argument("--settle-seconds", type=float, default=1)
    p.add_argument("--output", type=Path, default=Path("reports/benchmark.json"))
    return p


def main():
    p = parser()
    args = p.parse_args()
    if min(args.deadline, args.http_timeout, args.poll_seconds) <= 0 or args.settle_seconds < 0:
        p.error("deadlines/poll interval must be positive; settle interval must be nonnegative")
    try:
        report = asyncio.run(evaluate(args))
        code = 0 if report["correct"] else 2
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
        report = {"correct": False, "valid_makespan_seconds": None,
                  "error": f"Benchmark could not complete: {type(exc).__name__}: {exc}"}
        code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
