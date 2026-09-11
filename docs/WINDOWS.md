# Windows notes

## Native support

The processor, generator, benchmark, verifier and tests use Python. The included
`.cmd` launchers run that interpreter directly; no Bash, WSL, Make, PowerShell
script activation, Unix signals or Windows administrator session is required for
the supplied local tooling. Run setup with Internet access to install dependencies.

The assignment does not require a Windows-specific backend. Document whatever
runtime your own implementation needs. A dependency you choose may require Docker
or another environment, even though the supplied kit does not.

## Commands and paths

Open PowerShell or Command Prompt in the extracted `cit-backend-eval-2026` folder.
For example, quote a path that contains spaces:

```powershell
Set-Location 'C:\Users\Student\Desktop\Backend Challenge\cit-backend-eval-2026'
.\setup.cmd
.\run-processor.cmd
```

The scripts resolve their own directory, so paths with spaces are supported.
The direct equivalent is:

```powershell
.\.venv\Scripts\python.exe -m processor
.\.venv\Scripts\python.exe -m tools.suite --list
```

Use one-line commands from the documentation. Bash's trailing `\` is not a
PowerShell line-continuation character. Use `curl.exe` for the curl examples, or
use the documented `Invoke-RestMethod` calls.

## Local configuration

Edit `.env` in a text editor and save as UTF-8. LF and CRLF, and an optional UTF-8
BOM, are accepted. Format: one `KEY=VALUE` per line; whole-line `#` comments and
matching outer quotes are supported. There is no shell expansion, escape expansion
or inline-comment syntax. Forward slashes in `PROCESSOR_DB=state/processor.sqlite3`
work with the provided Python launcher.

To override a setting temporarily in PowerShell before launching the server:

```powershell
$env:PROCESSOR_CAPACITY = '8'
.\run-processor.cmd
```

After stopping it, remove the override to use `.env` again:

```powershell
Remove-Item Env:PROCESSOR_CAPACITY
```

Command Prompt uses `set PROCESSOR_CAPACITY=8` instead. Restart the processor after
changing settings. Do not change capacity in the middle of an ordinary benchmark.
If local tokens change, keep the benchmark's `.env`/environment consistent with the
processor. Do not give evaluator tokens or signing keys to the candidate backend.

## Input handling

Treat ZIP members and their JSONL basenames as logical input identities. Apply the
published archive rules independently of the host OS. A Windows drive path, a UNC
path, a nested path, and `../...` are all invalid input members. The corpus includes
explicit Windows-path rejection cases.

JSONL is UTF-8. The valid edge-case archive includes CRLF line endings, Unicode,
blank lines, escaped newlines inside strings and a file with no trailing newline.
Do not rely on the Windows system code page when reading or writing JSON.

## Common problems

| Symptom | Check |
|---|---|
| `py` not found | Try `python --version`, then `python bootstrap.py`; install Python 3.11+ if neither works. |
| Windows opens the Store instead of Python | Use the installed Python launcher/interpreter and check the Windows app-execution alias settings. |
| `Activate.ps1` blocked | Activation is unnecessary. Use the `.cmd` shortcuts or `.venv\Scripts\python.exe`. |
| `Address already in use` | Stop the other native/Docker simulator or use a different port consistently. |
| Benchmark cannot reach port 8080 | Your candidate backend must be implemented and running; the kit does not provide it. |
| HTTP 429 | Documented finite-capacity response, not a Windows installation issue. |
| HTTP 503/504 | Check the documented simulated failure/deadline behavior. |
| Permission error deleting audit database | Stop the processor first; do not delete its open SQLite files. |
| Moved project or copied `.venv` from another OS | Recreate `.venv` with setup instead of reusing it. |

Use `Get-NetTCPConnection -LocalPort 8001` in PowerShell or `netstat -ano` in Command
Prompt to inspect a port. Stop only processes you own and recognize. Ctrl+C in the
server's terminal is preferred for normal shutdown.

## Containers are optional

If you choose Docker Desktop with Linux containers, a host service is normally
reachable from a container using `host.docker.internal`, not container-local
`127.0.0.1`. The processor must listen on an interface reachable by that route;
loopback-only binding is not sufficient in every topology. Native host processes
can continue using `http://127.0.0.1:8001`.

Do not expose the simulator's admin API to an untrusted network. Keep its port
private through your binding/firewall/container-network configuration. Setting
`PROCESSOR_HOST=0.0.0.0` increases exposure; it is not required for the normal native
Windows setup. A Linux-host Docker setup may require an explicit host-gateway
mapping; consult your runtime's documentation.

## Reproducible comparisons

Run all graded submissions on the same evaluator machine and resource budget.
Student laptop results are useful for tuning but not directly comparable across
machines. Antivirus activity, background load and filesystem speed can affect
observed timing. The configured record durations do not remove those differences.

The repository includes Windows/Linux CI jobs. Their presence is not evidence that
this downloaded package has already run on your Windows version; see the packaging
validation report. Official platform references are in `REFERENCES.md`.
