import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
CHECK_SCRIPT = REPO_ROOT / 'scripts' / 'seedance' / 'check_templates_integrity.py'


def test_templates_integrity_check_script_passes():
    result = subprocess.run(
        ['python', str(CHECK_SCRIPT)],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
