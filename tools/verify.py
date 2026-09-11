"""A black-box result verifier, not a worker, scheduler, or reference executor."""
from collections import defaultdict
import json
from pathlib import Path
import stat
import zipfile

from pydantic import TypeAdapter
from processor.models import FileID, Record, canonical

MAX_ZIP_BYTES = 64*1024*1024
MAX_EXPANDED_BYTES = 256*1024*1024
MAX_LINE_BYTES = 65536


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_expected(path: Path) -> dict[str, dict[str, Record]]:
    """Bounded, no-extraction reader for trusted benchmark fixtures and tests."""
    if path.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("ZIP too large")
    expected = {}
    total_bytes = total_records = 0
    adapter = TypeAdapter(FileID)
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= 1000:
            raise ValueError("ZIP must contain 1..1000 files")
        if sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
            raise ValueError("expanded ZIP too large")
        for info in infos:
            adapter.validate_python(info.filename)
            mode = stat.S_IFMT(info.external_attr >> 16)
            if info.is_dir() or mode not in (0, stat.S_IFREG) or info.flag_bits & 1:
                raise ValueError("only unencrypted regular files are allowed")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("unsupported compression")
            if info.filename in expected:
                raise ValueError("duplicate filename")
            records = {}
            with archive.open(info) as stream:
                while line := stream.readline(MAX_LINE_BYTES+1):
                    total_bytes += len(line)
                    if len(line) > MAX_LINE_BYTES or total_bytes > MAX_EXPANDED_BYTES:
                        raise ValueError("expanded input limit exceeded")
                    if not line.strip():
                        continue
                    record = Record.model_validate(json.loads(line.decode("utf-8"), object_pairs_hook=unique_object))
                    if record.record_id in records:
                        raise ValueError("duplicate record_id within file")
                    records[record.record_id] = record
                    total_records += 1
                    if total_records > 100000:
                        raise ValueError("too many records")
            if not records:
                raise ValueError("empty JSONL file")
            expected[info.filename] = records
    return expected


def verify_result(expected, result, audit, job_id):
    """Return errors; a fast invalid submission never receives a valid timing."""
    errors = []
    def fail(message):
        if len(errors) < 100:
            errors.append(message)
    def eq(actual, wanted, path):
        if type(actual) is not type(wanted) or actual != wanted:
            fail(f"{path}: expected {wanted!r}, got {actual!r}")
    if not isinstance(result, dict):
        return ["result must be an object"]
    eq(result.get("job_id"), job_id, "job_id")
    files = result.get("files")
    if not isinstance(files, list):
        return errors + ["files must be an array"]
    by_file = {}
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("task_id"), str):
            fail("invalid file result")
            continue
        if file["task_id"] in by_file:
            fail(f"duplicate file result: {file['task_id']}")
        by_file[file["task_id"]] = file
    if set(by_file) != set(expected):
        fail("file identities do not match input")
    evidence = defaultdict(list)
    for row in audit:
        identity = row["task_id"], row["record_id"]
        if identity[0] not in expected or identity[1] not in expected.get(identity[0], {}):
            fail(f"unexpected downstream record: {identity}")
            continue
        if row["input_hash"] != expected[identity[0]][identity[1]].fingerprint():
            fail(f"downstream input was changed: {identity}")
        if row["status"] != "overloaded":
            evidence[identity].append(row)
    totals = {"files": len(expected), "records": sum(map(len, expected.values())),
              "succeeded": 0, "failed": 0, "value_sum": 0}
    for task_id, records in expected.items():
        file = by_file.get(task_id, {})
        out_records = file.get("records", [])
        if not isinstance(out_records, list):
            fail(f"{task_id}.records must be an array")
            out_records = []
        by_record = {}
        for out in out_records:
            if not isinstance(out, dict) or not isinstance(out.get("record_id"), str):
                fail(f"{task_id}: invalid record result")
                continue
            if out["record_id"] in by_record:
                fail(f"{task_id}: duplicate record result {out['record_id']}")
            by_record[out["record_id"]] = out
        if set(by_record) != set(records):
            fail(f"{task_id}: record identities do not match input")
        succeeded = failed = value_sum = 0
        for rid, record in records.items():
            path = f"{task_id}/{rid}"
            out = by_record.get(rid, {})
            rows = evidence[path_to_key(task_id, rid)]
            attempts = {r["attempt"] for r in rows}
            reported = out.get("attempts")
            if type(reported) is not int or not 1 <= reported <= record.max_attempts:
                fail(f"{path}: invalid attempts count")
            else:
                if attempts != set(range(1, reported+1)):
                    fail(f"{path}: attempts do not match downstream audit")
                # A later logical attempt must follow a failed prior attempt.
                # Re-delivery of the SAME attempt may overlap; it is not new work.
                for number in range(2, reported+1):
                    previous = [r for r in rows if r["attempt"] == number-1 and r["status"] in {"transient_error", "deadline_exceeded"}]
                    current = [r for r in rows if r["attempt"] == number]
                    if not previous or (current and min(r["started_at"] for r in current) < min(r["finished_at"] for r in previous)):
                        fail(f"{path}: logical attempt {number} started without a preceding failure")
            successes = [r for r in rows if r["status"] == "ok"]
            if out.get("status") == "SUCCEEDED":
                succeeded += 1
                if type(out.get("value")) is not int:
                    fail(f"{path}: successful value must be an integer")
                else:
                    value_sum += out["value"]
                if not any(r["value"] == out.get("value") and r["receipt"] == out.get("receipt") for r in successes):
                    fail(f"{path}: result has no matching successful processor receipt")
            elif out.get("status") == "FAILED":
                failed += 1
                eq(out.get("error_code"), "ATTEMPTS_EXHAUSTED", f"{path}.error_code")
                eq(reported, record.max_attempts, f"{path}.attempts")
                failed_attempts = {r["attempt"] for r in rows if r["status"] in {"transient_error", "deadline_exceeded"}}
                if successes or failed_attempts != set(range(1, record.max_attempts+1)):
                    fail(f"{path}: declared failed without exhausting genuine failed attempts")
                if "value" in out or "receipt" in out:
                    fail(f"{path}: failed record must not contain a result")
            else:
                fail(f"{path}: record has no terminal status")
        eq(file.get("records_total"), len(records), f"{task_id}.records_total")
        eq(file.get("records_succeeded"), succeeded, f"{task_id}.records_succeeded")
        eq(file.get("records_failed"), failed, f"{task_id}.records_failed")
        eq(file.get("value_sum"), value_sum, f"{task_id}.value_sum")
        eq(file.get("status"), "FAILED" if failed else "SUCCEEDED", f"{task_id}.status")
        totals["succeeded"] += succeeded
        totals["failed"] += failed
        totals["value_sum"] += value_sum
    returned_totals = result.get("totals", {})
    if not isinstance(returned_totals, dict):
        fail("totals must be an object")
    else:
        for key, value in totals.items():
            eq(returned_totals.get(key), value, f"totals.{key}")
    eq(result.get("status"), "FAILED" if totals["failed"] else "SUCCEEDED", "status")
    return errors


def path_to_key(task_id, record_id):
    return task_id, record_id


def check_progress(snapshot, previous=None, terminal=False):
    errors = []
    fields = ("files_total", "files_terminal", "records_total", "records_succeeded", "records_failed", "records_running", "records_pending")
    if not isinstance(snapshot, dict):
        return ["progress must be an object"]
    if snapshot.get("status") not in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED"}:
        errors.append("invalid job status")
    if type(snapshot.get("ingestion_complete")) is not bool:
        errors.append("ingestion_complete must be boolean")
    if any(type(snapshot.get(k)) is not int or snapshot[k] < 0 for k in fields):
        return errors + ["progress counts must be non-negative integers"]
    if sum(snapshot[k] for k in ("records_succeeded", "records_failed", "records_running", "records_pending")) != snapshot["records_total"]:
        errors.append("record progress counts do not add up")
    if snapshot["files_terminal"] > snapshot["files_total"]:
        errors.append("files_terminal exceeds files_total")
    if previous and all(type(previous.get(k)) is int for k in fields):
        for key in ("records_succeeded", "records_failed", "files_terminal", "records_total", "files_total"):
            if snapshot[key] < previous[key]:
                errors.append(f"{key} moved backwards")
        if previous.get("ingestion_complete"):
            if not snapshot["ingestion_complete"] or any(snapshot[k] != previous[k] for k in ("files_total", "records_total")):
                errors.append("work appeared after ingestion was complete")
    if terminal or snapshot.get("status") in {"SUCCEEDED", "FAILED"}:
        if not snapshot.get("ingestion_complete") or snapshot["records_running"] or snapshot["records_pending"] or snapshot["files_total"] != snapshot["files_terminal"]:
            errors.append("job became terminal while work remained")
        wanted = "FAILED" if snapshot["records_failed"] else "SUCCEEDED"
        if snapshot.get("status") != wanted:
            errors.append("job status disagrees with failed-record count")
    return errors


def stable_result(result):
    """Ignore array ordering, not content changes."""
    copy = json.loads(json.dumps(result))
    if isinstance(copy, dict) and isinstance(copy.get("files"), list):
        copy["files"].sort(key=lambda f: f.get("task_id", "") if isinstance(f, dict) else "")
        for file in copy["files"]:
            if isinstance(file, dict) and isinstance(file.get("records"), list):
                file["records"].sort(key=lambda r: r.get("record_id", "") if isinstance(r, dict) else "")
    return canonical(copy)
