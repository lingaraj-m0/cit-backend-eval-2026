# Submission

Submit the URL of your public GitHub fork containing your completed backend.

Your fork must include:

- The backend source code and dependency configuration.
- Setup and run instructions for the API and at least two workers on the supported development platform.
- `.env.example` with every required configuration variable documented.
- Tests covering the important state, retry, idempotency, and aggregation behavior.
- `DESIGN.md` describing the state model, crash and restart behavior, performance measurements, tradeoffs, and limitations.
- Metrics for the implementation where practical, covering individual tasks, complete jobs, and overall service behavior.

The submission must be usable in a documented environment. Windows is not required;
Linux, macOS, or Docker are acceptable. If Docker is supported, include a
`Dockerfile` and, where needed, a `compose.yaml` or equivalent commands that start
the API and at least two workers with persistent job state. Document how the
container reaches the supplied processor, including the processor URL to use when
the processor runs on the host (for example, `host.docker.internal:8001`).

The instructions must include commands to:

1. Start the API and two workers.
2. Start the supplied processor and run the supplied benchmark against the backend.
3. Restart a worker while work is active without deleting job state.

Do not commit local virtual environments, `.env`, processor state, benchmark reports, credentials, or generated temporary files. Keep the supplied processor, fixtures, benchmark, and verifier unchanged unless a change is explicitly requested.

Be prepared to explain the implementation and make a small change during review, including any generated or borrowed code.
