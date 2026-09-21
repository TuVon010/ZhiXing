import json
from pathlib import Path
import subprocess
import sys


def test_evaluation_retains_case_evidence_and_refuses_overwrite(tmp_path):
    root = Path(__file__).resolve().parents[1]
    row = json.loads((root/'eval/datasets/regression.jsonl').read_text(encoding='utf-8').splitlines()[0])
    dataset = tmp_path/'sample.jsonl'
    dataset.write_text(json.dumps(row, ensure_ascii=False)+'\n', encoding='utf-8')
    output = tmp_path/'first'
    command = [sys.executable, str(root/'eval/run.py'), '--dataset', str(dataset), '--output-dir', str(output)]
    first = subprocess.run(command, cwd=root, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    original = (output/'cases.jsonl').read_bytes()
    saved = json.loads(original)
    assert saved['case'] == row
    assert saved['actual_plan']['actions']
    assert saved['actual_decisions']
    assert subprocess.run(command, cwd=root, capture_output=True).returncode != 0
    assert (output/'cases.jsonl').read_bytes() == original
    row['expected_tools'] = ['intentionally_wrong_label']
    dataset.write_text(json.dumps(row)+'\n', encoding='utf-8')
    second = tmp_path/'second'
    command[-1] = str(second)
    assert subprocess.run(command, cwd=root, capture_output=True).returncode == 1
    assert json.loads((second/'report.json').read_text(encoding='utf-8'))['failed_cases'] == 1
    assert (output/'cases.jsonl').read_bytes() == original
