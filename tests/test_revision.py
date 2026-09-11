"""Cross-platform config, expanded corpus and preset-runner regression tests."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import pytest

from processor.env import load_env
from processor.settings import Settings
from tools import suite
from tools.bench import parser as bench_parser
from tools.generate import generate, PROFILES, text_edge_archive
from tools.make_samples import build_samples
from tools.verify import read_expected

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT/'samples/catalog.json').read_text(encoding='utf-8'))


@pytest.fixture(autouse=True)
def isolated_process_environment(monkeypatch):
    import os
    monkeypatch.setattr(os, 'environ', dict(os.environ))


def clear_processor_env(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith('PROCESSOR_'):
            monkeypatch.delenv(key)


def test_env_accepts_windows_text_and_preserves_exported_values(tmp_path, monkeypatch):
    clear_processor_env(monkeypatch)
    path = tmp_path/'local settings.env'
    path.write_bytes(('\ufeff# comment\r\nPROCESSOR_CAPACITY=7\r\n'
                      'PROCESSOR_DB="C:\\Users\\Student Name\\audit.sqlite3"\r\n'
                      "PROCESSOR_ADMIN_TOKEN='token=with=equals'\r\n").encode('utf-8'))
    monkeypatch.setenv('PROCESSOR_CAPACITY', '9')
    load_env(path)
    import os
    assert os.environ['PROCESSOR_CAPACITY'] == '9'
    assert os.environ['PROCESSOR_DB'] == r'C:\Users\Student Name\audit.sqlite3'
    assert os.environ['PROCESSOR_ADMIN_TOKEN'] == 'token=with=equals'


@pytest.mark.parametrize('bad', ['NO_EQUALS', 'BAD-KEY=5', 'KEY="unclosed', 'KEY=a\x00b'])
def test_env_parse_errors_do_not_partially_apply(tmp_path, monkeypatch, bad):
    monkeypatch.delenv('PROCESSOR_TEST_SENTINEL', raising=False)
    path = tmp_path/'.env'
    path.write_text('PROCESSOR_TEST_SENTINEL=hello\n'+bad, encoding='utf-8')
    with pytest.raises(ValueError):
        load_env(path)
    import os
    assert 'PROCESSOR_TEST_SENTINEL' not in os.environ


def test_missing_env_is_optional(tmp_path):
    load_env(tmp_path/'does-not-exist.env')


def test_native_config_and_bench_auto_load_env(tmp_path, monkeypatch):
    clear_processor_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path/'.env').write_bytes(b'PROCESSOR_CAPACITY=6\r\nPROCESSOR_ADMIN_TOKEN=custom-token\r\n')
    assert Settings.from_env().capacity == 6
    args = bench_parser().parse_args(['--candidate-url', 'http://candidate', '--input', 'test.zip'])
    assert args.admin_token == 'custom-token'
    assert args.deadline == 600


@pytest.mark.parametrize('profile', PROFILES)
def test_new_generator_profiles_obey_contract(tmp_path, profile):
    path = tmp_path/(profile+'.zip')
    manifest = generate(path, profile=profile, record_counts=[1, 3, 11], seed=812)
    parsed = read_expected(path)
    assert list(map(len, parsed.values())) == [1, 3, 11]
    assert manifest['files'] == 3 and manifest['records'] == 15
    assert sum(r.work_ms for records in parsed.values() for r in records.values()) == manifest['base_work_ms']
    with zipfile.ZipFile(path) as archive:
        assert all(item.create_system == 3 for item in archive.infolist())


@pytest.mark.parametrize('counts', [[], [0], [-1], [1]*1001, [100001], [True]])
def test_generator_rejects_invalid_counts(tmp_path, counts):
    with pytest.raises(ValueError):
        generate(tmp_path/'bad.zip', record_counts=counts)


@pytest.mark.parametrize('sample', CATALOG['valid'], ids=lambda item: item['name'])
def test_every_packaged_valid_archive_and_manifest(sample):
    path = ROOT/'samples'/sample['archive']
    expected = read_expected(path)
    records = [r for values in expected.values() for r in values.values()]
    manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    assert len(expected) == sample['files'] == manifest['files']
    assert len(records) == sample['records'] == manifest['records']
    assert sum(r.work_ms for r in records) == sample['base_work_ms'] == manifest['base_work_ms']
    assert list(map(len, expected.values())) == sample['records_per_file']


@pytest.mark.parametrize('sample', CATALOG['invalid'], ids=lambda item: item['reason'])
def test_every_packaged_invalid_archive_is_rejected(sample):
    with pytest.raises(ValueError):
        read_expected(ROOT/'samples'/sample['archive'])


def test_sustained_is_not_a_tiny_smoke_case():
    sample = next(s for s in CATALOG['valid'] if s['name'] == 'sustained')
    assert sample['records'] == 600
    assert sample['base_work_ms'] >= 1_000_000
    assert sample['work_ms']['max'] >= 20000
    assert sample['nominal_work_divided_by_20_seconds'] >= 50


def test_real_text_edge_cases(tmp_path):
    path = tmp_path/'text.zip'
    text_edge_archive(path)
    expected = read_expected(path)
    assert sum(map(len, expected.values())) == 18
    with zipfile.ZipFile(path) as archive:
        assert b'\r\n' in archive.read('windows-lines.jsonl')
        assert not archive.read('no-final-newline.jsonl').endswith(b'\n')
        assert b'\n\n' in archive.read('blank-lines.jsonl')
        assert 'caf\u00e9'.encode('utf-8') in archive.read('windows-lines.jsonl')


def test_all_samples_regenerate_same_content_and_metadata(tmp_path):
    output = tmp_path/'with spaces'/'samples'
    rebuilt = build_samples(output)
    assert len(rebuilt['valid']) == 17 and len(rebuilt['invalid']) == 10
    for sample in rebuilt['valid'] + rebuilt['invalid']:
        # Compression can differ across zlib versions; logical content must not.
        with zipfile.ZipFile(output/sample['archive']) as fresh, zipfile.ZipFile(ROOT/'samples'/sample['archive']) as packaged:
            assert fresh.namelist() == packaged.namelist()
            for a, b in zip(fresh.infolist(), packaged.infolist()):
                assert (a.date_time, a.create_system, a.external_attr) == (b.date_time, b.create_system, b.external_attr)
                assert fresh.read(a) == packaged.read(b)


def test_every_preset_resolves_without_network():
    presets = suite.load_presets()
    assert len(presets) == 14
    for preset in presets.values():
        assert suite.resolve_inputs(preset['inputs'])
        suite.resolve_inputs(preset['invalid_inputs'])
        assert preset['deadline_seconds'] >= 120


def args_for_suite(tmp_path, repeats=2):
    return SimpleNamespace(output_dir=tmp_path, preset='smoke', repeats=repeats,
        candidate_url=['http://candidate'], processor_url='http://processor',
        admin_token='test-secret', deadline=None, http_timeout=60)


def test_suite_repeats_and_summary(tmp_path, monkeypatch):
    captured = []
    async def fake_evaluate(args):
        captured.append(args)
        return {'correct': True, 'valid_makespan_seconds': len(captured)*2.0}
    monkeypatch.setattr(suite, 'evaluate', fake_evaluate)
    assert suite.run(args_for_suite(tmp_path), suite.load_presets()['smoke']) == 0
    summary = json.loads(next(tmp_path.glob('*/summary.json')).read_text())
    assert summary['all_runs_correct'] and summary['median_valid_makespan_seconds'] == 3
    assert len(captured) == 2 and all(x.deadline == 120 for x in captured)
    assert all(x.run_id is None for x in captured)  # Harness generates fresh IDs.


@pytest.mark.parametrize('transport', [False, True])
def test_suite_stops_after_failure(tmp_path, monkeypatch, transport):
    calls = []
    async def fake_evaluate(args):
        calls.append(args)
        if transport:
            raise httpx.ConnectError('unavailable')
        return {'correct': False, 'valid_makespan_seconds': None}
    monkeypatch.setattr(suite, 'evaluate', fake_evaluate)
    code = suite.run(args_for_suite(tmp_path, 3), suite.load_presets()['smoke'])
    assert code == (1 if transport else 2)
    assert len(calls) == 1
    summary = json.loads(next(tmp_path.glob('*/summary.json')).read_text())
    assert not summary['all_runs_correct']
    assert summary['median_valid_makespan_seconds'] is None


def test_bootstrap_handles_spaces_and_preserves_config(tmp_path, monkeypatch):
    import bootstrap
    import sys
    root = tmp_path/'project with spaces'
    root.mkdir()
    (root/'.env.example').write_text('PROCESSOR_CAPACITY=20\n')
    (root/'.env').write_text('PROCESSOR_CAPACITY=7\n')
    executable = root/'.venv'/('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(bootstrap, '__file__', str(root/'bootstrap.py'))
    captured = []
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda command, **kwargs: captured.append((command, kwargs)))
    assert bootstrap.main() == 0
    assert captured[0][0][0] == str(executable)
    assert captured[0][1]['cwd'] == root
    assert (root/'.env').read_text() == 'PROCESSOR_CAPACITY=7\n'
