# Sample inputs

Upload a ZIP, not its manifest. Do not manually extract the invalid archives.
A ZIP is one job, each JSONL file is a task, and each nonempty line is a record/subtask.
The supplied corpus contains 17 valid archives and 10 deliberately invalid archives.

## Valid archives

`work_ms` below is nominal **real milliseconds**, before the documented jitter.
Total nominal work is the sum across records for one attempt, not elapsed runtime.

| Archive | Files | Records | Nominal duration range (ms) | Total nominal work (seconds) | Purpose |
|---|---:|---:|---:|---:|---|
| `single-record.zip` | 1 | 1 | 49-49 | 0.05 | Smallest valid archive. |
| `smoke.zip` | 3 | 24 | 25-100 | 1.51 | Fast setup and correctness check; not a speed-ranking workload. |
| `mixed.zip` | 5 | 200 | 50-3,970 | 72.24 | Legacy short mixed workload; keep for development. |
| `timeouts.zip` | 2 | 24 | 157-347 | 6.17 | Fast retry/deadline correctness check. |
| `sustained.zip` | 12 | 600 | 402-26,913 | 1,267.10 | Main performance workload; 600 heterogeneous real-duration records. |
| `sustained-large.zip` | 24 | 2,400 | 400-29,649 | 5,473.40 | Optional longer sustained workload; 2,400 records. |
| `single-file.zip` | 1 | 500 | 400-29,066 | 1,116.94 | Many subtasks inside one file/task. |
| `long-tail.zip` | 10 | 500 | 302-38,181 | 966.49 | Includes 20-40 second records; exposes completion stragglers. |
| `burst-10000.zip` | 40 | 10,000 | 25-60 | 425.55 | 10,000 short calls; input and request-volume pressure. |
| `burst-100000.zip` | 100 | 100,000 | 25-60 | 4,248.03 | Optional maximum-record-count stress input; allow several minutes. |
| `many-files.zip` | 1,000 | 1,000 | 25-100 | 64.06 | 1,000 file-level tasks with one record each; hierarchy/aggregation check. |
| `uneven-files.zip` | 7 | 600 | 401-29,379 | 1,489.55 | File sizes vary from 1 to 400 records. |
| `timeout-heavy.zip` | 8 | 200 | 1,046-5,959 | 673.62 | Longer calls with impossible and borderline processor deadlines. |
| `attempt-mix.zip` | 6 | 240 | 200-1,784 | 249.23 | Retry budgets from 1 to 5; includes impossible deadlines. |
| `tiny-job.zip` | 1 | 5 | 46-100 | 0.34 | Tiny job to submit while a large job is active. |
| `small-job.zip` | 2 | 40 | 54-2,691 | 14.11 | Small job for overlapping-job measurements. |
| `text-edge-cases.zip` | 3 | 18 | 30-96 | 1.23 | UTF-8 payloads, CRLF lines, blank lines and a missing final newline. |

For `sustained.zip`, dividing total nominal work by 20 gives a useful capacity-20
reference, not a measured completion time. Retries, jitter, deadlines, startup,
ingestion and finalization affect the actual result. Capacity is evaluator-controlled.
Short smoke and edge-case inputs are deliberately not performance benchmarks.
`burst-100000` and `sustained-large` are optional stress runs, not default quick checks.

Use `python -m tools.suite --list` to see named benchmark presets. See
[benchmark instructions](../docs/BENCHMARKS.md) for Windows commands.

## Invalid archives

Every archive below must be rejected before any record is sent downstream.
- `invalid-empty.zip`: empty.
- `invalid-path.zip`: unsafe-path.
- `invalid-duplicate-record.zip`: duplicate-record.
- `invalid-malformed-json.zip`: malformed-json.
- `invalid-duplicate-file.zip`: duplicate-file.
- `invalid-record.zip`: invalid-record.
- `invalid-windows-drive-path.zip`: windows-drive-path.
- `invalid-windows-unc-path.zip`: windows-unc-path.
- `invalid-nested-path.zip`: nested-path.
- `invalid-directory-entry.zip`: directory-entry.

## Extra files

`processor-request.json` is a complete request for a manual processor API call.
`example.jsonl` illustrates individual records; it is not an upload by itself.
`catalog.json` and per-archive manifests are descriptions for humans/tools and are
not part of the candidate input contract. They contain no evaluation capacity.

To rebuild this corpus, run `python -m tools.make_samples` from the repository root.
Generation uses fixed seeds, timestamps, member metadata and UTF-8 bytes. Repeated
generation is reproducible in the same Python/zlib environment; compressed ZIP bytes
can differ between zlib versions even when uncompressed records are identical.
