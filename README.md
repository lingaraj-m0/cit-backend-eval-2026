# Backend: batch processing service

## Problem statement

A team receives exports from different systems as ZIP archives containing JSONL files. Each record needs to be processed by an external service before the export can be used. The external service is slow, has finite capacity, and sometimes fails.

Build a backend that accepts these archives, processes their records, tracks progress, and produces a final result for each file and for the complete archive.

One uploaded ZIP is a **job**. Each JSONL file is a **task**. Each non-empty line is a **record/subtask** requiring one logical call to the provided processor. The hierarchy is fixed by the input; arbitrary recursive workflows are not required.

No frontend, authentication system, real AI model, or document-processing library is required.

## Core requirements

### Ingestion and APIs

Implement these endpoints using the contract in [docs/CONTRACT.md](docs/CONTRACT.md):

```text
POST /jobs
GET  /jobs/{job_id}
GET  /jobs/{job_id}/result
GET  /health
```

Accept a ZIP upload, validate it, and return a job identifier. Repeating the same submission with the same idempotency key must not create another job. Reject invalid records and unsafe archives before submitting their records to the processor.

### Processing

Use the provided API to process every record. Records have different processing times, deadlines, and retry budgets. The processor normally fails approximately 5% of logical attempts and rejects excess concurrent requests.

Multiple jobs and independently running workers must be supported. A worker restart must not lose accepted work. Repeated execution must not create duplicate logical results or inflate aggregates. Work that has exhausted its retry budget must reach a terminal failure state rather than retry forever.

The exact downstream capacity is not part of the contract. It is fixed within an evaluation run but may differ between runs. You receive the processor source and a local configuration; evaluation configuration is controlled by the evaluator.

### State and results

Expose internally consistent progress. A file is terminal only when all its records are terminal. A job is terminal only when ingestion is complete and every file is terminal. Continue processing other records when one record fails.

Return each successful record's processor output and receipt, each failed record's terminal error, a per-file sum of successful values, and an archive-wide sum. Failed records do not contribute a value. Published terminal results must not change.

### Efficiency

Minimize end-to-end completion time **without sacrificing correctness**. Evaluation includes uneven record durations, multiple files, overlapping jobs, and workloads substantially larger than the processor's capacity. Faster incorrect results receive no valid performance score.

A configurable, stable implementation is sufficient. The main performance inputs use actual second-scale processing durations; the short smoke inputs are only for correctness checks. Automatically discovering an optimal concurrency limit, implementing a general workflow language, and deploying Kubernetes are not required.

## Implementation rules

Choose your own language, database, broker, and service structure. General HTTP, database, queue, and concurrency libraries are allowed. Do not delegate the entire execution problem to an existing workflow/job orchestration framework: the submission must own its job state, retry behavior, and aggregation logic. No specific concurrency primitive is banned.

Do not modify or bypass the supplied processor during evaluation. Computing its output locally is not a substitute for calling it.

## What is provided

This repository contains the downstream simulator, 17 valid sample archives, 10 invalid-input cases, a fixture generator, and a black-box benchmark with named presets. **It does not contain the backend you are being asked to build.**

Start with [QUICKSTART.md](QUICKSTART.md). The provided tools run natively on Windows, Linux, and macOS; Docker is also documented as an optional route for the supplied processor. See [samples/README.md](samples/README.md) for workloads, [docs/PROCESSOR.md](docs/PROCESSOR.md) for its behavior, and [docs/BENCHMARKS.md](docs/BENCHMARKS.md) to test your submission.

## Submission requirements

See [SUBMISSION.md](SUBMISSION.md) for the fork submission format and checklist.

Submit a repository containing source code, setup instructions, `.env.example`, tests, and `DESIGN.md`. Include commands to start the API and at least two workers, run a benchmark, and restart a worker without deleting job state.

Explain your state model, what happens at relevant crash points, how you measured performance, the tradeoffs you made, and the limitations you did not solve. Include your own benchmark results and the exact resource/configuration settings used. Be prepared to explain and modify the implementation during review, including any generated or borrowed code.
