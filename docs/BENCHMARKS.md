# Benchmarking and evaluation

## Windows: named presets

Keep the supplied processor and **your own backend** running in separate terminals.
From `cit-backend-eval-2026`, use PowerShell:

```powershell
.\run-benchmark.cmd --list
.\run-benchmark.cmd --candidate-url http://127.0.0.1:8080 --preset smoke
.\run-benchmark.cmd --candidate-url http://127.0.0.1:8080 --preset sustained --repeats 3
.\run-benchmark.cmd --candidate-url http://127.0.0.1:8080 --preset overlapping
```

For Linux/macOS replace `.\run-benchmark.cmd` with `.venv/bin/python -m tools.suite`.
No command starts or implements a candidate backend. The preset selects archives,
invalid cases and a run deadline; it does not tune a candidate or scale durations.
Inspect `benchmarks/presets.json` to see every preset. Each run gets a fresh run ID.

`reports/<preset>-<timestamp>-<suffix>/` contains per-run reports and raw audits,
plus `summary.json`. Median valid makespan is reported only when all requested
repetitions pass. An incorrect or interrupted run stops further repetitions to
avoid silently measuring against old, still-active candidate work.

## Arbitrary input selection

```powershell
.\kit.cmd -m tools.bench --candidate-url http://127.0.0.1:8080 --input samples/sustained.zip samples/tiny-job.zip --deadline 1200 --output reports/custom.json
```

Multiple inputs are submitted in order without waiting for earlier jobs to finish.
Timing begins immediately before each upload. The harness replays submissions with
the same idempotency key, polls progress and independently verifies terminal results.
It does not assert that jobs overlapped when the workload completed before the next
submission; use a sustained input when testing overlap behavior.

For API replicas sharing state:

```powershell
.\kit.cmd -m tools.bench --candidate-url http://127.0.0.1:8080 --candidate-url http://127.0.0.1:8082 --input samples/smoke.zip --output reports/replicas.json
```

Requests rotate across APIs, including replayed submissions. API replicas are an
extension; independent worker processes are a core requirement. The benchmark never
selects scheduling policies, executes records for the candidate, or reads its database.

The processor defaults to `http://127.0.0.1:8001`; use `--processor-url` when needed.
The tools read the local admin token from `.env` for result verification. The
candidate itself must not call admin endpoints. The direct harness defaults to a
600-second overall deadline; named presets have their own explicit deadlines.


## What it checks

The verifier checks exact file/record coverage, processor receipts and values, unmodified downstream inputs, logical retry sequence, evidence for exhausted failures, per-file and archive sums, consistent progress, immutable terminal results, and duplicate submission identity. A valid terminal `FAILED` result can pass when its failures are genuine and exhausted.

An input record cannot simply be omitted or reported as failed to improve timing. A locally computed value without a corresponding successful processor receipt cannot pass.

Optional invalid-input tests:

```powershell
.\kit.cmd -m tools.bench --candidate-url http://127.0.0.1:8080 --input samples/smoke.zip --invalid samples/invalid-path.zip samples/invalid-record.zip
```

## Report

The JSON report includes:

- `correct` and detailed validation errors;
- `valid_makespan_seconds`, populated **only** for correct runs;
- per-job observed latency and mean job latency;
- processor admitted requests, overload rejections, duplicate admissions, peak active work, and mean/p95 service time.

An adjacent `.audit.jsonl` contains the evidence used for verification. Treat evaluation reports/audits as evaluator material; do not commit them. Local reports contain no signing secret.

Makespan starts immediately before the first upload and ends after all terminal results have been received. It includes ingestion, retries, progress polling delay, and result transfer. Audit verification and the final stability-check delay are excluded. Default polling is 200 ms, so very short runs have measurement noise. Service-latency metrics describe server processing, not full client/network latency.

Exit codes: **0** = correct; **2** = completed evaluation but incorrect; **1** = harness could not complete, for example an unavailable endpoint, invalid fixture, or malformed protocol response. A code of 1 is never a valid performance result.

## Workload selection and real durations

The full corpus is documented in [samples/README.md](../samples/README.md). Start
with `smoke`, then use `sustained`, `long-tail`, `uneven-files`, and `overlapping`.
`stress-100k` and `sustained-large` are optional longer runs. Edge-case and
many-file presets primarily test correctness, not scheduler performance.

The main `sustained` input contains **600 records and 1,267.097 seconds of total
nominal work**. A capacity-20 arithmetic reference is `1267.097 / 20 = 63.355`
seconds before retries, jitter, ingestion and finalization. This is **not a measured
runtime or a strict lower bound**. It is a sizing reference showing why this input
is not a sub-five-second demonstration. A lower-capacity server can take several
minutes. Preset deadlines leave headroom; they are not target or predicted runtimes.

Durations are already in the input. To make a different workload, generate another
input profile; the simulator does not have a time-scale multiplier. A processor
`timeout_ms` limits an individual attempt. The harness `--deadline` separately limits
the entire measured run; changing that deadline does not speed up processing.

The generator keeps the legacy short `smoke`, `mixed`, `burst`, `long-tail` and
`timeouts` profiles, and adds `sustained`, `sustained-tail`, `deadline-heavy` and
`attempt-mix`. The checked-in `long-tail.zip` uses `sustained-tail`. For uneven file
sizes use `--file-counts`. Generate fresh seeds rather than optimizing only for
checked-in samples. Use identical input content and processor seed for comparisons.
ZIP timestamps/member metadata are fixed; compressed bytes can still differ across
Python/zlib versions, so distribute the same input archive to every candidate.

## What still needs a live review

The public harness is not proof of distributed correctness. The evaluator will also inspect tests and run documented demonstrations: restart a worker while work is active, redeliver work using the candidate's mechanism, add another worker, and check that state survives and aggregates remain correct. Multiple API instances, fair treatment of small jobs, and richer scheduling are extension discussions, not undisclosed mandatory APIs.

Predict the result before each intervention. Be prepared to explain the actual code and make a small change to it. Source access or generated code does not replace that explanation.

## Fair comparisons

Use the same processor profile and fixture per comparison, no competing traffic, and an announced resource budget for the candidate's API/workers and dependencies. Run timed profiles at least three times and compare median valid makespan. A small number of overload responses while adapting to unknown capacity is not automatically a correctness failure. Uncontrolled request volume, excessive duplication, memory exhaustion, and inability to finish are relevant evidence.

Do not grade by library names or the presence of a particular queue/database. Correctness precedes speed. There is no requirement to discover an exact hidden capacity or to implement an optimal scheduler.
