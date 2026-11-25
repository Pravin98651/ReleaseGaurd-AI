"""Async GitHub REST and Git Data API client (never auto-merges PRs)."""

from __future__ import annotations

import asyncio
import base64
import re
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.security import (
    MAX_DIFF_CHARS,
    MAX_FILE_CHARS,
    make_token_hint,
    wrap_untrusted,
)

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503})
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


class GitHubAPIError(Exception):
    """Structured error raised by GitHubClient."""

    def __init__(
        self,
        status_code: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details or {}


class GitHubClient:
    """Async httpx client for GitHub OAuth, PAT verification, PR reads, and Draft PR creation.

    Hard guardrail: exposes ZERO PR-merge functionality.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = "https://api.github.com",
        oauth_base_url: str = "https://github.com",
        timeout: float = 20.0,
        max_retries: int = 2,
        retry_delay_base: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.oauth_base_url = oauth_base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay_base = retry_delay_base
        self._transport = transport

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ReleaseGuard-AI/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _build_client(self, base_url: str | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url or self.base_url,
            headers=self._default_headers(),
            timeout=self.timeout,
            transport=self._transport,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retry_safe: bool | None = None,
    ) -> httpx.Response:
        allow_retry = (
            retry_safe if retry_safe is not None else method.upper() in _IDEMPOTENT_METHODS
        )
        attempts = (self.max_retries + 1) if allow_retry else 1

        async with self._build_client() as client:
            for attempt in range(attempts):
                resp = await client.request(
                    method=method.upper(),
                    url=path,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts - 1:
                    if self.retry_delay_base > 0:
                        await asyncio.sleep(self.retry_delay_base * (2**attempt))
                    continue
                if resp.status_code >= 400:
                    detail: dict[str, Any]
                    try:
                        detail = resp.json() if resp.content else {}
                    except ValueError:
                        detail = {"raw": resp.text}
                    msg = detail.get("message") or f"GitHub API HTTP {resp.status_code}"
                    raise GitHubAPIError(resp.status_code, str(msg), detail)
                return resp

        raise GitHubAPIError(500, "GitHub request exhausted retries.")

    @staticmethod
    def sandbox_text(
        source: str,
        metadata: dict[str, Any] | None,
        text: str,
        max_chars: int = MAX_DIFF_CHARS,
    ) -> str:
        """Wrap untrusted GitHub text inside <untrusted_external_data> XML delimiters."""
        return wrap_untrusted(source=source, metadata=metadata, text=text, max_chars=max_chars)

    @staticmethod
    def generate_branch_name(slug: str) -> str:
        """Build a safe releaseguard/<slug>-<short_uuid> branch name."""
        normalized = _SLUG_RE.sub("-", slug.lower()).strip("-") or "patch"
        short_id = uuid.uuid4().hex[:8]
        return f"releaseguard/{normalized[:48]}-{short_id}"

    # ------------------------------------------------------------------
    # OAuth & PAT Authentication
    # ------------------------------------------------------------------

    def build_oauth_authorize_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        include_private: bool = False,
    ) -> str:
        """Build GitHub OAuth authorize URL with least-privilege scopes."""
        scopes = "read:user user:email repo" if include_private else "read:user user:email public_repo"
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scopes,
                "state": state,
            }
        )
        return f"{self.oauth_base_url}/login/oauth/authorize?{query}"

    async def exchange_oauth_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Exchange OAuth authorization code for an access token."""
        async with httpx.AsyncClient(
            base_url=self.oauth_base_url,
            headers={"Accept": "application/json", "User-Agent": "ReleaseGuard-AI/1.0"},
            timeout=self.timeout,
            transport=self._transport,
        ) as client:
            resp = await client.post(
                "/login/oauth/access_token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            if resp.status_code >= 400:
                raise GitHubAPIError(resp.status_code, "GitHub OAuth token exchange failed.")
            data = resp.json()
            if data.get("error"):
                raise GitHubAPIError(
                    400,
                    data.get("error_description") or str(data["error"]),
                    data,
                )
            return data

    async def verify_token(self) -> dict[str, Any]:
        """Verify active token via GET /user and GET /user/repos?per_page=1."""
        user_resp = await self._request("GET", "/user")
        user_data = user_resp.json()
        raw_scopes = user_resp.headers.get("X-OAuth-Scopes", "")
        scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()]
        await self._request("GET", "/user/repos", params={"per_page": 1})
        return {
            "user": user_data,
            "scopes": scopes,
            "token_hint": make_token_hint(self.token or ""),
        }

    # ------------------------------------------------------------------
    # Repository, Branch, Commit & PR Reads
    # ------------------------------------------------------------------

    async def list_repositories(
        self,
        *,
        visibility: str = "all",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """List repositories accessible to the authenticated GitHub token."""
        resp = await self._request(
            "GET",
            "/user/repos",
            params={"visibility": visibility, "per_page": min(max(limit, 1), 100), "sort": "updated"},
        )
        return list(resp.json())

    async def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        """Fetch repository metadata."""
        resp = await self._request("GET", f"/repos/{owner}/{repo}")
        return dict(resp.json())

    async def list_branches(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List branches for a repository."""
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/branches", params={"per_page": 100}
        )
        return list(resp.json())

    async def branch_exists(self, owner: str, repo: str, branch: str) -> bool:
        """Check whether a branch currently exists in GitHub."""
        try:
            await self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
            return True
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                return False
            raise

    async def list_commits(
        self,
        owner: str,
        repo: str,
        *,
        sha: str | None = None,
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """List commits for a repository or branch SHA."""
        params: dict[str, Any] = {"per_page": min(max(per_page, 1), 100)}
        if sha:
            params["sha"] = sha
        resp = await self._request("GET", f"/repos/{owner}/{repo}/commits", params=params)
        return list(resp.json())

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """List pull requests for a repository."""
        resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": min(max(per_page, 1), 100)},
        )
        return list(resp.json())

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        """Fetch pull request metadata."""
        resp = await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return dict(resp.json())

    async def get_pull_request_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        max_files: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch changed files and patches for a pull request."""
        resp = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/files",
            params={"per_page": min(max(max_files, 1), 100)},
        )
        return list(resp.json())[:max_files]

    async def get_pull_request_diff(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        max_files: int = 50,
    ) -> dict[str, Any]:
        """Fetch raw PR metadata/files/diff plus <untrusted_external_data> sandboxed blocks."""
        pr_data = await self.get_pull_request(owner, repo, pr_number)
        files_data = await self.get_pull_request_files(owner, repo, pr_number, max_files=max_files)

        diff_sections: list[str] = []
        for item in files_data:
            filename = item.get("filename", "")
            status = item.get("status", "modified")
            patch = item.get("patch") or ""
            diff_sections.append(f"--- a/{filename} ({status})\n+++ b/{filename}\n{patch}")
        raw_diff = "\n\n".join(diff_sections)

        meta = {"repo": f"{owner}/{repo}", "pr_number": pr_number}
        return {
            "pr": pr_data,
            "files": files_data,
            "raw_diff": raw_diff,
            "changed_files": [str(f.get("filename", "")) for f in files_data],
            "sandboxed": {
                "title": self.sandbox_text(
                    "github_pr_title", meta, str(pr_data.get("title") or ""), max_chars=2000
                ),
                "body": self.sandbox_text(
                    "github_pr_body", meta, str(pr_data.get("body") or ""), max_chars=8000
                ),
                "diff": self.sandbox_text(
                    "github_pr_diff", meta, raw_diff, max_chars=MAX_DIFF_CHARS
                ),
            },
        }

    async def get_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        ref: str | None = None,
        max_chars: int = MAX_FILE_CHARS,
    ) -> dict[str, Any]:
        """Fetch file contents from GitHub, decoding base64 and returning raw + sandboxed text."""
        clean_path = path.lstrip("/")
        params = {"ref": ref} if ref else None
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{clean_path}", params=params
        )
        data = dict(resp.json())
        raw_content = data.get("content", "")
        encoding = data.get("encoding", "")
        if encoding == "base64" and isinstance(raw_content, str):
            decoded = base64.b64decode(raw_content.encode("ascii")).decode(
                "utf-8", errors="replace"
            )
        else:
            decoded = str(raw_content)

        return {
            "path": clean_path,
            "sha": str(data.get("sha") or ""),
            "size": int(data.get("size") or len(decoded)),
            "content": decoded,
            "sandboxed_content": self.sandbox_text(
                "github_file",
                {"repo": f"{owner}/{repo}", "path": clean_path, "ref": ref},
                decoded,
                max_chars=max_chars,
            ),
        }

    async def get_repo_tree(
        self,
        owner: str,
        repo: str,
        *,
        tree_sha: str = "HEAD",
        recursive: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch repository git tree entries for code flag scanning."""
        params = {"recursive": "1"} if recursive else None
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}", params=params
        )
        data = dict(resp.json())
        return list(data.get("tree") or [])

    # ------------------------------------------------------------------
    # 8-Step Git Data API Branch + Commit + Draft PR (NEVER merges PRs)
    # ------------------------------------------------------------------

    async def create_branch_and_draft_pr(
        self,
        *,
        owner: str,
        repo: str,
        base_branch: str,
        files: list[dict[str, str]],
        commit_message: str,
        pr_title: str,
        pr_body: str,
        branch_name: str | None = None,
    ) -> dict[str, Any]:
        """Execute the 8-step Git Data API flow to open a Draft PR (draft=True).

        Steps:
        1. GET base branch ref -> base_sha
        2. GET base commit -> base_tree_sha
        3. POST git/refs -> create branch pointing to base_sha
        4. POST git/blobs -> create UTF-8 blob per modified/created file
        5. POST git/trees -> build new tree from base_tree_sha
        6. POST git/commits -> commit new tree with parent [base_sha]
        7. PATCH git/refs/heads/{branch} -> advance branch (force=False)
        8. POST pulls -> open Draft PR (draft=True)
        """
        if not files:
            raise ValueError("At least one file change is required to open a Draft PR.")

        target_branch = branch_name or self.generate_branch_name(pr_title)
        if target_branch in {"main", "master"} or target_branch == base_branch:
            raise ValueError(
                f"Refusing to commit directly to protected/base branch '{target_branch}'."
            )

        # Step 1: Resolve base branch HEAD SHA
        ref_resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/git/ref/heads/{base_branch}"
        )
        base_sha = str(ref_resp.json()["object"]["sha"])

        # Step 2: Resolve base commit tree SHA
        commit_resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/git/commits/{base_sha}"
        )
        base_tree_sha = str(commit_resp.json()["tree"]["sha"])

        # Step 3: Create target feature branch
        await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json_body={"ref": f"refs/heads/{target_branch}", "sha": base_sha},
            retry_safe=False,
        )

        # Step 4: Create blobs for each file
        tree_entries: list[dict[str, str]] = []
        for file_item in files:
            file_path = file_item["path"].lstrip("/")
            content = file_item["content"]
            blob_resp = await self._request(
                "POST",
                f"/repos/{owner}/{repo}/git/blobs",
                json_body={"content": content, "encoding": "utf-8"},
                retry_safe=False,
            )
            blob_sha = str(blob_resp.json()["sha"])
            tree_entries.append(
                {
                    "path": file_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            )

        # Step 5: Create new Git tree
        tree_resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json_body={"base_tree": base_tree_sha, "tree": tree_entries},
            retry_safe=False,
        )
        new_tree_sha = str(tree_resp.json()["sha"])

        # Step 6: Create Git commit
        new_commit_resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json_body={
                "message": commit_message,
                "tree": new_tree_sha,
                "parents": [base_sha],
            },
            retry_safe=False,
        )
        new_commit_sha = str(new_commit_resp.json()["sha"])

        # Step 7: Advance branch ref (force=False)
        await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{target_branch}",
            json_body={"sha": new_commit_sha, "force": False},
            retry_safe=False,
        )

        # Step 8: Open Draft PR (draft=True)
        pr_resp = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json_body={
                "title": pr_title,
                "body": pr_body,
                "head": target_branch,
                "base": base_branch,
                "draft": True,
            },
            retry_safe=False,
        )
        pr_data = dict(pr_resp.json())
        return {
            "pr_number": int(pr_data["number"]),
            "html_url": str(pr_data["html_url"]),
            "branch_name": target_branch,
            "commit_sha": new_commit_sha,
            "draft": bool(pr_data.get("draft", True)),
        }
