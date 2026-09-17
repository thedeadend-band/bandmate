import shutil
import subprocess


def require_rubberband() -> str:
    """Return the Rubber Band executable or fail with an actionable error."""
    executable = shutil.which('rubberband')
    if not executable:
        raise RuntimeError(
            'rubberband-cli is required but the rubberband executable is not on PATH')

    result = subprocess.run(
        [executable, '--version'],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(
            f'rubberband executable failed its startup check: {detail[-300:]}')
    return executable
