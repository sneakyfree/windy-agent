"""GitHub read capabilities — public + PAT-authed private repo access.

Two tools the LLM can call:

  - ``github.fetch_file(owner, repo, path, ref="main")`` — fetch the
    UTF-8 contents of a file via the GitHub Contents API. Path defaults
    to ``"README.md"`` so "look at the README of foo/bar" needs only
    owner+repo.
  - ``github.list_repo(owner, repo, path="", ref="main")`` — list
    entries at a path (root if path is empty), returning name + type +
    size for each.

Why this exists
---------------

Pre-Wave-15-#1 the agent had to ``shell.exec`` ``git clone`` then
``fs.read_file`` to inspect a GitHub repo. That works for repos the
local user already has SSH access to, but:

  * Wastes seconds + disk + sandbox quota on a full clone.
  * Fails on repos the local user doesn't have keys for, even if the
    agent has a PAT.
  * Makes "what does README say?" ten tool calls instead of one.

Direct API calls cover the dominant read use-case in one round trip.
The clone path stays for "I need to grep this whole repo" or "I want
to actually run code from it" — different use cases, different tools.

Auth
----

Reads ``GITHUB_PAT`` (preferred) or ``GITHUB_TOKEN`` from the
environment. With a token: private repos work, public repos hit a
much higher rate limit (5000/hour vs 60/hour). Without a token:
public repos still work; tools self-document the rate limit risk in
the description so the LLM knows.

Per-instance config (``[capabilities.github]`` in ``windyfly.toml``):

  - ``allowed_owners``: optional allowlist. If set, only repos owned
    by these accounts/orgs are fetchable. Use this on shared
    instances. Default: no restriction.
  - ``base_url``: override for GitHub Enterprise. Default:
    ``https://api.github.com``.

Errors are returned as ``{"error": "..."}`` JSON for the LLM to
self-correct on, never raised — the capability registry's audit hook
records the failure either way, but the LLM sees a structured
message it can react to.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.github.com"
_DEFAULT_TIMEOUT_S = 10.0
# Hard cap so a 50 MB binary file dropped via path doesn't blow context.
_MAX_FILE_BYTES = 256 * 1024  # 256 KB; LLM context ceiling, not GitHub's
# GitHub's Contents API caps at 1 MB; for >1 MB the response is empty
# and ``download_url`` is set. We do NOT chase ``download_url`` in v1
# (that would silently pull arbitrarily large files); LLM gets a
# size-too-large error instead.


def _build_client(base_url: str, token: str | None) -> httpx.Client:
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "windyfly-agent/0.5",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # follow_redirects=True is essential — GitHub returns 301 when the
    # repo URL has been canonicalized (e.g., default-branch redirects,
    # owner case differences). httpx does NOT follow by default.
    return httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=_DEFAULT_TIMEOUT_S,
        follow_redirects=True,
    )


def _check_owner_allowed(
    owner: str, allowed_owners: list[str] | None,
) -> str | None:
    """Return error message if owner is blocked, else None."""
    if not allowed_owners:
        return None
    if owner.lower() not in {o.lower() for o in allowed_owners}:
        return (
            f"owner {owner!r} not in allowed_owners "
            f"(configured: {allowed_owners})"
        )
    return None


def _fetch_file_handler(
    *,
    owner: str,
    repo: str,
    path: str = "README.md",
    ref: str = "main",
    base_url: str,
    token: str | None,
    allowed_owners: list[str] | None,
) -> dict[str, Any]:
    """Implementation of github.fetch_file. Returns dict for the LLM."""
    err = _check_owner_allowed(owner, allowed_owners)
    if err is not None:
        return {"error": err}
    if not owner or not repo:
        return {"error": "owner and repo are required"}
    # Normalize: strip leading/trailing slashes from path; the API is
    # finicky about leading slashes.
    path_norm = path.strip("/")
    api_path = f"/repos/{owner}/{repo}/contents/{path_norm}"
    params = {"ref": ref} if ref else None
    try:
        with _build_client(base_url, token) as client:
            resp = client.get(api_path, params=params)
    except httpx.HTTPError as e:
        return {"error": f"network error fetching {owner}/{repo}/{path_norm}: {e}"}
    if resp.status_code == 404:
        return {
            "error": (
                f"not found: {owner}/{repo}/{path_norm}@{ref}. Check the "
                "path, branch, or — for private repos — that GITHUB_PAT "
                "is set with appropriate scope."
            ),
        }
    if resp.status_code in (401, 403):
        return {
            "error": (
                f"unauthorized ({resp.status_code}) for {owner}/{repo}. "
                "Set GITHUB_PAT for private repos or hit rate limit."
            ),
        }
    if resp.status_code >= 400:
        return {"error": f"github api {resp.status_code}: {resp.text[:200]}"}
    data = resp.json()
    # The Contents API returns a list when path is a directory.
    if isinstance(data, list):
        return {
            "error": (
                f"{path_norm!r} is a directory, not a file. Use "
                "github.list_repo to list its entries."
            ),
        }
    if data.get("type") != "file":
        return {"error": f"unsupported entry type: {data.get('type')!r}"}
    encoding = data.get("encoding")
    if encoding != "base64":
        return {"error": f"unexpected encoding: {encoding!r}"}
    raw_content = data.get("content") or ""
    try:
        decoded_bytes = base64.b64decode(raw_content)
    except Exception as e:
        return {"error": f"could not decode base64 content: {e}"}
    if len(decoded_bytes) > _MAX_FILE_BYTES:
        return {
            "error": (
                f"file too large ({len(decoded_bytes)} bytes; cap is "
                f"{_MAX_FILE_BYTES}). Specify a smaller file or read a "
                "specific section locally."
            ),
        }
    try:
        text = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "error": (
                f"file is binary or non-UTF-8: {owner}/{repo}/{path_norm}. "
                "Tool returns text only."
            ),
        }
    return {
        "owner": owner,
        "repo": repo,
        "path": path_norm,
        "ref": ref,
        "size_bytes": len(decoded_bytes),
        "sha": data.get("sha"),
        "content": text,
    }


def _list_repo_handler(
    *,
    owner: str,
    repo: str,
    path: str = "",
    ref: str = "main",
    base_url: str,
    token: str | None,
    allowed_owners: list[str] | None,
) -> dict[str, Any]:
    """Implementation of github.list_repo."""
    err = _check_owner_allowed(owner, allowed_owners)
    if err is not None:
        return {"error": err}
    if not owner or not repo:
        return {"error": "owner and repo are required"}
    path_norm = path.strip("/")
    api_path = f"/repos/{owner}/{repo}/contents/{path_norm}" if path_norm else (
        f"/repos/{owner}/{repo}/contents"
    )
    params = {"ref": ref} if ref else None
    try:
        with _build_client(base_url, token) as client:
            resp = client.get(api_path, params=params)
    except httpx.HTTPError as e:
        return {"error": f"network error listing {owner}/{repo}/{path_norm}: {e}"}
    if resp.status_code == 404:
        return {"error": f"not found: {owner}/{repo}/{path_norm}@{ref}"}
    if resp.status_code in (401, 403):
        return {"error": f"unauthorized ({resp.status_code}) for {owner}/{repo}"}
    if resp.status_code >= 400:
        return {"error": f"github api {resp.status_code}: {resp.text[:200]}"}
    data = resp.json()
    if isinstance(data, dict):
        # Single file at this path, not a directory listing.
        return {
            "error": (
                f"{path_norm!r} is a file, not a directory. Use "
                "github.fetch_file to read it."
            ),
        }
    entries = []
    for item in data:
        entries.append({
            "name": item.get("name"),
            "type": item.get("type"),  # "file" | "dir" | "symlink"
            "size": item.get("size", 0),
            "path": item.get("path"),
        })
    return {
        "owner": owner,
        "repo": repo,
        "path": path_norm,
        "ref": ref,
        "count": len(entries),
        "entries": entries[:200],  # safety cap; very large repos exist
        "truncated": len(entries) > 200,
    }


def _put_file_handler(
    *,
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str = "main",
    sha: str | None = None,
    dry_run: bool = False,
    base_url: str,
    token: str | None,
    allowed_owners: list[str] | None,
) -> dict[str, Any]:
    """Create or update a file in a GitHub repo via Contents API.

    GitHub's PUT /repos/{owner}/{repo}/contents/{path} requires the
    *current* file ``sha`` when updating an existing file (optimistic
    concurrency — prevents lost updates). To make this ergonomic we
    auto-fetch the existing SHA on update if the caller didn't pass
    one. Costs one extra round trip; saves the LLM from a two-step
    fetch_file → put_file dance.

    Returns a dict with:
      - executed (bool), action ("create" | "update")
      - commit_sha, commit_url, content_sha (on success)
      - error (on failure — never raises for operational errors)
    """
    err = _check_owner_allowed(owner, allowed_owners)
    if err is not None:
        return {"ok": False, "error": err}
    if not owner or not repo or not path:
        return {"ok": False, "error": "owner, repo, and path are required"}
    if not message:
        return {"ok": False, "error": "commit message is required"}
    if not token:
        return {
            "ok": False,
            "error": (
                "github write requires authentication. Set GITHUB_PAT "
                "with `repo` scope (Contents:write for fine-grained "
                "tokens) and restart."
            ),
        }
    path_norm = path.strip("/")
    api_path = f"/repos/{owner}/{repo}/contents/{path_norm}"

    # Auto-fetch current SHA when updating without one provided.
    auto_sha_lookup_done = False
    if sha is None:
        try:
            with _build_client(base_url, token) as client:
                head = client.get(api_path, params={"ref": branch})
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"network error checking existing file: {e}"}
        if head.status_code == 404:
            existing = False
        elif head.status_code in (401, 403):
            return {
                "ok": False,
                "error": (
                    f"unauthorized ({head.status_code}) checking "
                    f"existing file. Token may lack Contents:read."
                ),
            }
        elif head.status_code >= 400:
            return {
                "ok": False,
                "error": f"github api {head.status_code} on existence check: {head.text[:200]}",
            }
        else:
            head_data = head.json()
            if isinstance(head_data, list):
                return {
                    "ok": False,
                    "error": (
                        f"{path_norm!r} is a directory in {owner}/{repo} on "
                        f"branch {branch!r}; cannot write a file at a directory path"
                    ),
                }
            sha = head_data.get("sha")
            existing = sha is not None
            auto_sha_lookup_done = True
    else:
        existing = True  # caller asserts the file exists with this sha

    plan = {
        "action": "update" if existing else "create",
        "target": f"{owner}/{repo}/{path_norm}",
        "branch": branch,
        "would_write_bytes": len(content.encode("utf-8")),
        "auto_sha_lookup": auto_sha_lookup_done,
        "side_effects": (
            [f"new commit on {branch} replacing existing {path_norm}"]
            if existing else
            [f"new commit on {branch} creating {path_norm}"]
        ),
    }
    if dry_run:
        return {"plan": plan, "executed": False, "preview_only": True}

    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing and sha:
        body["sha"] = sha

    try:
        with _build_client(base_url, token) as client:
            resp = client.put(api_path, json=body)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"network error during PUT: {e}"}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token may lack "
                "Contents:write scope or repo-write permission."
            ),
        }
    if resp.status_code == 409:
        return {
            "ok": False,
            "error": (
                "conflict (409) — sha is stale (someone else updated "
                "the file). Re-fetch and try again."
            ),
        }
    if resp.status_code == 422:
        return {
            "ok": False,
            "error": (
                f"validation error (422): {resp.text[:300]}. Common "
                "causes: branch doesn't exist, path is invalid, or sha "
                "missing on update."
            ),
        }
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"github api {resp.status_code}: {resp.text[:200]}",
        }
    data = resp.json() or {}
    commit = data.get("commit") or {}
    file_meta = data.get("content") or {}
    return {
        "ok": True,
        "executed": True,
        "plan": plan,
        "commit_sha": commit.get("sha"),
        "commit_url": commit.get("html_url"),
        "content_sha": file_meta.get("sha"),
        "outcome_score": 1.0,
    }


def _create_issue_handler(
    *,
    owner: str,
    repo: str,
    title: str,
    body: str | None = None,
    labels: list[str] | None = None,
    dry_run: bool = False,
    base_url: str,
    token: str | None,
    allowed_owners: list[str] | None,
) -> dict[str, Any]:
    """Create a GitHub issue."""
    err = _check_owner_allowed(owner, allowed_owners)
    if err is not None:
        return {"ok": False, "error": err}
    if not owner or not repo:
        return {"ok": False, "error": "owner and repo are required"}
    if not title or not title.strip():
        return {"ok": False, "error": "title is required and must be non-empty"}
    if not token:
        return {
            "ok": False,
            "error": (
                "github write requires authentication. Set GITHUB_PAT "
                "with `repo` scope (Issues:write for fine-grained "
                "tokens) and restart."
            ),
        }
    plan = {
        "action": "create_issue",
        "target": f"{owner}/{repo}",
        "title": title,
        "body_chars": len(body or ""),
        "labels": labels or [],
    }
    if dry_run:
        return {"plan": plan, "executed": False, "preview_only": True}

    payload: dict[str, Any] = {"title": title}
    if body:
        payload["body"] = body
    if labels:
        payload["labels"] = labels

    try:
        with _build_client(base_url, token) as client:
            resp = client.post(f"/repos/{owner}/{repo}/issues", json=payload)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"network error: {e}"}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": (
                f"unauthorized ({resp.status_code}) — token may lack "
                "Issues:write scope."
            ),
        }
    if resp.status_code == 410:
        return {
            "ok": False,
            "error": "issues are disabled on this repo (410)",
        }
    if resp.status_code == 422:
        return {
            "ok": False,
            "error": f"validation error (422): {resp.text[:300]}",
        }
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"github api {resp.status_code}: {resp.text[:200]}",
        }
    data = resp.json() or {}
    return {
        "ok": True,
        "executed": True,
        "plan": plan,
        "issue_number": data.get("number"),
        "issue_url": data.get("html_url"),
        "issue_id": data.get("id"),
        "outcome_score": 1.0,
    }


def register_github_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register github.* capabilities. Idempotent.

    Pulls token from ``GITHUB_PAT`` env (preferred), falling back to
    ``GITHUB_TOKEN`` (the convention curl/gh use). No token is OK —
    public repos still work, just at the unauthenticated rate limit.
    """
    gh_cfg = (config or {}).get("capabilities", {}).get("github", {})
    base_url: str = gh_cfg.get("base_url", _DEFAULT_BASE_URL)
    allowed_owners: list[str] | None = gh_cfg.get("allowed_owners") or None
    token: str | None = (
        os.environ.get("GITHUB_PAT")
        or os.environ.get("GITHUB_TOKEN")
        or None
    )

    auth_status = "authenticated" if token else "anonymous (60/hr rate limit)"
    logger.info(
        "Registering github.* capabilities: base=%s, auth=%s, "
        "allowed_owners=%s",
        base_url, auth_status,
        allowed_owners if allowed_owners else "any",
    )

    # Re-read env at call time so a token added post-boot via
    # setup.save_credential is picked up without a restart. The
    # boot-time ``token`` is the fallback (so config-via-windyfly.toml
    # still works for tests + non-env layouts). Mirrors the cloudflare
    # pattern; without this, hot-load via setup_status was a no-op for
    # github specifically. Found by audit 2026-04-27.
    def _live_token() -> str | None:
        return (
            os.environ.get("GITHUB_PAT")
            or os.environ.get("GITHUB_TOKEN")
            or token
        )

    def fetch_file(
        owner: str,
        repo: str,
        path: str = "README.md",
        ref: str = "main",
    ) -> dict[str, Any]:
        return _fetch_file_handler(
            owner=owner, repo=repo, path=path, ref=ref,
            base_url=base_url, token=_live_token(), allowed_owners=allowed_owners,
        )

    def list_repo(
        owner: str,
        repo: str,
        path: str = "",
        ref: str = "main",
    ) -> dict[str, Any]:
        return _list_repo_handler(
            owner=owner, repo=repo, path=path, ref=ref,
            base_url=base_url, token=_live_token(), allowed_owners=allowed_owners,
        )

    def put_file(
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str = "main",
        sha: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _put_file_handler(
            owner=owner, repo=repo, path=path, content=content,
            message=message, branch=branch, sha=sha, dry_run=dry_run,
            base_url=base_url, token=_live_token(),
            allowed_owners=allowed_owners,
        )

    def create_issue(
        owner: str,
        repo: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _create_issue_handler(
            owner=owner, repo=repo, title=title, body=body, labels=labels,
            dry_run=dry_run,
            base_url=base_url, token=_live_token(),
            allowed_owners=allowed_owners,
        )

    registry.register(Capability(
        id="github.fetch_file",
        description=(
            "Fetch the contents of a file from a GitHub repo. Defaults "
            "to 'README.md' on the main branch — perfect for 'what does "
            "the README of foo/bar say?'. Returns up to 256 KB of UTF-8 "
            "text. Public repos work without auth; private repos need "
            "GITHUB_PAT in the environment. Use github.list_repo first "
            "if you don't know the file path."
        ),
        handler=fetch_file,
        tier=Tier.READ_EXTERNAL,
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "GitHub user or org (e.g. 'sneakyfree').",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name (e.g. 'windy-agent').",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Path within the repo. Defaults to 'README.md'. "
                        "Use forward slashes; no leading slash needed."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Branch / tag / commit SHA. Defaults to 'main'."
                    ),
                },
            },
            "required": ["owner", "repo"],
        },
    ))

    registry.register(Capability(
        id="github.list_repo",
        description=(
            "List files and directories in a GitHub repo at the given "
            "path (root if path is empty). Returns name, type "
            "(file/dir/symlink), and size for each entry. Use this "
            "first when exploring an unfamiliar repo, then "
            "github.fetch_file to read specific files."
        ),
        handler=list_repo,
        tier=Tier.READ_EXTERNAL,
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "GitHub user or org.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Path within the repo. Empty / omitted = repo "
                        "root."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": "Branch / tag / commit SHA.",
                },
            },
            "required": ["owner", "repo"],
        },
    ))

    registry.register(Capability(
        id="github.put_file",
        description=(
            "Create or update a file in a GitHub repo via the Contents "
            "API. Defaults to the 'main' branch. For updates, you can "
            "omit `sha` and the tool will auto-fetch the current sha "
            "(one extra request, optimistic-concurrency safe). "
            "Requires GITHUB_PAT with Contents:write scope. Use "
            "dry_run=true to preview the commit plan without writing. "
            "Tier EXTERNAL_EFFECT — TRUSTED+ band only."
        ),
        handler=put_file,
        tier=Tier.EXTERNAL_EFFECT,
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "GitHub user or org (e.g. 'sneakyfree').",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Path within the repo to create or update. "
                        "Forward slashes; no leading slash."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "UTF-8 text content for the file.",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message — required.",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name. Defaults to 'main'.",
                },
                "sha": {
                    "type": "string",
                    "description": (
                        "Current sha of the file (required by GitHub "
                        "for updates). Omit to auto-fetch."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, return the plan without writing.",
                },
            },
            "required": ["owner", "repo", "path", "content", "message"],
        },
    ))

    registry.register(Capability(
        id="github.create_issue",
        description=(
            "Open a new issue on a GitHub repo. Title is required; "
            "body is plain markdown (optional); labels is an optional "
            "list of label names that must already exist on the repo. "
            "Requires GITHUB_PAT with Issues:write scope. Use "
            "dry_run=true to preview without filing. Tier "
            "EXTERNAL_EFFECT — TRUSTED+ band only."
        ),
        handler=create_issue,
        tier=Tier.EXTERNAL_EFFECT,
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "GitHub user or org.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name.",
                },
                "title": {
                    "type": "string",
                    "description": "Issue title (required, non-empty).",
                },
                "body": {
                    "type": "string",
                    "description": "Optional markdown body.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of existing label names to "
                        "apply. Labels that don't exist on the repo "
                        "cause a 422 validation error."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, return plan without filing.",
                },
            },
            "required": ["owner", "repo", "title"],
        },
    ))
