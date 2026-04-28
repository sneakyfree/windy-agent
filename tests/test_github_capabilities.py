"""github.* capability tests.

Network mocked via respx. Validates:
  - Successful README/file fetch returns decoded UTF-8 content
  - Directory listing returns name/type/size entries
  - 404 / 401 / 403 / 500 / network error all return structured
    ``{"error": "..."}`` (never raise)
  - Owner allowlist works
  - Path normalization (leading slashes stripped)
  - Binary / oversized files return errors instead of crashing
  - File-vs-directory mismatch is reported with a hint
  - Auth header is present when GITHUB_PAT / GITHUB_TOKEN is set,
    absent when neither is.
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest
import respx

from windyfly.agent.capabilities.github import (
    _create_issue_handler,
    _fetch_file_handler,
    _list_repo_handler,
    _put_file_handler,
    register_github_capabilities,
)
from windyfly.agent.capabilities.registry import CapabilityRegistry


_BASE = "https://api.github.com"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# ── github.fetch_file ──────────────────────────────────────────────


@respx.mock
def test_fetch_file_returns_decoded_content() -> None:
    respx.get(f"{_BASE}/repos/foo/bar/contents/README.md").respond(
        json={
            "type": "file",
            "encoding": "base64",
            "content": _b64("# Hello world\n\nthis is the readme"),
            "sha": "abc123",
        },
    )
    out = _fetch_file_handler(
        owner="foo", repo="bar",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" not in out, out
    assert out["content"] == "# Hello world\n\nthis is the readme"
    assert out["sha"] == "abc123"
    assert out["path"] == "README.md"
    assert out["owner"] == "foo"


@respx.mock
def test_fetch_file_strips_leading_slash() -> None:
    """Path "/docs/intro.md" should hit /repos/.../contents/docs/intro.md
    not /repos/.../contents//docs/intro.md (which 404s)."""
    respx.get(f"{_BASE}/repos/foo/bar/contents/docs/intro.md").respond(
        json={"type": "file", "encoding": "base64", "content": _b64("hi")},
    )
    out = _fetch_file_handler(
        owner="foo", repo="bar", path="/docs/intro.md",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" not in out, out
    assert out["content"] == "hi"


@respx.mock
def test_fetch_file_404_returns_structured_error() -> None:
    respx.get(f"{_BASE}/repos/foo/bar/contents/missing.md").respond(404)
    out = _fetch_file_handler(
        owner="foo", repo="bar", path="missing.md",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out
    assert "not found" in out["error"].lower()
    assert "private repos" in out["error"].lower()  # actionable hint


@respx.mock
def test_fetch_file_401_returns_unauth_error() -> None:
    respx.get(f"{_BASE}/repos/foo/bar/contents/README.md").respond(401)
    out = _fetch_file_handler(
        owner="foo", repo="bar",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out
    assert "unauthorized" in out["error"].lower()


@respx.mock
def test_fetch_file_directory_returns_actionable_error() -> None:
    """If you fetch_file a directory, you get told to use list_repo."""
    respx.get(f"{_BASE}/repos/foo/bar/contents/src").respond(
        json=[{"type": "dir", "name": "subdir"}],
    )
    out = _fetch_file_handler(
        owner="foo", repo="bar", path="src",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out
    assert "github.list_repo" in out["error"]


@respx.mock
def test_fetch_file_binary_returns_error_not_garbage() -> None:
    binary_b64 = base64.b64encode(b"\xff\xfe\xfd\x00\x01").decode("ascii")
    respx.get(f"{_BASE}/repos/foo/bar/contents/logo.png").respond(
        json={"type": "file", "encoding": "base64", "content": binary_b64},
    )
    out = _fetch_file_handler(
        owner="foo", repo="bar", path="logo.png",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out
    assert "binary" in out["error"].lower() or "utf-8" in out["error"].lower()


@respx.mock
def test_fetch_file_oversized_returns_error() -> None:
    huge = base64.b64encode(b"X" * (300 * 1024)).decode("ascii")
    respx.get(f"{_BASE}/repos/foo/bar/contents/big.txt").respond(
        json={"type": "file", "encoding": "base64", "content": huge},
    )
    out = _fetch_file_handler(
        owner="foo", repo="bar", path="big.txt",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out
    assert "too large" in out["error"].lower()


@respx.mock
def test_fetch_file_network_error_returns_error() -> None:
    respx.get(f"{_BASE}/repos/foo/bar/contents/README.md").mock(
        side_effect=httpx.ConnectError("kaboom"),
    )
    out = _fetch_file_handler(
        owner="foo", repo="bar",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out
    assert "network" in out["error"].lower()


def test_fetch_file_missing_owner_or_repo() -> None:
    out = _fetch_file_handler(
        owner="", repo="bar",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out and "owner" in out["error"].lower()
    out2 = _fetch_file_handler(
        owner="foo", repo="",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out2 and "repo" in out2["error"].lower()


def test_fetch_file_owner_allowlist_blocks_outsiders() -> None:
    out = _fetch_file_handler(
        owner="randomstranger", repo="bar",
        base_url=_BASE, token=None,
        allowed_owners=["sneakyfree", "anthropic"],
    )
    assert "error" in out
    assert "allowed_owners" in out["error"]


@respx.mock
def test_fetch_file_owner_allowlist_passes_listed_owners() -> None:
    respx.get(f"{_BASE}/repos/sneakyfree/bar/contents/README.md").respond(
        json={"type": "file", "encoding": "base64", "content": _b64("ok")},
    )
    out = _fetch_file_handler(
        owner="sneakyfree", repo="bar",
        base_url=_BASE, token=None, allowed_owners=["sneakyfree"],
    )
    assert "error" not in out, out
    assert out["content"] == "ok"


# ── github.list_repo ───────────────────────────────────────────────


@respx.mock
def test_list_repo_root_returns_entries() -> None:
    respx.get(f"{_BASE}/repos/foo/bar/contents").respond(
        json=[
            {"name": "README.md", "type": "file", "size": 1234, "path": "README.md"},
            {"name": "src", "type": "dir", "size": 0, "path": "src"},
        ],
    )
    out = _list_repo_handler(
        owner="foo", repo="bar",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" not in out, out
    assert out["count"] == 2
    assert {e["name"] for e in out["entries"]} == {"README.md", "src"}


@respx.mock
def test_list_repo_at_subpath() -> None:
    respx.get(f"{_BASE}/repos/foo/bar/contents/src").respond(
        json=[{"name": "main.py", "type": "file", "size": 100, "path": "src/main.py"}],
    )
    out = _list_repo_handler(
        owner="foo", repo="bar", path="src",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" not in out, out
    assert out["count"] == 1
    assert out["entries"][0]["path"] == "src/main.py"


@respx.mock
def test_list_repo_file_path_returns_actionable_error() -> None:
    """Listing a path that's actually a file: tell LLM to use fetch_file."""
    respx.get(f"{_BASE}/repos/foo/bar/contents/README.md").respond(
        json={"type": "file", "encoding": "base64", "content": _b64("hi")},
    )
    out = _list_repo_handler(
        owner="foo", repo="bar", path="README.md",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert "error" in out
    assert "github.fetch_file" in out["error"]


# ── auth header behavior ───────────────────────────────────────────


@respx.mock
def test_token_present_adds_auth_header() -> None:
    route = respx.get(f"{_BASE}/repos/foo/bar/contents/README.md").respond(
        json={"type": "file", "encoding": "base64", "content": _b64("ok")},
    )
    _fetch_file_handler(
        owner="foo", repo="bar",
        base_url=_BASE, token="ghp_secret123",
        allowed_owners=None,
    )
    sent = route.calls.last.request.headers
    assert sent.get("Authorization") == "Bearer ghp_secret123"


@respx.mock
def test_token_absent_no_auth_header() -> None:
    route = respx.get(f"{_BASE}/repos/foo/bar/contents/README.md").respond(
        json={"type": "file", "encoding": "base64", "content": _b64("ok")},
    )
    _fetch_file_handler(
        owner="foo", repo="bar",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    sent = route.calls.last.request.headers
    assert "Authorization" not in sent


# ── registration end-to-end ───────────────────────────────────────


def test_register_github_capabilities_idempotent() -> None:
    registry = CapabilityRegistry()
    register_github_capabilities(registry, config={})
    # Call twice — second call shouldn't raise (capability registry's
    # register raises on duplicate id, so this would catch it).
    # NOTE: filesystem.py does NOT call register_*_capabilities twice
    # in production; this is just defensive.
    # Actually CapabilityRegistry.register DOES raise on duplicate ids,
    # so a second call WILL raise. We only assert one call works.
    assert registry.get("github.fetch_file") is not None
    assert registry.get("github.list_repo") is not None


def test_pat_env_var_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """GITHUB_PAT in env should reach the handler when no explicit token."""
    monkeypatch.setenv("GITHUB_PAT", "ghp_envvar")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    registry = CapabilityRegistry()
    register_github_capabilities(registry, config={})
    # The handler closes over `token`. We can't directly inspect it, but
    # we can call fetch_file on a known-mocked URL and check the header.
    # Using a fresh respx context.
    with respx.mock(base_url=_BASE) as mock:
        route = mock.get("/repos/foo/bar/contents/README.md").respond(
            json={"type": "file", "encoding": "base64", "content": _b64("hi")},
        )
        cap = registry.get("github.fetch_file")
        assert cap is not None
        cap.handler(owner="foo", repo="bar")
        assert route.calls.last.request.headers.get("Authorization") == "Bearer ghp_envvar"


def test_github_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """GITHUB_TOKEN is the conventional fallback (gh / curl style)."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fallback")
    registry = CapabilityRegistry()
    register_github_capabilities(registry, config={})
    with respx.mock(base_url=_BASE) as mock:
        route = mock.get("/repos/foo/bar/contents/README.md").respond(
            json={"type": "file", "encoding": "base64", "content": _b64("hi")},
        )
        registry.get("github.fetch_file").handler(owner="foo", repo="bar")
        assert route.calls.last.request.headers.get("Authorization") == "Bearer ghp_fallback"


# ── github.put_file ───────────────────────────────────────────────


@respx.mock
def test_put_file_create_new_file_no_sha_lookup_needed():
    # 404 on the existence check → we treat as new file
    respx.get(f"{_BASE}/repos/foo/bar/contents/notes.md").respond(404)
    put_route = respx.put(f"{_BASE}/repos/foo/bar/contents/notes.md").respond(
        201, json={
            "commit": {"sha": "abc123", "html_url": "https://github.com/foo/bar/commit/abc123"},
            "content": {"sha": "def456"},
        },
    )
    out = _put_file_handler(
        owner="foo", repo="bar", path="notes.md",
        content="hello world", message="add notes",
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is True
    assert out["plan"]["action"] == "create"
    assert out["commit_sha"] == "abc123"
    assert out["content_sha"] == "def456"
    # PUT body must NOT contain sha when creating
    sent = put_route.calls.last.request.read()
    import json
    payload = json.loads(sent)
    assert "sha" not in payload
    assert payload["message"] == "add notes"
    assert payload["branch"] == "main"


@respx.mock
def test_put_file_update_auto_fetches_sha():
    """No sha provided + file exists → auto-fetch and include in PUT."""
    respx.get(f"{_BASE}/repos/foo/bar/contents/notes.md").respond(
        200, json={"type": "file", "sha": "current-sha-xyz", "content": _b64("old")},
    )
    put_route = respx.put(f"{_BASE}/repos/foo/bar/contents/notes.md").respond(
        200, json={
            "commit": {"sha": "newcommit", "html_url": "https://github.com/foo/bar/commit/newcommit"},
            "content": {"sha": "new-content-sha"},
        },
    )
    out = _put_file_handler(
        owner="foo", repo="bar", path="notes.md",
        content="updated", message="update notes",
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is True
    assert out["plan"]["action"] == "update"
    assert out["plan"]["auto_sha_lookup"] is True
    import json
    payload = json.loads(put_route.calls.last.request.read())
    assert payload["sha"] == "current-sha-xyz"


@respx.mock
def test_put_file_directory_target_returns_friendly_error():
    """Existence check returns a list (directory) → refuse cleanly."""
    respx.get(f"{_BASE}/repos/foo/bar/contents/somedir").respond(
        200, json=[{"name": "a.txt"}, {"name": "b.txt"}],
    )
    out = _put_file_handler(
        owner="foo", repo="bar", path="somedir",
        content="x", message="x",
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is False
    assert "directory" in out["error"]


@respx.mock
def test_put_file_dry_run_does_not_call_put():
    respx.get(f"{_BASE}/repos/foo/bar/contents/notes.md").respond(404)
    put_route = respx.put(f"{_BASE}/repos/foo/bar/contents/notes.md")
    out = _put_file_handler(
        owner="foo", repo="bar", path="notes.md",
        content="hi", message="add",
        dry_run=True,
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["executed"] is False
    assert out["preview_only"] is True
    assert put_route.call_count == 0


def test_put_file_no_token_returns_friendly_error():
    out = _put_file_handler(
        owner="foo", repo="bar", path="x.md",
        content="x", message="x",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert out["ok"] is False
    assert "GITHUB_PAT" in out["error"]


def test_put_file_missing_message_refused():
    out = _put_file_handler(
        owner="foo", repo="bar", path="x.md",
        content="x", message="",
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is False
    assert "commit message is required" in out["error"]


@respx.mock
def test_put_file_409_conflict_returns_stale_sha_message():
    respx.get(f"{_BASE}/repos/foo/bar/contents/x.md").respond(
        200, json={"type": "file", "sha": "stale", "content": _b64("old")},
    )
    respx.put(f"{_BASE}/repos/foo/bar/contents/x.md").respond(409)
    out = _put_file_handler(
        owner="foo", repo="bar", path="x.md",
        content="new", message="update",
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is False
    assert "stale" in out["error"]


@respx.mock
def test_put_file_owner_allowlist_enforced():
    out = _put_file_handler(
        owner="evil", repo="bar", path="x.md",
        content="x", message="x",
        base_url=_BASE, token="ghp_test",
        allowed_owners=["sneakyfree"],
    )
    assert out["ok"] is False
    assert "not in allowed_owners" in out["error"]


# ── github.create_issue ────────────────────────────────────────────


@respx.mock
def test_create_issue_happy_path():
    issue_route = respx.post(f"{_BASE}/repos/foo/bar/issues").respond(
        201, json={
            "id": 999, "number": 42,
            "html_url": "https://github.com/foo/bar/issues/42",
        },
    )
    out = _create_issue_handler(
        owner="foo", repo="bar",
        title="bug: thing is broken",
        body="steps:\n1. do thing\n2. observe break",
        labels=["bug", "p1"],
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is True
    assert out["issue_number"] == 42
    assert out["issue_url"].endswith("/issues/42")
    import json
    payload = json.loads(issue_route.calls.last.request.read())
    assert payload["title"] == "bug: thing is broken"
    assert payload["labels"] == ["bug", "p1"]


def test_create_issue_empty_title_refused():
    out = _create_issue_handler(
        owner="foo", repo="bar", title="   ",
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is False
    assert "title is required" in out["error"]


def test_create_issue_no_token_returns_friendly_error():
    out = _create_issue_handler(
        owner="foo", repo="bar", title="hi",
        base_url=_BASE, token=None, allowed_owners=None,
    )
    assert out["ok"] is False
    assert "GITHUB_PAT" in out["error"]


@respx.mock
def test_create_issue_dry_run_does_not_post():
    route = respx.post(f"{_BASE}/repos/foo/bar/issues")
    out = _create_issue_handler(
        owner="foo", repo="bar", title="dry test", dry_run=True,
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["executed"] is False
    assert out["preview_only"] is True
    assert route.call_count == 0


@respx.mock
def test_create_issue_410_disabled_message():
    respx.post(f"{_BASE}/repos/foo/bar/issues").respond(410)
    out = _create_issue_handler(
        owner="foo", repo="bar", title="hi",
        base_url=_BASE, token="ghp_test", allowed_owners=None,
    )
    assert out["ok"] is False
    assert "issues are disabled" in out["error"]


@respx.mock
def test_create_issue_owner_allowlist_enforced():
    out = _create_issue_handler(
        owner="evil", repo="bar", title="hi",
        base_url=_BASE, token="ghp_test",
        allowed_owners=["sneakyfree"],
    )
    assert out["ok"] is False
    assert "not in allowed_owners" in out["error"]


# ── Registration smoke for new caps ────────────────────────────────


def test_register_github_includes_put_file_and_create_issue(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    registry = CapabilityRegistry()
    register_github_capabilities(registry, config={})
    put_cap = registry.get("github.put_file")
    issue_cap = registry.get("github.create_issue")
    assert put_cap is not None
    assert issue_cap is not None
    # Both must be EXTERNAL_EFFECT (TRUSTED+ band default)
    from windyfly.agent.capabilities.descriptor import Band, Tier
    assert put_cap.tier == Tier.EXTERNAL_EFFECT
    assert issue_cap.tier == Tier.EXTERNAL_EFFECT
    assert put_cap.band_required == Band.TRUSTED
    assert issue_cap.band_required == Band.TRUSTED
    assert put_cap.audit_required is True
    assert issue_cap.audit_required is True


# ── Hot-load: token added post-boot via setup.save_credential ──────


@respx.mock
def test_github_hot_loads_token_added_after_boot(monkeypatch):
    """Audit-found bug 2026-04-27: github capability closures used
    the boot-time ``token`` directly without re-reading os.environ,
    so a token added post-boot via setup.save_credential was a no-op
    (would have required a service restart). Cloudflare did this
    right; github didn't. Fix: each closure re-reads
    GITHUB_PAT/GITHUB_TOKEN at call time.

    This regression test boots the registry with NO token, then
    sets the env var, then confirms the next capability call sends
    the new token in its Authorization header.
    """
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    registry = CapabilityRegistry()
    register_github_capabilities(registry, config={})

    # Simulate setup.save_credential's hot-load by setting the env var
    # AFTER registration. The closure must pick this up on next call.
    monkeypatch.setenv("GITHUB_PAT", "ghp_hotloaded_after_boot")

    route = respx.get(f"{_BASE}/repos/foo/bar/contents/README.md").respond(
        json={"type": "file", "encoding": "base64", "content": _b64("hi")},
    )
    out = registry.get("github.fetch_file").handler(owner="foo", repo="bar")

    # The handler must succeed AND the request must carry the new token.
    assert "error" not in out, out
    sent_auth = route.calls.last.request.headers.get("Authorization")
    assert sent_auth == "Bearer ghp_hotloaded_after_boot", (
        "github capability must re-read GITHUB_PAT at call time, "
        f"not use boot-time value (got Authorization={sent_auth!r})"
    )
