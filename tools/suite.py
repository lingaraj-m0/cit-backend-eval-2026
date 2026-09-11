"""Run named input presets against a candidate. Never processes records itself."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
from types import SimpleNamespace
import uuid

import httpx
from processor.env import load_env
from .bench import evaluate

ROOT = Path(__file__).resolve().parents[1]


def load_presets():
    return json.loads((ROOT/'benchmarks'/'presets.json').read_text(encoding='utf-8'))['presets']


def resolve_inputs(names):
    paths = []
    for name in names:
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise ValueError(f'Missing or unsafe fixture: {name}')
        paths.append(path)
    return paths


def run(args, preset):
    inputs = resolve_inputs(preset['inputs'])
    invalids = resolve_inputs(preset.get('invalid_inputs', []))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = args.output_dir/(args.preset+'-'+stamp+'-'+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    reports = []
    for number in range(1, args.repeats+1):
        output = directory/f'run-{number:02d}.json'
        bench_args = SimpleNamespace(candidate_url=args.candidate_url, processor_url=args.processor_url,
            input=inputs, invalid=invalids, run_id=None, admin_token=args.admin_token,
            deadline=args.deadline or preset['deadline_seconds'], http_timeout=args.http_timeout,
            poll_seconds=.2, settle_seconds=1, output=output)
        print(f'Preset {args.preset}: run {number}/{args.repeats}', flush=True)
        try:
            report = asyncio.run(evaluate(bench_args))
            report['harness_exit_code'] = 0 if report['correct'] else 2
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
            report = {'correct': False, 'valid_makespan_seconds': None, 'harness_exit_code': 1,
                      'error': f'{type(exc).__name__}: {exc}'}
        output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        reports.append(report)
        print(f"Correct: {report['correct']}; report: {output}", flush=True)
        if not report['correct']:
            print('Stopped. Inspect unfinished candidate work before starting another run.', flush=True)
            break
    all_valid = len(reports) == args.repeats and all(r['correct'] for r in reports)
    summary = {'preset': args.preset, 'requested_repeats': args.repeats,
               'completed_repeats': len(reports), 'all_runs_correct': all_valid,
               'median_valid_makespan_seconds': statistics.median(r['valid_makespan_seconds'] for r in reports) if all_valid else None,
               'reports': [f'run-{i:02d}.json' for i in range(1, len(reports)+1)]}
    (directory/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if all_valid else reports[-1]['harness_exit_code']


def main():
    load_env()
    presets = load_presets()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='list available presets; no server is contacted')
    parser.add_argument('--preset', choices=sorted(presets), default='smoke')
    parser.add_argument('--candidate-url', action='append')
    parser.add_argument('--processor-url', default='http://127.0.0.1:8001')
    parser.add_argument('--admin-token', default=os.getenv('PROCESSOR_ADMIN_TOKEN', 'local-admin-change-me'))
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--deadline', type=float, help='override this preset\'s real-wall-clock deadline in seconds')
    parser.add_argument('--http-timeout', type=float, default=60)
    parser.add_argument('--output-dir', type=Path, default=Path('reports'))
    args = parser.parse_args()
    if args.list:
        for name, item in presets.items():
            print(f"{name:18} {item['description']} (deadline: {item['deadline_seconds']}s)")
        return 0
    if not args.candidate_url:
        parser.error('--candidate-url is required unless --list is used')
    if args.repeats < 1 or args.http_timeout <= 0 or (args.deadline is not None and args.deadline <= 0):
        parser.error('repeats and timeouts must be positive')
    try:
        return run(args, presets[args.preset])
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
