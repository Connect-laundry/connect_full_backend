from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)


def git_ls_files(*patterns: str) -> list[str]:
    try:
        output = subprocess.check_output(
            ['git', 'ls-files', *patterns],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return []
    return [line for line in output.splitlines() if line]


def tracked_matches(patterns: list[str]) -> list[str]:
    tracked = git_ls_files('.')
    results: list[str] = []
    for tracked_file in tracked:
        if not (ROOT / tracked_file).exists():
            continue
        if any(fnmatch.fnmatch(tracked_file, pattern) for pattern in patterns):
            results.append(tracked_file)
    return results


def ensure_file(relative_path: str) -> None:
    if not (ROOT / relative_path).exists():
        fail(f'Missing required file: {relative_path}')


def ensure_text_contains(relative_path: str, snippet: str, message: str) -> None:
    content = (ROOT / relative_path).read_text(encoding='utf-8')
    if snippet not in content:
        fail(message)


def is_allowed_env_template(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        name in {'.env.example', '.env.sample', 'env.example', 'env.sample'}
        or name.endswith('.example')
        or name.endswith('.sample')
        or name.endswith('.template')
        or name.endswith('.dist')
    )


def is_secret_env_candidate(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        name == '.env'
        or name.startswith('.env.')
        or name.endswith('.env')
        or '.env.' in name
    )


ensure_file('.env.example')
ensure_file('security_prod.env.template')
ensure_file('.github/workflows/backend-ci.yml')

tracked_env = [
    path
    for path in git_ls_files('.')
    if (ROOT / path).exists()
    and is_secret_env_candidate(path)
    and not is_allowed_env_template(path)
]
if tracked_env:
    fail(f'Tracked env files must be removed from git: {", ".join(tracked_env)}')

forbidden_artifacts = tracked_matches(
    [
        'tmp/*',
        '*.log',
        '*_error*.txt',
        '*results.txt',
        'result*.txt',
        'inspect_out.txt',
        'mock_findings.txt',
        'db_tables.txt',
        'test_db.sqlite3',
    ]
)
if forbidden_artifacts:
    fail(f'Tracked debug artifacts must be removed: {", ".join(forbidden_artifacts[:12])}')

ensure_text_contains(
    'config/settings.py',
    'send_default_pii=False',
    'Sentry PII collection must stay disabled in config/settings.py.',
)
ensure_text_contains(
    'config/settings.py',
    "'rest_framework.renderers.JSONRenderer'",
    'Production DRF renderer must include JSONRenderer in config/settings.py.',
)
ensure_text_contains(
    'config/settings.py',
    'BLACKLIST_AFTER_ROTATION',
    'Refresh-token blacklisting must remain configured in config/settings.py.',
)

admin_monitoring = (ROOT / 'marketplace/views/admin_monitoring.py').read_text(encoding='utf-8')
if 'Simulated' in admin_monitoring:
    fail('Admin monitoring still contains simulated operational data.')

if FAILURES:
    print('[release-readiness] failures:')
    for failure in FAILURES:
        print(f'- {failure}')
    raise SystemExit(1)

print('Backend release readiness validation passed.')
