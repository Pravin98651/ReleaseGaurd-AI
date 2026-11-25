"""Tests for isolated temp-dir PatchValidator (path, overwrite, AST, ruff/pytest, env scrub)."""

from __future__ import annotations

import pytest

from app.services.patch_validator import PatchValidator, validate_patch_isolated


def test_patch_validator_valid_patch_and_secret_scrubbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-gemini-value")
    monkeypatch.setenv("ENCRYPTION_KEY", "super-secret-fernet-value")
    monkeypatch.setenv("FLAGSMITH_ORG_TOKEN", "super-secret-flagsmith-token")
    monkeypatch.setenv("GITHUB_PAT", "ghp_super_secret_pat_token")
    monkeypatch.setenv("EVENT_INGEST_KEY", "super-secret-ingest")

    base_files = {
        "checkout.py": (
            "def compute_checkout(flag_enabled: bool) -> str:\n"
            '    return "v1"\n'
        )
    }
    files_diff = [
        {
            "file_path": "checkout.py",
            "action": "modify",
            "before_code": base_files["checkout.py"],
            "after_code": (
                "def compute_checkout(flag_enabled: bool) -> str:\n"
                '    return "v2" if flag_enabled else "v1"\n'
            ),
        }
    ]
    generated_tests = [
        {
            "file_path": "test_checkout.py",
            "content": (
                "import os\n\n"
                "from checkout import compute_checkout\n\n\n"
                "def test_flag_branches_and_no_leaked_secrets() -> None:\n"
                '    assert compute_checkout(True) == "v2"\n'
                '    assert compute_checkout(False) == "v1"\n'
                "    for key in (\n"
                '        "GEMINI_API_KEY",\n'
                '        "ENCRYPTION_KEY",\n'
                '        "FLAGSMITH_ORG_TOKEN",\n'
                '        "GITHUB_PAT",\n'
                '        "EVENT_INGEST_KEY",\n'
                "    ):\n"
                "        assert key not in os.environ\n"
            ),
        }
    ]

    report = validate_patch_isolated(
        files_diff=files_diff,
        generated_tests=generated_tests,
        base_files=base_files,
    )
    assert report["validation_status"] == "passed", report
    assert report["path_safety_ok"] is True
    assert report["overwrite_ok"] is True
    assert report["ast_ok"] is True
    assert report["ruff_ok"] is True
    assert report["pytest_ok"] is True


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.py",
        "subdir/../../escape.py",
        "/etc/passwd",
        "C:\\Windows\\system32\\evil.py",
        ".git/config",
        ".env",
        ".env.production",
    ],
)
def test_patch_validator_blocks_unsafe_paths(bad_path: str) -> None:
    report = PatchValidator.validate(
        files_diff=[
            {
                "file_path": bad_path,
                "action": "create",
                "before_code": "",
                "after_code": "x = 1\n",
            }
        ],
        run_linters=False,
    )
    assert report["path_safety_ok"] is False
    assert report["validation_status"] == "failed"


def test_patch_validator_overwrite_checks() -> None:
    base_files = {"app.py": "def run():\n    return 1\n"}

    # Cannot create over existing file
    rep_create = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "app.py",
                "action": "create",
                "before_code": "",
                "after_code": "def run():\n    return 2\n",
            }
        ],
        base_files=base_files,
        run_linters=False,
    )
    assert rep_create["overwrite_ok"] is False
    assert rep_create["validation_status"] == "failed"

    # Cannot modify with stale before_code
    rep_stale = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "app.py",
                "action": "modify",
                "before_code": "def run():\n    return 999\n",
                "after_code": "def run():\n    return 2\n",
            }
        ],
        base_files=base_files,
        run_linters=False,
    )
    assert rep_stale["overwrite_ok"] is False
    assert rep_stale["validation_status"] == "failed"


def test_patch_validator_ast_syntax_json_and_js_ts_checks() -> None:
    # Broken Python AST
    rep_py = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "broken.py",
                "action": "create",
                "before_code": "",
                "after_code": "def broken(:\n    pass\n",
            }
        ],
        run_linters=False,
    )
    assert rep_py["ast_ok"] is False
    assert rep_py["validation_status"] == "failed"

    # Broken JSON
    rep_json = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "flags.json",
                "action": "create",
                "before_code": "",
                "after_code": '{"checkout_v2": true',
            }
        ],
        run_linters=False,
    )
    assert rep_json["ast_ok"] is False
    assert rep_json["validation_status"] == "failed"

    # Unbalanced TS
    rep_ts_bad = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "checkout.ts",
                "action": "create",
                "before_code": "",
                "after_code": "export function go() { return 1;",
            }
        ],
        run_linters=False,
    )
    assert rep_ts_bad["ast_ok"] is False

    # Valid TS passes balanced check & records eslint check (passed or skipped)
    rep_ts_ok = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "checkout.ts",
                "action": "create",
                "before_code": "",
                "after_code": "export function go(): number { return 1; }\n",
            }
        ],
        run_linters=True,
    )
    assert rep_ts_ok["ast_ok"] is True
    assert any(c["name"] == "eslint" for c in rep_ts_ok["checks"])


def test_patch_validator_catches_failing_test_and_timeout() -> None:
    rep_fail = PatchValidator.validate(
        files_diff=[
            {
                "file_path": "mod.py",
                "action": "create",
                "before_code": "",
                "after_code": "def add(a: int, b: int) -> int:\n    return a - b\n",
            }
        ],
        generated_tests=[
            {
                "file_path": "test_mod.py",
                "content": (
                    "from mod import add\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(2, 2) == 4\n"
                ),
            }
        ],
    )
    assert rep_fail["ruff_ok"] is True
    assert rep_fail["pytest_ok"] is False
    assert rep_fail["validation_status"] == "failed"

    rep_timeout = PatchValidator.validate(
        files_diff=[],
        generated_tests=[
            {
                "file_path": "test_slow.py",
                "content": (
                    "import time\n\n\n"
                    "def test_sleep() -> None:\n"
                    "    time.sleep(5)\n"
                ),
            }
        ],
        timeout_seconds=1,
    )
    assert rep_timeout["pytest_ok"] is False
    assert rep_timeout["validation_status"] == "failed"
    assert "timed out" in rep_timeout["stderr"].lower()
