# Run the supplied tools

The supplied tools support native Windows, Linux, and macOS. Docker is optional;
WSL, Bash, and an activated virtual environment are not required to run the kit.
Your candidate backend may use any language and have its own dependencies.

Use Python 3.11 or newer. The supplied CI matrix targets 3.11, 3.12 and 3.13 on
Windows and Linux. The setup downloads the pinned Python dependencies.

## Windows: PowerShell or Command Prompt

Extract the complete distribution first. Open a terminal in `cit-backend-eval-2026`.
Do not execute files from the ZIP preview in File Explorer.

### 1. Set up

In PowerShell:

```powershell
py -3 --version
.\setup.cmd
```

If the `py` command is not installed but `python` starts Python 3.11 or newer:

```powershell
python bootstrap.py
```

Setup creates `.venv`, installs the supplied tools and copies `.env.example` to
`.env` **only if `.env` does not already exist**. It does not overwrite your local
settings. An existing virtual environment must belong to the current OS; do not
copy `.venv` between machines.

There is no `Activate.ps1` step and no need to change PowerShell execution policy.
The `.cmd` shortcuts use the virtual environment's interpreter directly. In
Command Prompt, the same scripts can be run as `setup.cmd`, `run-processor.cmd`,
`run-benchmark.cmd` and `run-tests.cmd` without the leading `.\`.

### 2. Start the processor

```powershell
.\run-processor.cmd
```

Leave this terminal open. The API listens at `http://127.0.0.1:8001`; its interactive
documentation is at `http://127.0.0.1:8001/docs`. Stop it with Ctrl+C.

Both the processor and benchmark entry points read `.env` from their working
directory automatically. The shortcuts set that directory to this repository.
Already-exported environment variables take precedence over `.env`.

Run **one simulator process**. Its finite capacity is shared across all candidate
jobs/workers; starting more simulator processes changes the test environment.

### 3. Try the processor from a second PowerShell window

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8001/health
$body = Get-Content -Raw -Encoding UTF8 .\samples\processor-request.json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/v1/process -ContentType 'application/json' -Body $body
```

The example requests approximately two seconds of real processing, plus seeded
jitter. A simulated HTTP 503 is possible, not necessarily a setup error.
[PROCESSOR.md](docs/PROCESSOR.md) specifies attempt numbering and error behavior.

For a Command Prompt alternative, or an explicit curl executable in PowerShell:

```text
curl.exe http://127.0.0.1:8001/health
curl.exe -H "Content-Type: application/json" --data-binary "@samples/processor-request.json" http://127.0.0.1:8001/v1/process
```

Use `curl.exe`, not a possibly aliased `curl`. Reading the JSON from a file avoids
shell-dependent escaping of its quotation marks.

### 4. Start YOUR backend separately

This kit contains **no reference executor or candidate backend**. Implement the
[contract](docs/CONTRACT.md), run your service, and point its processor client at
`http://127.0.0.1:8001`. Candidate API examples below assume port 8080.

A manual upload after your backend exists:

```text
curl.exe -X POST http://127.0.0.1:8080/jobs -H "X-Run-ID: manual-upload" -H "Idempotency-Key: first-upload" -F "file=@samples/smoke.zip;type=application/zip"
```

### 5. Run benchmarks

In another terminal at the repository root:

```powershell
.\run-benchmark.cmd --list
.\run-benchmark.cmd --candidate-url http://127.0.0.1:8080 --preset smoke
.\run-benchmark.cmd --candidate-url http://127.0.0.1:8080 --preset sustained
.\run-benchmark.cmd --candidate-url http://127.0.0.1:8080 --preset overlapping
```

`smoke` is a fast correctness check. **Use `sustained` for performance**, not the
legacy short `mixed` sample. The preset selects inputs and a wall-clock deadline;
it does not change their processing durations or discover downstream capacity.

A connection error is expected until your own backend is running. Reports go in
`reports/`. Stop and diagnose a failed run before launching another: unfinished
candidate work may otherwise consume capacity during the next measurement.

For an arbitrary fixture:

```powershell
.\kit.cmd -m tools.bench --candidate-url http://127.0.0.1:8080 --input samples/uneven-files.zip --deadline 900 --output reports/custom.json
```

### 6. Generate inputs and run the kit tests

```powershell
.\kit.cmd -m tools.generate --output generated/custom.zip --profile sustained --files 10 --records-per-file 60 --seed 902
.\kit.cmd -m tools.generate --output generated/uneven.zip --profile sustained --file-counts 1 5 20 100 400 --seed 903
.\kit.cmd -m tools.make_samples
.\run-tests.cmd -q
```

The tests check the supplied simulator, fixtures and benchmark, **not your backend**.
Generated ZIPs are inputs; adjacent manifests are descriptions and must not be
uploaded. See [samples/README.md](samples/README.md) for the complete corpus.

## Linux / macOS

From `cit-backend-eval-2026`:

```bash
python3 bootstrap.py
.venv/bin/python -m processor
```

In a second terminal, after starting your candidate backend:

```bash
.venv/bin/python -m tools.suite --candidate-url http://127.0.0.1:8080 --preset smoke
.venv/bin/python -m tools.suite --candidate-url http://127.0.0.1:8080 --preset sustained
.venv/bin/python -m pytest -q
```

No activation or shell sourcing of `.env` is required.

## Optional Docker

After creating `.env`, `docker compose up --build -d` starts only the simulator,
not your candidate. The native Windows route is sufficient for the supplied kit.
Do not run the Docker and native simulators on the same port. Stop Docker with
`docker compose down`; its audit data remains in the `processor-state` volume.

A candidate running inside a container needs a processor address reachable from
that container. `127.0.0.1` inside a container is not the Windows host. See
[WINDOWS.md](docs/WINDOWS.md) before mixing host and container processes.

## Resetting local audit data

The harness creates a fresh run ID for each run. Usually no reset is necessary.
To remove native audit data, stop the processor first and remove `state/`.
`docker compose down -v` removes the Docker audit volume. Never delete candidate
state while demonstrating restart recovery.
