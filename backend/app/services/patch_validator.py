"""Isolated Temp-Dir Patch Validator with scrubbed subprocess environments."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_JS_TS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
_JS_EXEC_EXTS = frozenset({".js", ".mjs", ".cjs"})

_SAFE_ENV_PASSTHROUGH = (
    "PATH",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
    "LANG",
    "LC_ALL",
)


def build_scrubbed_env(temp_dir: Path) -> dict[str, str]:
    """Construct a secret-free subprocess environment isolated to temp_dir."""
    env: dict[str, str] = {}
    for key in _SAFE_ENV_PASSTHROUGH:
        val = os.environ.get(key)
        if val:
            env[key] = val

    temp_str = str(temp_dir.resolve())
    env["HOME"] = temp_str
    env["USERPROFILE"] = temp_str
    env["TMP"] = temp_str
    env["TEMP"] = temp_str
    env["TMPDIR"] = temp_str
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = temp_str
    return env


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _resolve_tool_executable(name: str) -> str | None:
    venv_dir = Path(sys.executable).resolve().parent
    for candidate_name in (f"{name}.exe", name):
        candidate = venv_dir / candidate_name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _check_balanced_js_ts(content: str) -> tuple[bool, str]:
    """Verify non-empty JS/TS source and balanced delimiters outside strings/comments."""
    if not content.strip():
        return False, "File content is empty."

    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    i = 0
    n = len(content)
    in_line_comment = False
    in_block_comment = False
    quote: str | None = None

    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        if ch in pairs:
            stack.append(ch)
        elif ch in closing:
            if not stack or stack[-1] != closing[ch]:
                return False, f"Unbalanced closing delimiter '{ch}'."
            stack.pop()
        i += 1

    if quote is not None:
        return False, "Unterminated string literal."
    if in_block_comment:
        return False, "Unterminated block comment."
    if stack:
        return False, f"Unclosed delimiter '{stack[-1]}'."
    return True, "Balanced JS/TS delimiters."


def _validate_relative_path(raw_path: str, temp_root: Path) -> tuple[bool, str, Path | None]:
    if not raw_path or "\x00" in raw_path:
        return False, "Empty or null-byte path.", None

    cleaned = raw_path.replace("\\", "/")
    if cleaned.startswith("/") or _DRIVE_PREFIX_RE.match(cleaned):
        return False, f"Absolute path rejected: '{raw_path}'.", None

    parts = [p for p in cleaned.split("/") if p != ""]
    if not parts:
        return False, f"Invalid empty relative path: '{raw_path}'.", None

    for part in parts:
        if part in (".", ".."):
            return False, f"Path traversal component rejected in '{raw_path}'.", None
        lower = part.lower()
        if lower == ".git":
            return False, f".git path access rejected in '{raw_path}'.", None
        if lower == ".env" or lower.startswith(".env."):
            return False, f".env file access rejected in '{raw_path}'.", None

    root_resolved = temp_root.resolve()
    target = temp_root.joinpath(*parts)

    # Check symlink escapes on any existing ancestor or target before writing
    probe = target
    while True:
        if probe.exists() and probe.is_symlink():
            return False, f"Symlink component rejected in '{raw_path}'.", None
        if probe == temp_root or probe.parent == probe:
            break
        probe = probe.parent

    resolved = target.resolve()
    if not resolved.is_relative_to(root_resolved):
        return False, f"Resolved path escapes temporary sandbox: '{raw_path}'.", None

    return True, "/".join(parts), resolved


class PatchValidator:
    """Validates generated code patches and tests inside an isolated TemporaryDirectory."""

    @staticmethod
    def validate(
        *,
        files_diff: list[dict[str, Any]],
        generated_tests: list[dict[str, Any]] | None = None,
        base_files: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        run_linters: bool = True,
    ) -> dict[str, Any]:
        """Run path safety, overwrite safety, AST/syntax, and scrubbed ruff/pytest/eslint."""
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else get_settings().PATCH_VALIDATION_TIMEOUT_SECONDS
        )
        tests_list = generated_tests or []
        checks: list[dict[str, Any]] = []
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        path_safety_ok = True
        overwrite_ok = True
        ast_ok = True
        ruff_ok: bool | None = None
        pytest_ok: bool | None = None
        eslint_ok: bool | None = None

        normalized_base: dict[str, str] | None = (
            {k.replace("\\", "/").lstrip("/"): _normalize_newlines(v) for k, v in base_files.items()}
            if base_files is not None
            else None
        )

        with tempfile.TemporaryDirectory(prefix="rg_patch_") as tmp_dir_str:
            temp_root = Path(tmp_dir_str)
            scrubbed_env = build_scrubbed_env(temp_root)

            # Materialize base snapshot files if provided so tests/imports resolve cleanly
            if normalized_base:
                for rel_path, base_content in normalized_base.items():
                    ok, norm_or_err, dest = _validate_relative_path(rel_path, temp_root)
                    if ok and dest is not None:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text(base_content, encoding="utf-8")

            written_code_paths: list[Path] = []
            written_test_paths: list[Path] = []

            # Check 1 & Check 2: Path safety + overwrite safety on all patches & tests
            for item in files_diff:
                raw_path = str(item.get("file_path", ""))
                action = str(item.get("action", "modify"))
                before_code = str(item.get("before_code", ""))
                after_code = str(item.get("after_code", ""))

                ok, norm_or_err, dest = _validate_relative_path(raw_path, temp_root)
                if not ok or dest is None:
                    path_safety_ok = False
                    checks.append(
                        {"name": f"path_safety:{raw_path}", "status": "failed", "detail": norm_or_err}
                    )
                    continue
                norm_path = norm_or_err

                if action == "create":
                    if normalized_base is not None and norm_path in normalized_base:
                        overwrite_ok = False
                        checks.append(
                            {
                                "name": f"overwrite_safety:{norm_path}",
                                "status": "failed",
                                "detail": "Cannot create file that already exists in base snapshot.",
                            }
                        )
                        continue
                elif action == "modify":
                    if normalized_base is not None:
                        if norm_path not in normalized_base:
                            overwrite_ok = False
                            checks.append(
                                {
                                    "name": f"overwrite_safety:{norm_path}",
                                    "status": "failed",
                                    "detail": "Target file for modify does not exist in base snapshot.",
                                }
                            )
                            continue
                        expected_base = _normalize_newlines(before_code)
                        actual_base = normalized_base[norm_path]
                        if not expected_base or (
                            expected_base != actual_base and expected_base not in actual_base
                        ):
                            overwrite_ok = False
                            checks.append(
                                {
                                    "name": f"overwrite_safety:{norm_path}",
                                    "status": "failed",
                                    "detail": "before_code does not match target file in base snapshot.",
                                }
                            )
                            continue
                else:
                    overwrite_ok = False
                    checks.append(
                        {
                            "name": f"overwrite_safety:{norm_path}",
                            "status": "failed",
                            "detail": f"Unsupported file action '{action}'.",
                        }
                    )
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(after_code, encoding="utf-8")
                written_code_paths.append(dest)

            for test_item in tests_list:
                raw_path = str(test_item.get("file_path", ""))
                content = str(test_item.get("content", ""))
                ok, norm_or_err, dest = _validate_relative_path(raw_path, temp_root)
                if not ok or dest is None:
                    path_safety_ok = False
                    checks.append(
                        {
                            "name": f"path_safety:{raw_path}",
                            "status": "failed",
                            "detail": norm_or_err,
                        }
                    )
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                written_test_paths.append(dest)

            if path_safety_ok:
                checks.append(
                    {
                        "name": "path_safety",
                        "status": "passed",
                        "detail": "All paths validated inside temporary directory.",
                    }
                )
            if overwrite_ok:
                checks.append(
                    {
                        "name": "overwrite_safety",
                        "status": "passed",
                        "detail": "Create/modify actions match base expectations.",
                    }
                )

            all_written = written_code_paths + written_test_paths

            # Check 3: AST & Syntax validation
            py_files: list[Path] = []
            js_ts_files: list[Path] = []
            for file_path in all_written:
                suffix = file_path.suffix.lower()
                text = file_path.read_text(encoding="utf-8")
                rel = file_path.relative_to(temp_root).as_posix()

                if suffix == ".py":
                    py_files.append(file_path)
                    try:
                        ast.parse(text, filename=rel)
                        checks.append(
                            {"name": f"ast_python:{rel}", "status": "passed", "detail": "Valid AST."}
                        )
                    except SyntaxError as exc:
                        ast_ok = False
                        checks.append(
                            {
                                "name": f"ast_python:{rel}",
                                "status": "failed",
                                "detail": f"SyntaxError line {exc.lineno}: {exc.msg}",
                            }
                        )
                elif suffix == ".json":
                    try:
                        json.loads(text)
                        checks.append(
                            {"name": f"json_syntax:{rel}", "status": "passed", "detail": "Valid JSON."}
                        )
                    except json.JSONDecodeError as exc:
                        ast_ok = False
                        checks.append(
                            {
                                "name": f"json_syntax:{rel}",
                                "status": "failed",
                                "detail": f"JSONDecodeError line {exc.lineno}: {exc.msg}",
                            }
                        )
                elif suffix in _JS_TS_EXTS:
                    js_ts_files.append(file_path)
                    balanced, detail = _check_balanced_js_ts(text)
                    if not balanced:
                        ast_ok = False
                        checks.append(
                            {"name": f"js_ts_syntax:{rel}", "status": "failed", "detail": detail}
                        )
                    else:
                        checks.append(
                            {"name": f"js_ts_syntax:{rel}", "status": "passed", "detail": detail}
                        )

            # Check 4: Scrubbed subprocess checks (node --check, eslint, ruff, pytest)
            if run_linters and js_ts_files:
                node_bin = shutil.which("node")
                js_exec_files = [p for p in js_ts_files if p.suffix.lower() in _JS_EXEC_EXTS]
                if js_exec_files:
                    if node_bin:
                        for js_file in js_exec_files:
                            rel = js_file.relative_to(temp_root).as_posix()
                            try:
                                proc = subprocess.run(
                                    [node_bin, "--check", str(js_file)],
                                    check=False,
                                    cwd=str(temp_root),
                                    env=scrubbed_env,
                                    capture_output=True,
                                    text=True,
                                    timeout=timeout,
                                )
                                stdout_parts.append(proc.stdout)
                                stderr_parts.append(proc.stderr)
                                if proc.returncode != 0:
                                    ast_ok = False
                                    checks.append(
                                        {
                                            "name": f"node_check:{rel}",
                                            "status": "failed",
                                            "detail": proc.stderr.strip() or "node --check failed",
                                        }
                                    )
                                else:
                                    checks.append(
                                        {
                                            "name": f"node_check:{rel}",
                                            "status": "passed",
                                            "detail": "node --check passed.",
                                        }
                                    )
                            except subprocess.TimeoutExpired:
                                ast_ok = False
                                stderr_parts.append(f"node --check timed out after {timeout}s.")
                                checks.append(
                                    {
                                        "name": f"node_check:{rel}",
                                        "status": "failed",
                                        "detail": f"Timed out after {timeout}s.",
                                    }
                                )
                    else:
                        checks.append(
                            {
                                "name": "node_check",
                                "status": "skipped",
                                "detail": "Node.js binary not available in container.",
                                "skipped_reason": "node binary not installed",
                            }
                        )

                eslint_bin = shutil.which("eslint")
                if eslint_bin:
                    try:
                        proc = subprocess.run(
                            [eslint_bin, "--no-config-lookup", *[str(p) for p in js_ts_files]],
                            check=False,
                            cwd=str(temp_root),
                            env=scrubbed_env,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                        )
                        stdout_parts.append(proc.stdout)
                        stderr_parts.append(proc.stderr)
                        eslint_ok = proc.returncode == 0
                        checks.append(
                            {
                                "name": "eslint",
                                "status": "passed" if eslint_ok else "failed",
                                "detail": (proc.stdout + proc.stderr).strip() or "eslint finished.",
                            }
                        )
                    except subprocess.TimeoutExpired:
                        eslint_ok = False
                        stderr_parts.append(f"eslint timed out after {timeout}s.")
                        checks.append(
                            {
                                "name": "eslint",
                                "status": "failed",
                                "detail": f"Timed out after {timeout}s.",
                            }
                        )
                else:
                    checks.append(
                        {
                            "name": "eslint",
                            "status": "skipped",
                            "detail": "ESLint not available in backend test environment.",
                            "skipped_reason": "eslint binary not installed",
                        }
                    )

            if run_linters and py_files and path_safety_ok and overwrite_ok and ast_ok:
                ruff_bin = _resolve_tool_executable("ruff")
                if ruff_bin:
                    try:
                        proc = subprocess.run(
                            [ruff_bin, "check", "--no-cache", *[str(p) for p in py_files]],
                            check=False,
                            cwd=str(temp_root),
                            env=scrubbed_env,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                        )
                        stdout_parts.append(proc.stdout)
                        stderr_parts.append(proc.stderr)
                        ruff_ok = proc.returncode == 0
                        checks.append(
                            {
                                "name": "ruff",
                                "status": "passed" if ruff_ok else "failed",
                                "detail": (proc.stdout + proc.stderr).strip() or "ruff check passed.",
                            }
                        )
                    except subprocess.TimeoutExpired:
                        ruff_ok = False
                        stderr_parts.append(f"ruff timed out after {timeout}s.")
                        checks.append(
                            {
                                "name": "ruff",
                                "status": "failed",
                                "detail": f"Timed out after {timeout}s.",
                            }
                        )
                else:
                    checks.append(
                        {
                            "name": "ruff",
                            "status": "skipped",
                            "detail": "ruff binary not found.",
                            "skipped_reason": "ruff binary not installed",
                        }
                    )

                py_tests = [p for p in written_test_paths if p.suffix.lower() == ".py"]
                if py_tests:
                    pytest_bin = _resolve_tool_executable("pytest")
                    cmd = (
                        [pytest_bin, "-q", "-o", "cache_dir=.pytest_cache", *[str(p) for p in py_tests]]
                        if pytest_bin
                        else [sys.executable, "-m", "pytest", "-q", *[str(p) for p in py_tests]]
                    )
                    try:
                        proc = subprocess.run(
                            cmd,
                            check=False,
                            cwd=str(temp_root),
                            env=scrubbed_env,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                        )
                        stdout_parts.append(proc.stdout)
                        stderr_parts.append(proc.stderr)
                        pytest_ok = proc.returncode == 0
                        checks.append(
                            {
                                "name": "pytest",
                                "status": "passed" if pytest_ok else "failed",
                                "detail": (proc.stdout + proc.stderr).strip() or "pytest finished.",
                            }
                        )
                    except subprocess.TimeoutExpired:
                        pytest_ok = False
                        stderr_parts.append(f"pytest timed out after {timeout}s.")
                        checks.append(
                            {
                                "name": "pytest",
                                "status": "failed",
                                "detail": f"Timed out after {timeout}s.",
                            }
                        )
                else:
                    checks.append(
                        {
                            "name": "pytest",
                            "status": "skipped",
                            "detail": "No Python test files generated.",
                            "skipped_reason": "no python test files generated",
                        }
                    )

        overall_passed = (
            path_safety_ok
            and overwrite_ok
            and ast_ok
            and (ruff_ok is not False)
            and (pytest_ok is not False)
            and (eslint_ok is not False)
        )
        return {
            "validation_status": "passed" if overall_passed else "failed",
            "ast_ok": ast_ok,
            "path_safety_ok": path_safety_ok,
            "overwrite_ok": overwrite_ok,
            "ruff_ok": ruff_ok,
            "pytest_ok": pytest_ok,
            "eslint_ok": eslint_ok,
            "checks": checks,
            "stdout": "\n".join(part for part in stdout_parts if part).strip(),
            "stderr": "\n".join(part for part in stderr_parts if part).strip(),
        }


def validate_patch_isolated(
    *,
    files_diff: list[dict[str, Any]],
    generated_tests: list[dict[str, Any]] | None = None,
    base_files: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
    run_linters: bool = True,
) -> dict[str, Any]:
    """Convenience function calling PatchValidator.validate."""
    return PatchValidator.validate(
        files_diff=files_diff,
        generated_tests=generated_tests,
        base_files=base_files,
        timeout_seconds=timeout_seconds,
        run_linters=run_linters,
    )
