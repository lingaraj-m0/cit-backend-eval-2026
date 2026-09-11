"""Rebuild the checked-in input corpus. Generates files; never executes records."""
import argparse
import json
from pathlib import Path
from .generate import generate, invalid_archive, text_edge_archive, INVALID_KINDS

SAMPLES = [
    ('single-record', 'smoke', 1, 1, 101, None, 'Smallest valid archive.'),
    ('smoke', 'smoke', 3, 8, 42, None, 'Fast setup and correctness check; not a speed-ranking workload.'),
    ('mixed', 'mixed', 5, 40, 81, None, 'Legacy short mixed workload; keep for development.'),
    ('timeouts', 'timeouts', 2, 12, 90, None, 'Fast retry/deadline correctness check.'),
    ('sustained', 'sustained', 12, 50, 2026, None, 'Main performance workload; 600 heterogeneous real-duration records.'),
    ('sustained-large', 'sustained', 24, 100, 2027, None, 'Optional longer sustained workload; 2,400 records.'),
    ('single-file', 'sustained', 1, 500, 214, None, 'Many subtasks inside one file/task.'),
    ('long-tail', 'sustained-tail', 10, 50, 407, None, 'Includes 20-40 second records; exposes completion stragglers.'),
    ('burst-10000', 'burst', 40, 250, 73, None, '10,000 short calls; input and request-volume pressure.'),
    ('burst-100000', 'burst', 100, 1000, 74, None, 'Optional maximum-record-count stress input; allow several minutes.'),
    ('many-files', 'smoke', 1000, 1, 501, None, '1,000 file-level tasks with one record each; hierarchy/aggregation check.'),
    ('uneven-files', 'sustained', 7, 1, 502, [1, 3, 7, 19, 50, 120, 400], 'File sizes vary from 1 to 400 records.'),
    ('timeout-heavy', 'deadline-heavy', 8, 25, 503, None, 'Longer calls with impossible and borderline processor deadlines.'),
    ('attempt-mix', 'attempt-mix', 6, 40, 504, None, 'Retry budgets from 1 to 5; includes impossible deadlines.'),
    ('tiny-job', 'smoke', 1, 5, 947, None, 'Tiny job to submit while a large job is active.'),
    ('small-job', 'mixed', 2, 20, 948, None, 'Small job for overlapping-job measurements.'),
]


def build_samples(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, profile, files, count, seed, counts, description in SAMPLES:
        manifest = generate(output/(name+'.zip'), profile=profile, files=files,
                            records_per_file=count, seed=seed, record_counts=counts)
        entries.append({'name': name, 'archive': name+'.zip', 'description': description, **manifest})
    manifest = text_edge_archive(output/'text-edge-cases.zip')
    entries.append({'name': 'text-edge-cases', 'archive': 'text-edge-cases.zip',
                    'description': 'UTF-8 payloads, CRLF lines, blank lines and a missing final newline.', **manifest})
    invalids = []
    for kind in INVALID_KINDS:
        name = ('invalid-path' if kind == 'unsafe-path' else
                'invalid-record' if kind == 'invalid-record' else 'invalid-'+kind)
        invalid_archive(output/(name+'.zip'), kind)
        invalids.append({'archive': name+'.zip', 'reason': kind})
    catalog = {'version': 2, 'valid': entries, 'invalid': invalids}
    (output/'catalog.json').write_text(json.dumps(catalog, indent=2)+'\n', encoding='utf-8', newline='\n')
    rows = []
    for item in entries:
        rows.append(f"| `{item['archive']}` | {item['files']:,} | {item['records']:,} | "
                    f"{item['work_ms']['min']:,}-{item['work_ms']['max']:,} | "
                    f"{item['base_work_ms']/1000:,.2f} | {item['description']} |")
    guide = """# Sample inputs

Upload a ZIP, not its manifest. Do not manually extract the invalid archives.
A ZIP is one job, each JSONL file is a task, and each nonempty line is a record/subtask.
The supplied corpus contains 17 valid archives and 10 deliberately invalid archives.

## Valid archives

`work_ms` below is nominal **real milliseconds**, before the documented jitter.
Total nominal work is the sum across records for one attempt, not elapsed runtime.

| Archive | Files | Records | Nominal duration range (ms) | Total nominal work (seconds) | Purpose |
|---|---:|---:|---:|---:|---|
"""+ '\n'.join(rows)+"""

For `sustained.zip`, dividing total nominal work by 20 gives a useful capacity-20
reference, not a measured completion time. Retries, jitter, deadlines, startup,
ingestion and finalization affect the actual result. Capacity is evaluator-controlled.
Short smoke and edge-case inputs are deliberately not performance benchmarks.
`burst-100000` and `sustained-large` are optional stress runs, not default quick checks.

Use `python -m tools.suite --list` to see named benchmark presets. See
[benchmark instructions](../docs/BENCHMARKS.md) for Windows commands.

## Invalid archives

Every archive below must be rejected before any record is sent downstream.
"""+ '\n'.join(f"- `{i['archive']}`: {i['reason']}." for i in invalids)+"""

## Extra files

`processor-request.json` is a complete request for a manual processor API call.
`example.jsonl` illustrates individual records; it is not an upload by itself.
`catalog.json` and per-archive manifests are descriptions for humans/tools and are
not part of the candidate input contract. They contain no evaluation capacity.

To rebuild this corpus, run `python -m tools.make_samples` from the repository root.
Generation uses fixed seeds, timestamps, member metadata and UTF-8 bytes. Repeated
generation is reproducible in the same Python/zlib environment; compressed ZIP bytes
can differ between zlib versions even when uncompressed records are identical.
"""
    (output/'README.md').write_text(guide, encoding='utf-8', newline='\n')
    request = {'run_id': 'manual-check', 'job_id': 'manual-job', 'task_id': 'source.jsonl', 'attempt': 1,
               'record': {'record_id': 'row-001', 'payload': {'text': 'Example export record'},
                          'work_ms': 2000, 'timeout_ms': 5000, 'max_attempts': 3}}
    (output/'processor-request.json').write_text(json.dumps(request, indent=2)+'\n', encoding='utf-8', newline='\n')
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[1]/'samples')
    args = parser.parse_args()
    try:
        catalog = build_samples(args.output_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Created {len(catalog['valid'])} valid and {len(catalog['invalid'])} invalid archives in {args.output_dir}")


if __name__ == '__main__':
    main()
