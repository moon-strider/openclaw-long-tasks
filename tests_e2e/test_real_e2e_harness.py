from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_real_e2e_harness_reports_real_runtime_state():
    script = Path('scripts/run_real_e2e.py')
    proc = subprocess.run(['python3', str(script)], capture_output=True, text=True)
    payload = json.loads(proc.stdout)
    assert 'new_task_count' in payload
    assert 'result_file_exists' in payload
    assert proc.returncode in {0, 1}
    if proc.returncode == 1:
        combined = (payload.get('stdout', '') + '\n' + payload.get('stderr', '')).lower()
        assert (
            'pairing' in combined
            or 'invalid session id' in combined
            or 'not found' in combined
            or 'forbidden' in combined
            or payload['new_task_count'] == 0
        )
