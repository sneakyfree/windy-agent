"""The Windy Cloud file tools against Cloud's real contract (strand P4.11.28-30).

The old tests mocked a guessed route (/api/v1/archive/file), which Windy Cloud does not have, so they passed
while the tools failed in production. This fake Cloud implements the contract **as Windy Cloud gave it on
10-17** (prod 1045b17; quoted in specs/AGENT_CLOUD_FILES_STORAGE_ROUTES_2026-10-17.md):

- POST /api/v1/storage/upload: multipart "file" + product / file_type / metadata. Answers
  {file_id, key, size, content_type, message, storage_class, notice}.
- GET /api/v1/storage/files: answers {files: [{file_id, product, file_type, filename, storage_key,
  size_bytes, content_type, encrypted, created_at, storage_class}], total, next_token, truncated}.
- GET /api/v1/storage/files/{file_id}: the raw bytes with Content-Disposition, never a redirect; an unknown
  id (or another identity's) answers 404.
- Bearer JWT only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from windyfly.agent.capabilities import Band
from windyfly.tools import bands
from windyfly.tools import cloud
from windyfly.tools.registry import ToolRegistry

URL = "https://cloud.example.test"


class FakeCloud:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.files: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.status: dict[str, int] = {}          # path suffix -> forced status
        self.redirect: str | None = None
        self.disposition: str | None = None
        self.no_length = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        assert request.headers.get("authorization") == "Bearer cloud_test_token"
        assert not any(h.lower().startswith(("x-agent", "dpop", "x-eternitas")) for h in request.headers)
        for suffix, code in self.status.items():
            if path.endswith(suffix):
                return httpx.Response(code, json={"detail": "x"})
        if self.redirect:
            return httpx.Response(302, headers={"Location": self.redirect})
        if path == "/api/v1/storage/upload" and request.method == "POST":
            body = request.read()
            fid = f"f{len(self.files) + 1:04d}"
            name = body.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
            self.files[fid] = {"file_id": fid, "product": "windy_fly", "file_type": "file", "filename": name,
                               "storage_key": f"k/{fid}", "size_bytes": 11, "content_type": "text/plain",
                               "encrypted": False, "created_at": "2026-10-17T10:00:00Z", "storage_class": "standard"}
            return httpx.Response(201, json={"file_id": fid, "key": f"k/{fid}", "size": 11,
                                             "content_type": "text/plain", "message": "ok",
                                             "storage_class": "standard", "notice": None})
        if path == "/api/v1/storage/files" and request.method == "GET":
            return httpx.Response(200, json={"files": list(self.files.values()), "total": len(self.files),
                                             "next_token": None, "truncated": False})
        if path.startswith("/api/v1/storage/files/"):
            fid = path.rsplit("/", 1)[-1]
            if fid not in self.blobs:
                return httpx.Response(404, json={"detail": "File not found"})
            headers = {"content-disposition": self.disposition or f'attachment; filename="{fid}.txt"'}
            data = self.blobs[fid]
            if self.no_length:
                return httpx.Response(200, headers=headers, stream=httpx.ByteStream(data))
            return httpx.Response(200, headers=headers, content=data)
        return httpx.Response(404, json={"detail": "Not Found"})       # the old archive/file routes


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeCloud()
    real = httpx.Client
    made: list[dict] = []

    def client(*a, **k):
        made.append(k)
        return real(*a, **{**k, "transport": httpx.MockTransport(f.handler)})

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setenv("WINDY_CLOUD_URL", URL)
    monkeypatch.setenv("WINDY_CLOUD_TOKEN", "cloud_test_token")
    home = tmp_path / "home"
    (home / "docs").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    cloud.register_cloud_tools(ToolRegistry(), config={"capabilities": {"filesystem": {"allowed_roots": [str(home / "docs")]}}})
    f.made = made
    f.home = home
    return f


def _turn(text: str = "please upload it"):
    """Run as the owner's turn with ``text`` as the owner's message."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(bands.turn_band(Band.OWNER))
    stack.enter_context(bands.turn_context(text))
    return stack


def _doc(fake, name="notes.txt", body=b"hello world"):
    p = fake.home / "docs" / name
    p.write_bytes(body)
    return p


# ── the contract ──────────────────────────────────────────────────────────────────────────────

def test_upload_list_download_follow_clouds_contract(fake):
    with _turn():
        up = cloud.upload_to_cloud(str(_doc(fake)), description="trip notes")
    assert up == {"status": "uploaded", "file_id": "f0001", "name": "notes.txt", "size_bytes": 11}
    post = [r for r in fake.requests if r.method == "POST"][0]
    assert str(post.url) == f"{URL}/api/v1/storage/upload"
    body = post.read()
    for field, value in ((b"product", b"windy_fly"), (b"file_type", b"file")):
        assert b'name="' + field + b'"\r\n\r\n' + value in body
    assert b'"description": "trip notes"' in body
    listed = cloud.list_cloud_files()
    assert listed["files"] == [{"file_id": "f0001", "name": "notes.txt", "size_bytes": 11,
                                "created_at": "2026-10-17T10:00:00Z"}]
    assert listed["note"] == cloud.DATA_NOTE
    get = [r for r in fake.requests if r.url.path == "/api/v1/storage/files"][-1]
    assert dict(get.url.params) == {"limit": "20", "offset": "0", "product": "windy_fly"}
    fake.blobs["f0001"] = b"hello world"
    got = cloud.download_cloud_file("f0001")
    assert got["status"] == "downloaded" and Path(got["path"]).read_bytes() == b"hello world"
    assert not any("/archive/" in str(r.url) for r in fake.requests)   # never the routes Cloud lacks
    assert all(k.get("follow_redirects") is False and k.get("trust_env") is False for k in fake.made)


def test_a_redirect_is_refused_and_the_other_host_never_contacted(fake):
    fake.redirect = "https://elsewhere.example.test/x"
    fake.blobs["f1"] = b"x"
    assert "redirect" in cloud.download_cloud_file("f1")["error"]
    assert "redirect" in cloud.list_cloud_files()["error"]
    assert not any("elsewhere" in str(r.url) for r in fake.requests)


@pytest.mark.parametrize("code,words", [
    (401, "refused this agent's credentials"), (403, "frozen or suspended"), (404, "no such file"),
    (410, "trash"), (423, "locked this agent's storage"), (429, "allowance"), (502, "storage had an error"),
])
def test_download_answers_are_plain(fake, code, words):
    fake.status["/f9"] = code
    fake.blobs["f9"] = b"x"
    assert words in cloud.download_cloud_file("f9")["error"]


@pytest.mark.parametrize("code,words", [(413, "100 MB"), (507, "quota is full"), (503, "trust")])
def test_upload_answers_are_plain_and_a_503_is_not_retried(fake, code, words):
    fake.status["/storage/upload"] = code
    with _turn():
        out = cloud.upload_to_cloud(str(_doc(fake)))
    assert words in out["error"]
    assert sum(1 for r in fake.requests if r.url.path == "/api/v1/storage/upload") == 1


def test_another_identitys_file_id_is_a_plain_not_found(fake):
    out = cloud.download_cloud_file("someone-elses-id")
    assert out["status"] == "failed" and "no such file" in out["error"]


def test_unconfigured_is_plain(monkeypatch, fake):
    monkeypatch.delenv("WINDY_CLOUD_URL")
    assert cloud.list_cloud_files()["status"] == "unavailable"


# ── what may be uploaded ──────────────────────────────────────────────────────────────────────

def test_only_files_in_the_allowed_folders_or_named_by_the_owner(fake, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_bytes(b"x")
    with _turn("upload my notes"):
        assert cloud.upload_to_cloud(str(outside))["status"] == "refused"
    with _turn(f"please upload {outside} to my cloud"):
        assert cloud.upload_to_cloud(str(outside))["status"] == "uploaded"


def test_a_link_out_of_the_allowed_folder_is_judged_by_where_it_points(fake, tmp_path):
    target = tmp_path / "private.txt"
    target.write_bytes(b"x")
    link = fake.home / "docs" / "innocent.txt"
    link.symlink_to(target)
    with _turn("upload innocent.txt"):
        assert cloud.upload_to_cloud(str(link))["status"] == "refused"


@pytest.mark.parametrize("rel", [".ssh/id_ed25519", ".config/app/settings.json", "docs/.env", "docs/api_token.txt",
                                 "docs/server.pem", "docs/credentials.json"])
def test_the_denylist_holds_even_inside_the_allowed_folder_and_when_named(fake, rel):
    p = fake.home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"secret")
    with _turn(f"upload {p}"):
        out = cloud.upload_to_cloud(str(p))
    assert out["status"] == "refused" and "won't upload" in out["error"]
    assert not any(r.method == "POST" for r in fake.requests)


def test_the_agents_own_data_is_never_uploaded(fake, monkeypatch, tmp_path):
    state = Path(os.environ["WINDY_STATE_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    for name in ("windyfly.db", "flags.json"):              # by name, and by where it lives
        f = state / name
        f.write_bytes(b"agent data")
        with _turn(f"upload {f}"):
            out = cloud.upload_to_cloud(str(f))
        assert out["status"] == "refused", name


def test_a_directory_and_a_missing_file_are_refused(fake):
    with _turn():
        assert cloud.upload_to_cloud(str(fake.home / "docs"))["status"] == "failed"
        assert cloud.upload_to_cloud("/no/such/file")["status"] == "failed"
        assert cloud.upload_to_cloud("/etc/passwd")["status"] == "refused"


def test_five_uploads_or_200_mb_per_message(fake, monkeypatch):
    with _turn():
        for i in range(5):
            assert cloud.upload_to_cloud(str(_doc(fake, f"n{i}.txt")), replace=True)["status"] == "uploaded"
        assert "at most 5" in cloud.upload_to_cloud(str(_doc(fake, "n6.txt")))["error"]
    monkeypatch.setattr(cloud, "TURN_MAX_BYTES", 15)
    with _turn():
        assert cloud.upload_to_cloud(str(_doc(fake, "a.txt")), replace=True)["status"] == "uploaded"
        assert cloud.upload_to_cloud(str(_doc(fake, "b.txt")), replace=True)["status"] == "refused"


def test_a_same_name_file_asks_first(fake):
    with _turn():
        cloud.upload_to_cloud(str(_doc(fake)))
        again = cloud.upload_to_cloud(str(_doc(fake)))
        assert again["status"] == "confirm_required" and "trash" in again["speak"]
        assert cloud.upload_to_cloud(str(_doc(fake)), replace=True)["status"] == "uploaded"


def test_the_upload_audit_has_counts_and_sizes_only(fake, monkeypatch):
    seen = []
    monkeypatch.setattr(cloud, "_log_event", lambda event, data: seen.append((event, data)))
    with _turn():
        cloud.upload_to_cloud(str(_doc(fake)))
    assert seen == [("cloud.upload", {"count": 1, "bytes": 11})]


# ── where downloads land ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("disposition,expect", [
    ('attachment; filename="../../etc/cron.d/evil"', "f1-evil"),
    ('attachment; filename="a\x00b.sh"', "f1-ab.sh"),
    ('attachment; filename=".bashrc"', "f1-bashrc"),
])
def test_the_servers_name_is_cleaned_and_kept_inside_the_folder(fake, disposition, expect):
    fake.blobs["f1"] = b"x"
    fake.disposition = disposition
    out = cloud.download_cloud_file("f1")
    p = Path(out["path"])
    assert p.parent == cloud.downloads_dir() and p.name == expect
    assert (p.stat().st_mode & 0o777) == 0o600


def test_an_existing_download_is_never_overwritten(fake):
    fake.blobs["f1"] = b"first"
    a = cloud.download_cloud_file("f1")
    fake.blobs["f1"] = b"second"
    b = cloud.download_cloud_file("f1")
    assert a["path"] != b["path"] and Path(a["path"]).read_bytes() == b"first"
    assert Path(b["path"]).name.endswith("-1.txt")


def test_caps_stop_big_files_a_full_folder_and_a_low_disk(fake, monkeypatch):
    fake.blobs["f1"] = b"x" * 50
    monkeypatch.setattr(cloud, "MAX_FILE_BYTES", 10)
    assert cloud.download_cloud_file("f1")["status"] == "refused"
    fake.no_length = True                                     # a missing or lying length: the count stops it
    assert cloud.download_cloud_file("f1")["status"] == "refused"
    assert list(cloud.downloads_dir().iterdir()) == []
    monkeypatch.setattr(cloud, "MAX_FILE_BYTES", 10**8)
    monkeypatch.setattr(cloud, "DOWNLOADS_MAX_TOTAL", 0)
    assert "no room" in cloud.download_cloud_file("f1")["error"]
    monkeypatch.setattr(cloud, "DOWNLOADS_MAX_TOTAL", 10**9)
    monkeypatch.setattr(cloud, "DISK_FLOOR", 10**18)
    assert "no room" in cloud.download_cloud_file("f1")["error"]


def test_a_bad_file_id_never_reaches_cloud(fake):
    for bad in ("../x", "a/b", "", "x" * 200, "id with space"):
        assert cloud.download_cloud_file(bad)["status"] == "failed"
    assert fake.requests == []


# ── who may use them ──────────────────────────────────────────────────────────────────────────

def test_all_three_are_the_owners_only(fake):
    r = ToolRegistry()
    cloud.register_cloud_tools(r, config={})
    for name in ("upload_to_cloud", "list_cloud_files", "download_cloud_file"):
        assert bands.required_band(name) == Band.OWNER
        with bands.turn_band(Band.USER):
            assert "refused" in json.loads(r.execute(name, {"file_path": "x", "file_id": "f1"}))["error"]
    assert {s["function"]["name"] for s in r.get_schemas(Band.USER)} == set()


# ── Hub on #598: profiles, the owner-named rule, the per-turn cap outside a turn ─────────────

@pytest.mark.parametrize("rel", [
    # macOS
    "Library/Application Support/Google/Chrome/Default/History",
    "Library/Application Support/Microsoft Edge/Default/History",
    "Library/Application Support/BraveSoftware/Brave-Browser/Default/Bookmarks",
    "Library/Application Support/Firefox/Profiles/x.default/logins.json",
    "Library/Keychains/login.keychain-db",
    "Library/Safari/History.db",
    # Windows
    "AppData/Local/Google/Chrome/User Data/Default/History",
    "AppData/Local/Microsoft/Edge/User Data/Default/Bookmarks",
    "AppData/Local/BraveSoftware/Brave-Browser/User Data/Default/History",
    "AppData/Roaming/Mozilla/Firefox/Profiles/x.default/places.sqlite",
])
def test_browser_profiles_and_keychains_are_never_uploaded_on_any_os_layout(fake, monkeypatch, rel):
    monkeypatch.setattr(cloud, "_allowed_roots", lambda: [fake.home])        # even inside an allowed folder
    p = fake.home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"private")
    with _turn(f"upload {p}"):
        out = cloud.upload_to_cloud(str(p))
    assert out["status"] == "refused" and "browser profile" in out["error"]
    assert not any(r.method == "POST" for r in fake.requests)


def test_an_ordinary_library_or_appdata_file_still_uploads(fake, monkeypatch):
    monkeypatch.setattr(cloud, "_allowed_roots", lambda: [fake.home])
    for rel in ("Library/Mobile Documents/report.txt", "AppData/Local/Temp/report2.txt"):
        p = fake.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ok")
        with _turn():
            assert cloud.upload_to_cloud(str(p))["status"] == "uploaded", rel


@pytest.mark.parametrize("message,named", [
    ("upload {p}", True),
    ("upload {p}.", True),                          # sentence end
    ('upload "{p}" please', True),
    ("upload ({p})", True),
    ("upload {p}.old", False),                      # a longer path is not this one
    ("upload x{p}", False),
    ("upload a", False),                            # a letter or a bare name never counts
    ("upload elsewhere.txt", False),
    ("> upload {p}\n\nyes do it", False),           # a quoted reply is not the owner's words
])
def test_the_owner_must_write_the_whole_path(fake, tmp_path, message, named):
    p = tmp_path / "elsewhere.txt"
    p.write_bytes(b"x")
    with _turn(message.format(p=p)):
        out = cloud.upload_to_cloud(str(p))
    assert (out["status"] == "uploaded") is named, out


def test_a_typed_single_letter_path_is_never_named(fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a").write_bytes(b"x")
    with _turn("send a to my cloud, a is a file"):
        assert cloud.upload_to_cloud("a")["status"] == "refused"


def test_a_home_relative_path_written_with_a_tilde_counts(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "_allowed_roots", lambda: [tmp_path / "nowhere"])
    p = fake.home / "outside" / "plan.txt"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    with _turn("upload ~/outside/plan.txt"):
        assert cloud.upload_to_cloud("~/outside/plan.txt")["status"] == "uploaded"


def test_no_upload_outside_a_turn(fake):
    out = cloud.upload_to_cloud(str(_doc(fake)))
    assert out["status"] == "refused" and "while answering" in out["error"]
    assert fake.requests == []


def _llm_round(tool_calls):
    return {"content": "" if tool_calls else "done", "model": "gpt-4o-mini", "input_tokens": 10,
            "output_tokens": 5, "tool_calls": tool_calls}


def _agent_turn(fake, tmp_path, monkeypatch, rounds, text="please back up my notes"):
    """One real agent_respond turn whose model makes the tool calls in ``rounds``."""
    from unittest.mock import patch

    from tests.test_gauge_footprint_within_turn import _config
    from windyfly.agent.loop import agent_respond
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue

    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    monkeypatch.setenv("WINDY_YOLO_FLAG", str(tmp_path / ".yolo"))
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(tmp_path / ".guest"))
    registry = ToolRegistry()
    cloud.register_cloud_tools(registry, config={"capabilities": {"filesystem": {"allowed_roots": [str(fake.home / "docs")]}}})
    results: list[dict] = []
    real_execute = registry.execute

    def execute(name, args):
        out = real_execute(name, args)
        results.append({"name": name, **json.loads(out)})
        return out

    registry.execute = execute  # type: ignore[method-assign]
    db, wq = Database(":memory:"), WriteQueue()
    wq.start()
    try:
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=[_llm_round(r) for r in rounds] + [_llm_round(None)]):
            agent_respond(_config(), db, wq, text, "cloud-session", registry)
    finally:
        wq.stop()
        db.close()
    return results


def _call(i, name, **args):
    return {"id": f"c{i}", "function": {"name": name, "arguments": args}}


def test_through_agent_respond_the_cap_is_five_uploads_per_message(fake, tmp_path, monkeypatch):
    paths = [str(_doc(fake, f"n{i}.txt")) for i in range(6)]
    results = _agent_turn(fake, tmp_path, monkeypatch,
                          [[_call(i, "upload_to_cloud", file_path=p, replace=True) for i, p in enumerate(paths)]])
    assert [r.get("status") for r in results] == ["uploaded"] * 5 + ["refused"]
    assert "at most 5" in results[-1]["error"]


def test_listing_all_products_taints_the_turn_so_nothing_goes_out_after(fake, tmp_path, monkeypatch):
    p = str(_doc(fake))
    fake.blobs["f0001"] = b"x"
    results = _agent_turn(fake, tmp_path, monkeypatch, [
        [_call(1, "list_cloud_files", all_products=True)],
        [_call(2, "upload_to_cloud", file_path=p), _call(3, "download_cloud_file", file_id="f0001")],
    ])
    assert [r["name"] for r in results] == ["list_cloud_files"]       # the loop refused both before they ran
    assert not any(r.method == "POST" for r in fake.requests)
    assert not any(r.url.path.endswith("/f0001") for r in fake.requests)


def test_listing_only_this_agents_files_does_not_taint(fake, tmp_path, monkeypatch):
    p = str(_doc(fake))
    results = _agent_turn(fake, tmp_path, monkeypatch, [
        [_call(1, "list_cloud_files")],
        [_call(2, "upload_to_cloud", file_path=p)],
    ])
    assert results[-1]["status"] == "uploaded"


# ── before release (Hub on #598) ──────────────────────────────────────────────────────────────

def test_list_names_lose_control_characters_and_bad_numbers_do_not_raise(fake):
    fake.files["f1"] = {"file_id": "f1", "filename": "ok\x1b[31m‮evil\nname.txt", "size_bytes": "lots",
                        "created_at": "2026-10-17\x00T10"}
    out = cloud.list_cloud_files(limit="many")
    (f,) = out["files"]
    assert f["name"] == "ok[31mevilname.txt" and f["size_bytes"] == 0 and "\x00" not in f["created_at"]
    assert "limit=20" in str(fake.requests[-1].url)


def test_a_failed_write_closes_the_file_and_leaves_no_partial(fake, monkeypatch):
    fake.blobs["f1"] = b"some bytes"
    closed: list[int] = []
    real_close = os.close
    monkeypatch.setattr(cloud.os, "write", lambda fd, b: (_ for _ in ()).throw(OSError(28, "No space left")))
    monkeypatch.setattr(cloud.os, "close", lambda fd: (closed.append(fd), real_close(fd)))
    out = cloud.download_cloud_file("f1")
    assert out["status"] == "failed" and "OSError" in out["error"]
    assert len(closed) == 1 and list(cloud.downloads_dir().iterdir()) == []


def test_the_audit_events_are_known_types():
    from windyfly.observability.events import EVENT_TYPES

    assert {"cloud.upload", "cloud.download"} <= EVENT_TYPES


def test_download_is_refused_on_a_tainted_turn():
    from windyfly.agent.loop import TAINT_FORBIDDEN_TOOLS

    assert {"upload_to_cloud", "download_cloud_file"} <= TAINT_FORBIDDEN_TOOLS


def test_a_browser_profile_folder_is_refused_wherever_it_is(fake, tmp_path):
    p = tmp_path / "snap" / "chromium" / "common" / "chromium" / "Default" / "History"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"private")
    with _turn(f"upload {p}"):
        assert "browser profile" in cloud.upload_to_cloud(str(p))["error"]


def test_a_relative_name_in_the_message_does_not_name_the_file(fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "taxes.pdf").write_bytes(b"x")
    with _turn("upload taxes.pdf to my cloud"):
        assert cloud.upload_to_cloud("taxes.pdf")["status"] == "refused"
    with _turn(f"upload {tmp_path / 'taxes.pdf'} to my cloud"):
        assert cloud.upload_to_cloud("taxes.pdf")["status"] == "uploaded"
