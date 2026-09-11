# Input and candidate API contract

This document defines externally observable behavior. It does not prescribe architecture, storage, scheduling, or algorithms.

## Input hierarchy

```text
export.zip                       -> job
  source-a.jsonl                 -> task
    one JSON object per line     -> record/subtask
  source-b.jsonl                  -> another task
```

Every non-empty line contains one object:

```json
{"record_id":"record-001","payload":{"text":"Example"},"work_ms":250,"timeout_ms":1000,"max_attempts":3}
```

| Field | Meaning |
|---|---|
| `record_id` | 1-64 characters; first character alphanumeric, remaining characters alphanumeric, `_`, or `-`. Unique within a file, not necessarily across files. |
| `payload` | JSON object, at most 16,384 UTF-8 bytes in compact canonical JSON. Treat it as data to send to the processor. |
| `work_ms` | Integer, 25-60,000. Nominal processor duration. Not a promise that the response will arrive in exactly this time. |
| `timeout_ms` | Integer, 25-120,000. Processor-side execution budget for one logical attempt. It is not a job deadline. |
| `max_attempts` | Integer, 1-5; default 3. Includes the initial logical attempt. |

Unknown record fields, duplicate JSON object keys, non-finite numbers, and values of the wrong type are invalid. JSON must be UTF-8. Accept LF and CRLF line endings and a final record without a trailing newline. Blank lines can be ignored; a file must contain at least one record.

An identity is `(job_id, task_id, record_id)`, not `record_id` alone. Use the exact JSONL basename as `task_id`.

### Archive constraints

Support ZIP_STORED and ZIP_DEFLATED archives containing 1-1,000 unencrypted regular JSONL files at the archive root. Filenames must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.jsonl$`. Directory entries, nested paths, absolute paths, symlinks, and duplicate filenames are invalid. In particular, `/etc/...`, `../...`, Windows drive paths, and UNC paths are invalid, regardless of the OS running your backend.

Maximum compressed upload size: **64 MiB**. Maximum total expanded data: **256 MiB**. Maximum non-empty records across the archive: **100,000**. Maximum bytes per JSONL line, including its newline if present: **65,536**.

Reject an invalid archive before any of its records are sent downstream. Return HTTP 400, 413, or 422. No partially accepted job is required for invalid input.

## Candidate endpoints

### `POST /jobs`

Content type: `multipart/form-data`, with the ZIP in field `file`.

Required headers:

```text
X-Run-ID: a caller-provided run identifier
Idempotency-Key: a caller-provided submission identifier
```

Persist `X-Run-ID` with the job and forward it as `run_id` in processor calls. It allows test traffic to be attributed; it is not an authentication token or a scheduling hint.

Return **202** after accepting a new valid job:

```json
{"job_id":"job-123","status":"QUEUED"}
```

`job_id` and `X-Run-ID` must match `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`. Status may already be `RUNNING` if work started immediately.

The idempotency-key scope is the run ID. Repeating the same key and identical ZIP bytes must return the same job ID, with HTTP 200 or 202, without creating more logical work. Reusing the key for different bytes must return 409. Different keys represent distinct jobs even if the bytes are identical. Concurrent/restarted API instances must not change this behavior when that deployment mode is demonstrated.

### `GET /jobs/{job_id}`

Return 200 with:

```json
{
  "job_id":"job-123",
  "status":"RUNNING",
  "ingestion_complete":true,
  "files_total":2,
  "files_terminal":0,
  "records_total":10,
  "records_succeeded":4,
  "records_failed":1,
  "records_running":2,
  "records_pending":3
}
```

Allowed job states are `QUEUED`, `RUNNING`, `SUCCEEDED`, and `FAILED`. You may use a richer internal state model.

Counts describe **unique logical records**, not delivery attempts. Pending includes work waiting to retry. At every snapshot:

```text
records_total = records_succeeded + records_failed + records_running + records_pending
files_terminal <= files_total
```

During discovery, totals can increase. Once `ingestion_complete` becomes true, totals must not change. Successful/failed record counts and terminal-file counts cannot decrease.

A terminal job has complete ingestion, zero running/pending records, and all files terminal. It is `SUCCEEDED` only if every record succeeded, otherwise `FAILED`. Failure is not permission to stop processing other records. In particular, an exhausted record must not cause premature job completion.

Return 404 for unknown jobs. Percentage progress is optional; these counts are required.

### `GET /jobs/{job_id}/result`

Return 409 while the job is nonterminal, 404 if unknown, and 200 when terminal. Example with one file and two records:

```json
{
  "job_id":"job-123",
  "status":"FAILED",
  "files":[{
    "task_id":"source-a.jsonl",
    "status":"FAILED",
    "records_total":2,
    "records_succeeded":1,
    "records_failed":1,
    "value_sum":123,
    "records":[
      {"record_id":"record-001","status":"SUCCEEDED","attempts":1,"value":123,"receipt":"<processor-receipt>"},
      {"record_id":"record-002","status":"FAILED","attempts":3,"error_code":"ATTEMPTS_EXHAUSTED"}
    ]
  }],
  "totals":{"files":1,"records":2,"succeeded":1,"failed":1,"value_sum":123}
}
```

Exactly one entry is required for every input file and record. Successful entries must contain the unmodified processor `value` and `receipt`. Failed entries must not contain either field. `attempts` is the highest admitted **logical attempt number**, not a count of HTTP requests or 429s.

Per-file `value_sum` sums each successful logical record once. The archive sum adds file sums. A file is `FAILED` if any of its records failed, otherwise `SUCCEEDED`. Published results must be stable; array ordering is not significant. Do not add a freshly generated timestamp on every result read.

### `GET /health`

Return 200 when the candidate API is available, for example `{"status":"ok"}`. No UI is required.

## Scope boundaries

Input records are all declared in the uploaded files. The processor does not create further children. Cancellation, authentication, arbitrary dependency graphs, dynamic recursive fan-out, and autoscaling are not required. These may be discussed during review, but are not hidden mandatory features.
