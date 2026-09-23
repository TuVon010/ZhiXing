"""Record reproducible offline checks; never delete previous evidence."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    archive = ROOT / 'artifacts' / 'test-runs' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns())[-6:])
    archive.mkdir(parents=True, exist_ok=False)
    print('Evidence: ' + str(archive), flush=True)
    manifest = {'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
                'verification': 'offline_and_mock_only', 'real_channels_verified': False,
                'python': sys.executable, 'steps': [],
                'storage_paths': {key: os.environ.get(key) for key in ['CONDA_PKGS_DIRS', 'PIP_CACHE_DIR', 'npm_config_cache', 'PLAYWRIGHT_BROWSERS_PATH', 'TEMP', 'TMP']}}
    write_json(archive / 'manifest.json', manifest)
    cache=ROOT.parent/'.cache';temporary=ROOT/'.tmp';temporary.mkdir(parents=True,exist_ok=True)
    for folder in ['conda','pip','npm','pnpm','playwright','huggingface','torch','xdg']:(cache/folder).mkdir(parents=True,exist_ok=True)
    test_port=str(18000+(os.getpid()%10000))
    env = dict(os.environ, ZHIXING_MODE='demo', ZHIXING_DATA_DIR=str(archive / 'backend-data'),
               ZHIXING_TEST_OUTPUT=str(archive / 'browser'),ZHIXING_E2E_BASE_URL='http://127.0.0.1:'+test_port,
               CONDA_PKGS_DIRS=str(cache/'conda'),PIP_CACHE_DIR=str(cache/'pip'),npm_config_cache=str(cache/'npm'),
               PNPM_HOME=str(cache/'pnpm'),PLAYWRIGHT_BROWSERS_PATH=str(cache/'playwright'),
               HF_HOME=str(cache/'huggingface'),TORCH_HOME=str(cache/'torch'),XDG_CACHE_HOME=str(cache/'xdg'),
               TEMP=str(temporary),TMP=str(temporary),PYTHONUTF8='1')
    manifest['storage_paths']={key:env.get(key) for key in ['CONDA_PKGS_DIRS','PIP_CACHE_DIR','npm_config_cache','PLAYWRIGHT_BROWSERS_PATH','TEMP','TMP']}
    write_json(archive/'manifest.json',manifest)

    def run(name, command, cwd=ROOT):
        entry = {'name': name, 'command': command, 'cwd': str(cwd), 'started_at': datetime.now(timezone.utc).isoformat()}
        manifest['steps'].append(entry)
        write_json(archive / 'manifest.json', manifest)
        start = time.perf_counter()
        print('Running: ' + name, flush=True)
        with (archive / (name + '.log')).open('w', encoding='utf-8') as log:
            try:
                result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
                entry['exit_code'] = result.returncode
            except OSError as exc:
                log.write(str(exc))
                entry['exit_code'] = -1
        entry['duration_seconds'] = round(time.perf_counter() - start, 3)
        write_json(archive / 'manifest.json', manifest)
        print(f"{name}: exit {entry['exit_code']}", flush=True)

    run('git-revision', ['git', 'rev-parse', 'HEAD'])
    run('git-status', ['git', 'status', '--short'])
    run('python-version', [sys.executable, '--version'])
    run('dependencies', [sys.executable, '-m', 'pip', 'freeze'])
    run('node-version', ['node', '--version'])
    # Snapshot tracked source only: never read .env or user runtime databases.
    tracked = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode('utf-8').split('\0')
    with zipfile.ZipFile(archive / 'source.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        for relative in tracked:
            source = ROOT / relative
            if relative and source.is_file() and (not source.name.startswith('.env') or source.name == '.env.example'):
                bundle.write(source, relative)
        for relative in ['scripts/run_checks.py', 'scripts/test.ps1', 'docs/TESTING.md', 'tests/test_evaluation_archive.py']:
            if relative not in tracked and (ROOT / relative).is_file():
                bundle.write(ROOT / relative, relative)
    # Recover existing evidence without pretending it has historical stdout or commands.
    recovered = ROOT / 'artifacts' / 'historical'
    recovered.mkdir(parents=True, exist_ok=True)
    for old in sorted((ROOT / '.tmp').glob('e2e-*')):
        target = recovered / old.name
        if target.exists():
            continue
        target.mkdir()
        for database in old.glob('*.db'):
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target / database.name) as dst:
                src.backup(dst)
        write_json(target / 'provenance.json', {'source': str(old), 'recovered_at': datetime.now(timezone.utc).isoformat(),
                    'limitations': 'Recovered database only; original commands, logs and pass/fail status not available.'})
    for relative in ['eval/reports/offline.json', 'docs/VALIDATION.md', 'docs/console-preview.png', 'docs/trace-preview.png']:
        source = ROOT / relative
        target = recovered / source.name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
    try:
        run('backend', [sys.executable, '-m', 'pytest', '-v', '--capture=tee-sys', '--junitxml=' + str(archive / 'backend-junit.xml'), '--basetemp=' + str(archive / 'pytest-data'), 'tests'])
        run('evaluation', [sys.executable, 'eval/run.py', '--output-dir', str(archive / 'evaluation')])
        node = shutil.which('node') or 'node'
        run('typescript', [node, 'node_modules/typescript/bin/tsc', '-b'], ROOT / 'frontend')
        run('frontend-build', [node, 'node_modules/vite/bin/vite.js', 'build'], ROOT / 'frontend')
        run('browser', [node, 'node_modules/@playwright/test/cli.js', 'test'], ROOT / 'frontend')
        manifest['status'] = 'passed' if all(step['exit_code'] == 0 for step in manifest['steps']) else 'failed'
    finally:
        manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
        if manifest['status'] == 'running':
            manifest['status'] = 'interrupted'
        write_json(archive / 'manifest.json', manifest)
        hashes = {str(p.relative_to(archive)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in archive.rglob('*') if p.is_file()}
        write_json(archive / 'sha256.json', hashes)
    print('Result: ' + manifest['status'] + '\nEvidence: ' + str(archive), flush=True)
    return 0 if manifest['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
