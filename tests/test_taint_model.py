"""[I2 taint model] Once a turn reads untrusted external content (email/web/
news/voicemail), external-effect tools are refused and the band is capped so
TRUSTED+ capabilities (shell/ssh/fleet/email.send/destructive writes) are denied
for the rest of the turn. Reading/searching (USER and below) stays available.

This closes the injected-content → exfiltration/RCE path: an attacker who gets
an instruction into the agent's context (via an email body it reads, or a web
page it fetches) can no longer drive the agent to send/upload/run.
"""

from windyfly.agent.capabilities import (
    Band,
    Capability,
    CapabilityDenied,
    CapabilityRegistry,
    Tier,
)
from windyfly.agent.loop import (
    TAINT_FORBIDDEN_TOOLS,
    UNTRUSTED_SOURCE_TOOLS,
    _dispatch_tool_call,
    _taint_gate,
)


def test_taint_gate_denies_external_effect_tools_when_tainted():
    for name in [
        "send_email", "send_sms", "send_chat_message", "make_call",
        "upload_to_cloud", "windycode_run", "windycode_write_file",
        "windycode_save_to_git", "windycode_create_project",
    ]:
        deny, _band = _taint_gate(name, True, Band.OWNER)
        assert deny is True, f"{name} must be refused on a tainted turn"


def test_taint_gate_allows_external_effect_when_untainted():
    deny, band = _taint_gate("send_email", False, Band.OWNER)
    assert deny is False and band == Band.OWNER


def test_taint_gate_caps_band_to_user_when_tainted():
    # shell.exec is a capability (not in FORBIDDEN) → not denied outright, but
    # dispatched at a capped band so its TRUSTED requirement fails.
    assert _taint_gate("shell.exec", True, Band.OWNER) == (False, Band.USER)
    assert _taint_gate("shell.exec", True, Band.TRUSTED) == (False, Band.USER)
    # already at/below USER → unchanged
    assert _taint_gate("shell.exec", True, Band.USER) == (False, Band.USER)
    assert _taint_gate("shell.exec", True, Band.SANDBOX) == (False, Band.SANDBOX)


def test_untainted_turn_is_unchanged():
    assert _taint_gate("get_weather", False, Band.OWNER) == (False, Band.OWNER)
    assert _taint_gate("shell.exec", False, Band.OWNER) == (False, Band.OWNER)
    # a read tool on a tainted turn is allowed (just band-capped)
    assert _taint_gate("web_search", True, Band.OWNER) == (False, Band.USER)


def test_constants_cover_the_real_sinks_and_sources():
    for sink in [
        "send_email", "send_sms", "send_chat_message", "make_call",
        "upload_to_cloud", "windycode_run", "windycode_write_file",
        "windycode_save_to_git",
    ]:
        assert sink in TAINT_FORBIDDEN_TOOLS
    for src in ["list_inbox", "web_search", "fetch_url"]:
        assert src in UNTRUSTED_SOURCE_TOOLS
    # read-only tools are NOT sinks (so reading/summarizing stays possible)
    for safe in ["get_weather", "web_search", "list_inbox", "fetch_url"]:
        assert safe not in TAINT_FORBIDDEN_TOOLS


def test_integration_user_cap_denies_a_trusted_capability_via_dispatch():
    """End-to-end: a tainted OWNER turn caps the band to USER, and the real
    dispatch path (invoke_sync's band gate) then refuses a TRUSTED capability —
    while the same call on an untainted turn runs."""
    reg = CapabilityRegistry()
    reg.register(Capability(
        id="danger.act",
        description="a TRUSTED-gated action",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **_k: "ACTED",
        tier=Tier.PURE_COMPUTE,          # no sandbox — handler runs directly
        band_required=Band.TRUSTED,      # explicit override
    ))

    # untainted OWNER turn → runs
    _deny, band_ok = _taint_gate("danger.act", False, Band.OWNER)
    out_ok = _dispatch_tool_call("danger.act", {}, None, reg, band_ok, CapabilityDenied)
    assert "ACTED" in out_ok

    # tainted OWNER turn → capped to USER → denied at the band gate
    _deny2, band_capped = _taint_gate("danger.act", True, Band.OWNER)
    assert band_capped == Band.USER
    out_denied = _dispatch_tool_call("danger.act", {}, None, reg, band_capped, CapabilityDenied)
    assert "capability_denied" in out_denied
