"""Windy Mail on mode B (EPT+agent aud windy-mail + a DPoP proof on EVERY request, reads included), dark
behind WINDY_MAIL_EPT_AGENT=1. Mail refuses a credential it can't verify with 401 before the route runs
(windy-mail #249), so a 401 is retried once with the old credential; nothing else is."""
import httpx
import pytest

from windyfly.agent import service_auth
from windyfly.channels import email as mail_channel
from windyfly.channels.email import WindyMailAdapter

API = "https://api.windymail.ai"


@pytest.fixture
def mints(monkeypatch):
    """Every mint the code asks for, as (aud, method, url)."""
    seen: list[tuple[str, str, str]] = []

    def agent_headers(aud, method, url):
        seen.append((aud, method, url))
        return {"Authorization": "Bearer agent-tok-1", "DPoP": f"proof-{len(seen)}"}

    monkeypatch.setattr(service_auth, "agent_headers", agent_headers)
    return seen


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "zero@windymail.ai")
    monkeypatch.setenv("WINDYMAIL_API_URL", API)
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "jmap-tok")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "LONG-LEGACY-EPT")
    monkeypatch.delenv(mail_channel.ENV_FLAG, raising=False)
    return monkeypatch


def _posts(monkeypatch, *statuses):
    sent: list[dict] = []
    replies = list(statuses)

    def post(url, json=None, headers=None, timeout=None):
        sent.append({"url": url, "headers": dict(headers or {})})
        code = replies.pop(0) if len(replies) > 1 else replies[0]
        return httpx.Response(code, json={"message_id": "m1"} if code < 300 else {"detail": "no"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    return sent


def _gets(monkeypatch, *statuses, body=None):
    seen: list[dict] = []
    replies = list(statuses)

    def get(url, params=None, headers=None, timeout=None):
        seen.append({"url": url, "params": params, "headers": dict(headers or {})})
        code = replies.pop(0) if len(replies) > 1 else replies[0]
        return httpx.Response(code, json=body if code == 200 else {"detail": "no"},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    return seen


def _send(adapter):
    adapter.db = None
    return adapter.send_email("a@b.com", "hi", "body")


def test_dark_by_default_sends_exactly_as_today(env, mints):
    sent = _posts(env, 202)
    assert _send(WindyMailAdapter())["status"] == "sent"
    assert mints == []
    assert sent[0]["headers"] == {"Authorization": "Bearer LONG-LEGACY-EPT"}


def test_flag_on_send_carries_ept_agent_and_a_proof(env, mints):
    env.setenv(mail_channel.ENV_FLAG, "1")
    sent = _posts(env, 202)
    assert _send(WindyMailAdapter())["status"] == "sent"
    assert mints == [("windy-mail", "POST", f"{API}/api/v1/send")]
    assert sent[0]["headers"] == {"Authorization": "Bearer agent-tok-1", "DPoP": "proof-1"}
    assert len(sent) == 1


def test_flag_on_reads_carry_a_proof_too(env, mints):
    env.setenv(mail_channel.ENV_FLAG, "1")
    seen = _gets(env, 200, body={"messages": [{"id": "x"}]})
    assert WindyMailAdapter().check_inbox(unread_only=True) == [{"id": "x"}]
    # The proof signs the URL without its query (Mail checks public_api_base + path).
    assert mints == [("windy-mail", "GET", f"{API}/api/v1/inbox")]
    assert seen[0]["params"] == {"unread": "true"}
    assert seen[0]["headers"]["DPoP"] == "proof-1"
    assert "jmap-tok" not in str(seen[0]["headers"])


def test_flag_on_my_mailbox_carries_a_proof(env, mints):
    from windyfly.tools.mail import my_mailbox

    env.setenv(mail_channel.ENV_FLAG, "1")
    seen = _gets(env, 200, body={"address": "zero@windymail.ai", "status": "active"})
    assert my_mailbox()["address"] == "zero@windymail.ai"
    assert mints == [("windy-mail", "GET", f"{API}/api/v1/mailbox")]
    assert seen[0]["headers"]["Authorization"] == "Bearer agent-tok-1"


def test_long_ept_absent_still_sends_on_mode_b(env, mints):
    """Mail goes on working after the 365-day EPT is gone: passport + agent key are enough."""
    env.setenv(mail_channel.ENV_FLAG, "1")
    env.delenv("ETERNITAS_PASSPORT_TOKEN")
    env.delenv("WINDYMAIL_JMAP_TOKEN")
    sent = _posts(env, 202)
    assert _send(WindyMailAdapter())["status"] == "sent"
    assert sent[0]["headers"]["Authorization"] == "Bearer agent-tok-1"


def test_flag_off_still_needs_a_token(env):
    env.delenv("ETERNITAS_PASSPORT_TOKEN")
    env.delenv("WINDYMAIL_JMAP_TOKEN")
    with pytest.raises(RuntimeError):
        WindyMailAdapter()


def test_a_401_to_ept_agent_retries_once_with_the_old_credential(env, mints):
    env.setenv(mail_channel.ENV_FLAG, "1")
    sent = _posts(env, 401, 202)
    assert _send(WindyMailAdapter())["status"] == "sent"
    assert [s["headers"]["Authorization"] for s in sent] == ["Bearer agent-tok-1", "Bearer LONG-LEGACY-EPT"]
    assert "DPoP" not in sent[1]["headers"]


def test_a_read_401_retries_with_the_jmap_token(env, mints):
    env.setenv(mail_channel.ENV_FLAG, "1")
    seen = _gets(env, 401, 200, body={"messages": []})
    assert WindyMailAdapter().check_inbox() == []
    assert [s["headers"]["Authorization"] for s in seen] == ["Bearer agent-tok-1", "Bearer jmap-tok"]


@pytest.mark.parametrize("code", [403, 429, 500, 502])
def test_other_refusals_are_not_retried(env, mints, code):
    """Past auth the route may have acted (a 5xx may have sent): never send twice."""
    env.setenv(mail_channel.ENV_FLAG, "1")
    sent = _posts(env, code, 202)
    assert _send(WindyMailAdapter())["status"] == "failed"
    assert len(sent) == 1


def test_a_401_with_no_old_credential_is_not_retried(env, mints):
    env.setenv(mail_channel.ENV_FLAG, "1")
    env.delenv("ETERNITAS_PASSPORT_TOKEN")
    env.delenv("WINDYMAIL_JMAP_TOKEN")
    sent = _posts(env, 401, 202)
    assert _send(WindyMailAdapter())["status"] == "failed"
    assert len(sent) == 1


def test_a_failed_mint_falls_back_to_the_old_credential(env, monkeypatch):
    def refuse(aud, method, url):
        raise service_auth.ServiceAuthError("unreachable", "ConnectError")

    monkeypatch.setattr(service_auth, "agent_headers", refuse)
    env.setenv(mail_channel.ENV_FLAG, "1")
    sent = _posts(env, 202)
    assert _send(WindyMailAdapter())["status"] == "sent"
    assert sent == [{"url": f"{API}/api/v1/send", "headers": {"Authorization": "Bearer LONG-LEGACY-EPT"}}]


def test_a_failed_mint_and_no_old_credential_sends_nothing(env, monkeypatch):
    def refuse(aud, method, url):
        raise service_auth.ServiceAuthError("unreachable", "ConnectError")

    monkeypatch.setattr(service_auth, "agent_headers", refuse)
    env.setenv(mail_channel.ENV_FLAG, "1")
    env.delenv("ETERNITAS_PASSPORT_TOKEN")
    env.delenv("WINDYMAIL_JMAP_TOKEN")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("nothing goes out without a credential"))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("nothing goes out without a credential"))
    adapter = WindyMailAdapter()
    result = _send(adapter)
    assert result["status"] == "failed" and "nothing was sent" in result["error"]
    assert adapter.check_inbox() == [] and adapter.last_error == "no mail credential"


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 1"])
def test_only_exactly_1_turns_it_on(env, mints, value):
    env.setenv(mail_channel.ENV_FLAG, value)
    _posts(env, 202)
    _send(WindyMailAdapter())
    assert mints == []
