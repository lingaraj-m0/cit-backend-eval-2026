"""Deterministic input fixtures only; no executor or scheduling implementation."""
import argparse
import json
from pathlib import Path
import random
import warnings
import zipfile

PROFILES = ('smoke', 'mixed', 'burst', 'long-tail', 'timeouts',
            'sustained', 'sustained-tail', 'deadline-heavy', 'attempt-mix')
INVALID_KINDS = ('empty', 'unsafe-path', 'duplicate-record', 'malformed-json',
                 'duplicate-file', 'invalid-record', 'windows-drive-path',
                 'windows-unc-path', 'nested-path', 'directory-entry')


def make_record(rng: random.Random, index: int, file_index: int, profile: str):
    p = rng.random()
    if profile == 'smoke':
        work = rng.randint(25, 100)
    elif profile == 'burst':
        work = rng.randint(25, 60)
    elif profile == 'mixed':
        work = rng.randint(50, 350) if p < .85 else rng.randint(350, 1500) if p < .98 else rng.randint(2500, 4000)
    elif profile == 'long-tail':
        work = rng.randint(25, 150) if p < .90 else rng.randint(200, 800) if p < .99 else rng.randint(5000, 7000)
    elif profile == 'timeouts':
        work = rng.randint(150, 350)
    elif profile == 'sustained':
        work = (rng.randint(400, 1400) if p < .65 else
                rng.randint(1500, 4500) if p < .90 else
                rng.randint(5000, 10000) if p < .99 else rng.randint(20000, 30000))
    elif profile == 'sustained-tail':
        work = (rng.randint(300, 1200) if p < .88 else
                rng.randint(2000, 6000) if p < .97 else rng.randint(20000, 40000))
    elif profile == 'deadline-heavy':
        work = rng.randint(1000, 6000)
    elif profile == 'attempt-mix':
        work = rng.randint(200, 1800)
    else:
        raise ValueError(f'unknown profile: {profile}')
    timeout = min(120000, work * 2 + 100)
    if profile == 'timeouts' and p < .30:
        timeout = rng.randint(50, 100)
    elif profile == 'timeouts' and p < .40:
        timeout = work
    elif profile == 'deadline-heavy' and p < .30:
        timeout = rng.randint(200, 600)
    elif profile == 'deadline-heavy' and p < .50:
        timeout = work
    elif profile == 'attempt-mix' and p < .15:
        timeout = 75
    record = {
        # Repeated across files deliberately: identity is not record_id alone.
        'record_id': f'record-{index:06d}',
        'payload': {'text': f'Batch {file_index}, source record {index}',
                    'category': rng.choice(['alpha', 'beta', 'gamma'])},
        'work_ms': work, 'timeout_ms': timeout, 'max_attempts': 3,
    }
    if profile == 'attempt-mix':
        record['max_attempts'] = rng.randint(1, 5)
    return record


def member(name: str):
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 3  # Fixed metadata on Windows and POSIX alike.
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def summarize(records, counts, *, profile, seed):
    durations = sorted(r['work_ms'] for r in records)
    total = sum(durations)
    return {
        'manifest_version': 2, 'profile': profile, 'workload_seed': seed,
        'files': len(counts), 'records': len(records), 'records_per_file': counts,
        'base_work_ms': total,
        'nominal_first_attempt_budget_ms': sum(min(r['work_ms'], r['timeout_ms']) for r in records),
        'work_ms': {'min': durations[0], 'median': durations[len(durations)//2],
                    'p95': durations[max(0, (95*len(durations)+99)//100-1)], 'max': durations[-1]},
        'nominal_work_divided_by_20_seconds': round(total/20000, 3),
        'timing_note': 'Descriptive arithmetic, NOT a measured runtime or a strict lower bound. '
                       'Ignores jitter, retries, deadline cutoffs, dispatch, ingestion and finalization. '
                       'Actual capacity may differ from 20. work_ms is real milliseconds.',
    }


def write_manifest(path, manifest):
    path.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8', newline='\n')


def generate(path: Path, *, files: int = 3, records_per_file: int = 12,
             profile: str = 'smoke', seed: int = 42, record_counts: list[int] | None = None):
    if record_counts is None and (type(files) is not int or not 1 <= files <= 1000):
        raise ValueError('require 1..1000 files')
    counts = list(record_counts) if record_counts is not None else [records_per_file]*files
    if not 1 <= len(counts) <= 1000 or any(type(n) is not int or n < 1 for n in counts) or sum(counts) > 100000:
        raise ValueError('require 1..1000 files and 1..100000 total records')
    if profile not in PROFILES:
        raise ValueError('invalid profile')
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    all_records = []
    with zipfile.ZipFile(path, 'w') as archive:
        for f, count in enumerate(counts, 1):
            with archive.open(member(f'batch-{f:03d}.jsonl'), 'w') as out:
                for i in range(1, count+1):
                    record = make_record(rng, i, f, profile)
                    all_records.append(record)
                    out.write((json.dumps(record, separators=(',', ':'))+'\n').encode('utf-8'))
    manifest = summarize(all_records, counts, profile=profile, seed=seed)
    write_manifest(path, manifest)
    return manifest


def text_edge_archive(path: Path):
    """Valid UTF-8, CRLF, blank lines and missing final newline fixtures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(91)
    texts = ['C:\\Exports\\September\\data', 'quoted "text" and a tab\tinside JSON',
             'Unicode: caf\u00e9, \u65e5\u672c\u8a9e, \u0ba4\u0bae\u0bbf\u0bb4\u0bcd',
             'Escaped embedded newline:\nnot a second record', '\U0001f4c4 export', '']
    all_records = []
    with zipfile.ZipFile(path, 'w') as archive:
        for f, name in enumerate(('windows-lines.jsonl', 'no-final-newline.jsonl', 'blank-lines.jsonl'), 1):
            lines = []
            for i, text in enumerate(texts, 1):
                record = make_record(rng, i, f, 'smoke')
                record['payload'] = {'text': text, 'nested': {'ok': True, 'values': [1, None, 2]}}
                all_records.append(record)
                lines.append(json.dumps(record, ensure_ascii=False, separators=(',', ':')))
            content = ('\r\n'.join(lines)+'\r\n' if f == 1 else '\n'.join(lines) if f == 2 else '\n\n'+'\n\n'.join(lines)+'\n\n')
            archive.writestr(member(name), content.encode('utf-8'))
    manifest = summarize(all_records, [len(texts)]*3, profile='text-edge-cases', seed=91)
    write_manifest(path, manifest)
    return manifest


def invalid_archive(path: Path, kind: str):
    if kind not in INVALID_KINDS:
        raise ValueError('unknown invalid fixture kind')
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(make_record(random.Random(1), 1, 1, 'smoke'))+'\n'
    with zipfile.ZipFile(path, 'w') as archive:
        if kind == 'empty':
            pass
        elif kind == 'unsafe-path':
            archive.writestr(member('../escape.jsonl'), line)
        elif kind == 'windows-drive-path':
            archive.writestr(member('C:\\temp\\escape.jsonl'), line)
        elif kind == 'windows-unc-path':
            archive.writestr(member('\\\\server\\share\\escape.jsonl'), line)
        elif kind == 'nested-path':
            archive.writestr(member('folder/batch.jsonl'), line)
        elif kind == 'directory-entry':
            archive.writestr(member('folder/'), '')
            archive.writestr(member('batch-001.jsonl'), line)
        elif kind == 'duplicate-record':
            archive.writestr(member('batch-001.jsonl'), line+line)
        elif kind == 'malformed-json':
            archive.writestr(member('batch-001.jsonl'), line+'{not valid json}\n')
        elif kind == 'duplicate-file':
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                archive.writestr(member('batch-001.jsonl'), line)
                archive.writestr(member('batch-001.jsonl'), line)
        elif kind == 'invalid-record':
            archive.writestr(member('batch-001.jsonl'), '{"record_id":"x","payload":{},"work_ms":-1,"timeout_ms":100}\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--profile', choices=PROFILES, default='smoke')
    p.add_argument('--files', type=int, default=3)
    p.add_argument('--records-per-file', type=int, default=12)
    p.add_argument('--file-counts', type=int, nargs='+', help='uneven file sizes; overrides --files and --records-per-file')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--invalid', choices=INVALID_KINDS)
    a = p.parse_args()
    try:
        if a.invalid:
            invalid_archive(a.output, a.invalid)
            print(f'Created invalid fixture: {a.output} ({a.invalid})')
        else:
            print(json.dumps(generate(a.output, files=a.files, records_per_file=a.records_per_file,
                profile=a.profile, seed=a.seed, record_counts=a.file_counts), indent=2))
    except (OSError, ValueError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    main()
