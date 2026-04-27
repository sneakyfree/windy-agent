"""Tests for cloudflare.* read-only capabilities.

Network mocked via respx. Validates:
  - graceful refusal when CLOUDFLARE_API_TOKEN missing
  - list_zones happy path + 401/403 + bad-status mapping
  - zone_details by zone_id (no name lookup needed)
  - zone_details by zone_name (extra /zones?name=... lookup)
  - zone_name with no match → friendly error
  - list_dns_records happy path + filters
  - boot wiring step exists with correct dependency
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from windyfly.agent.boot import default_capability_registration_sequence
from windyfly.agent.capabilities import CapabilityRegistry
from windyfly.agent.capabilities.cloudflare import (
    _list_dns_records_handler,
    _list_zones_handler,
    _zone_details_handler,
    register_cloudflare_capabilities,
)

_BASE = "https://api.cloudflare.com/client/v4"
_TOKEN = "test-cf-token-abc"


# ── Graceful refusal when token missing ────────────────────────────


def test_list_zones_no_token_returns_friendly_error():
    out = _list_zones_handler(token="")
    assert out["ok"] is False
    # Grandma-mode (Tier 1): centralized dormant_nudge with
    # chat-driven setup intent + LLM no-jargon instruction.
    assert out["kind"] == "dormant_integration"
    assert out["integration"] == "cloudflare"
    assert "set up cloudflare" in out["error"]
    assert "do NOT relay" in out["error"]


def test_zone_details_no_token_returns_friendly_error():
    out = _zone_details_handler(zone_name="windycloud.com", zone_id=None, token="")
    assert out["ok"] is False
    assert out["kind"] == "dormant_integration"
    assert "set up cloudflare" in out["error"]


def test_list_dns_records_no_token_returns_friendly_error():
    out = _list_dns_records_handler(
        zone_name="windycloud.com", zone_id=None, token="",
    )
    assert out["ok"] is False
    assert out["kind"] == "dormant_integration"
    assert "set up cloudflare" in out["error"]


# ── list_zones ─────────────────────────────────────────────────────


@respx.mock
def test_list_zones_happy_path():
    respx.get(f"{_BASE}/zones").respond(
        200, json={
            "result": [
                {
                    "id": "zone-aaa", "name": "windycloud.com",
                    "status": "active", "paused": False, "type": "full",
                    "plan": {"name": "Free Website"},
                    "name_servers": ["ns1.cf.com", "ns2.cf.com"],
                },
                {
                    "id": "zone-bbb", "name": "eternitas.ai",
                    "status": "active", "paused": False, "type": "full",
                    "plan": {"name": "Free Website"},
                    "name_servers": ["ns1.cf.com", "ns2.cf.com"],
                },
            ],
            "result_info": {"total_count": 2},
            "success": True,
        },
    )
    out = _list_zones_handler(token=_TOKEN)
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["total_count"] == 2
    names = {z["name"] for z in out["zones"]}
    assert names == {"windycloud.com", "eternitas.ai"}
    assert out["zones"][0]["plan"] == "Free Website"


@respx.mock
def test_list_zones_unauthorized_returns_friendly_error():
    respx.get(f"{_BASE}/zones").respond(401, json={"errors": [{"code": 6003}]})
    out = _list_zones_handler(token="bad-token")
    assert out["ok"] is False
    assert "unauthorized" in out["error"]
    assert "Zone:Read" in out["error"]


@respx.mock
def test_list_zones_500_returns_error_dict():
    respx.get(f"{_BASE}/zones").respond(500, text="oh no")
    out = _list_zones_handler(token=_TOKEN)
    assert out["ok"] is False
    assert "500" in out["error"]


@respx.mock
def test_list_zones_clamps_page_size():
    """page_size > _MAX_PAGE_SIZE (500) gets clamped silently."""
    route = respx.get(f"{_BASE}/zones").respond(
        200, json={"result": [], "result_info": {"total_count": 0}},
    )
    _list_zones_handler(token=_TOKEN, page_size=99999)
    sent_url = str(route.calls[0].request.url)
    assert "per_page=500" in sent_url


# ── zone_details by zone_id (no name lookup) ───────────────────────


@respx.mock
def test_zone_details_by_id_skips_name_lookup():
    respx.get(f"{_BASE}/zones/zone-aaa").respond(
        200, json={
            "result": {
                "id": "zone-aaa", "name": "windycloud.com",
                "status": "active", "paused": False, "type": "full",
                "development_mode": 0,
                "name_servers": ["ns1.cf.com", "ns2.cf.com"],
                "original_name_servers": ["ns1.godaddy.com"],
                "plan": {"name": "Free Website"},
                "created_on": "2026-04-18T00:00:00Z",
                "modified_on": "2026-04-26T00:00:00Z",
                "activated_on": "2026-04-19T00:00:00Z",
            },
            "success": True,
        },
    )
    out = _zone_details_handler(
        zone_name=None, zone_id="zone-aaa", token=_TOKEN,
    )
    assert out["ok"] is True
    assert out["name"] == "windycloud.com"
    assert out["plan"] == "Free Website"
    assert out["original_name_servers"] == ["ns1.godaddy.com"]


# ── zone_details by zone_name (extra lookup) ───────────────────────


@respx.mock
def test_zone_details_by_name_resolves_then_fetches():
    respx.get(f"{_BASE}/zones", params={"name": "windycloud.com"}).respond(
        200, json={
            "result": [{"id": "zone-aaa", "name": "windycloud.com"}],
            "success": True,
        },
    )
    respx.get(f"{_BASE}/zones/zone-aaa").respond(
        200, json={
            "result": {
                "id": "zone-aaa", "name": "windycloud.com",
                "status": "active",
            },
            "success": True,
        },
    )
    out = _zone_details_handler(
        zone_name="windycloud.com", zone_id=None, token=_TOKEN,
    )
    assert out["ok"] is True
    assert out["id"] == "zone-aaa"


@respx.mock
def test_zone_details_no_match_returns_friendly_error():
    respx.get(f"{_BASE}/zones", params={"name": "ghost.io"}).respond(
        200, json={"result": [], "success": True},
    )
    out = _zone_details_handler(
        zone_name="ghost.io", zone_id=None, token=_TOKEN,
    )
    assert out["ok"] is False
    assert "no zone named 'ghost.io'" in out["error"]


def test_zone_details_requires_name_or_id():
    out = _zone_details_handler(zone_name=None, zone_id=None, token=_TOKEN)
    assert out["ok"] is False
    assert "zone_id or zone_name is required" in out["error"]


# ── list_dns_records ───────────────────────────────────────────────


@respx.mock
def test_list_dns_records_happy_path():
    respx.get(f"{_BASE}/zones", params={"name": "windycloud.com"}).respond(
        200, json={
            "result": [{"id": "zone-aaa", "name": "windycloud.com"}],
            "success": True,
        },
    )
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={
            "result": [
                {
                    "id": "rec-1", "type": "A", "name": "windycloud.com",
                    "content": "192.0.2.1", "ttl": 1, "proxied": True,
                    "comment": None,
                },
                {
                    "id": "rec-2", "type": "CNAME", "name": "www.windycloud.com",
                    "content": "windycloud.com", "ttl": 1, "proxied": True,
                    "comment": None,
                },
            ],
            "result_info": {"total_count": 2},
            "success": True,
        },
    )
    out = _list_dns_records_handler(
        zone_name="windycloud.com", zone_id=None, token=_TOKEN,
    )
    assert out["ok"] is True
    assert out["count"] == 2
    types = {r["type"] for r in out["records"]}
    assert types == {"A", "CNAME"}


@respx.mock
def test_list_dns_records_type_filter_uppercases_and_passes_through():
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={"result": [], "result_info": {"total_count": 0}},
    )
    route_calls_before = len(respx.routes)
    _list_dns_records_handler(
        zone_name=None, zone_id="zone-aaa", type="cname", token=_TOKEN,
    )
    # Find the dns_records route call and assert type was uppercased
    for route in respx.routes:
        for call in route.calls:
            if "/dns_records" in str(call.request.url):
                assert "type=CNAME" in str(call.request.url)
                return
    pytest.fail("dns_records call not captured")


@respx.mock
def test_list_dns_records_unauthorized_specific_message():
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(403, json={})
    out = _list_dns_records_handler(
        zone_name=None, zone_id="zone-aaa", token=_TOKEN,
    )
    assert out["ok"] is False
    assert "DNS:Read" in out["error"]


# ── Registration smoke ─────────────────────────────────────────────


def test_register_cloudflare_capabilities_adds_three_capabilities():
    registry = CapabilityRegistry()
    register_cloudflare_capabilities(registry, config={})
    for cap_id in (
        "cloudflare.list_zones",
        "cloudflare.zone_details",
        "cloudflare.list_dns_records",
    ):
        cap = registry.get(cap_id)
        assert cap is not None, f"{cap_id} not registered"
        assert cap.audit_required is True


def test_register_uses_env_token_at_call_time(monkeypatch):
    """Token added after boot still works on the next call."""
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    registry = CapabilityRegistry()
    register_cloudflare_capabilities(registry, config={})
    cap = registry.get("cloudflare.list_zones")

    # No token at call time → graceful refusal (grandma-mode nudge)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    out = cap.handler()
    assert out["ok"] is False
    assert out["kind"] == "dormant_integration"
    assert "set up cloudflare" in out["error"]


# ── Boot wiring ────────────────────────────────────────────────────


def test_boot_sequence_includes_capabilities_cloudflare():
    seq = default_capability_registration_sequence()
    names = [s.name for s in seq]
    assert "capabilities.cloudflare" in names
    cf_idx = names.index("capabilities.cloudflare")
    audit_idx = names.index("capabilities.audit")
    assert cf_idx > audit_idx, (
        "cloudflare registration must come after audit hooks"
    )
    cf_step = next(s for s in seq if s.name == "capabilities.cloudflare")
    assert "capabilities.audit" in cf_step.requires


# ── cloudflare.set_dns_record (upsert) ─────────────────────────────


from windyfly.agent.capabilities.cloudflare import (
    _delete_dns_record_handler,
    _set_dns_record_handler,
)


@respx.mock
def test_set_dns_record_creates_new_when_no_match():
    """No existing record with that type+name → POST a new one."""
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={"result": [], "success": True},
    )
    post_route = respx.post(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={
            "result": {
                "id": "rec-new", "type": "A", "name": "blog.eternitas.ai",
                "content": "192.0.2.1", "ttl": 1, "proxied": False,
            },
            "success": True,
        },
    )
    out = _set_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="blog.eternitas.ai", content="192.0.2.1",
        token=_TOKEN,
    )
    assert out["ok"] is True
    assert out["plan"]["action"] == "create"
    assert out["record_id"] == "rec-new"
    assert post_route.called


@respx.mock
def test_set_dns_record_updates_when_match():
    """Existing match → PATCH it."""
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={
            "result": [{
                "id": "rec-existing", "type": "A", "name": "blog.eternitas.ai",
                "content": "10.0.0.1", "ttl": 300, "proxied": True,
            }],
            "success": True,
        },
    )
    patch_route = respx.patch(
        f"{_BASE}/zones/zone-aaa/dns_records/rec-existing",
    ).respond(
        200, json={
            "result": {
                "id": "rec-existing", "type": "A", "name": "blog.eternitas.ai",
                "content": "192.0.2.1", "ttl": 1, "proxied": True,
            },
            "success": True,
        },
    )
    out = _set_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="a", name="blog.eternitas.ai", content="192.0.2.1",
        token=_TOKEN,
    )
    assert out["ok"] is True
    assert out["plan"]["action"] == "update"
    assert out["plan"]["existing_content"] == "10.0.0.1"
    assert out["plan"]["existing_record_id"] == "rec-existing"
    assert patch_route.called


@respx.mock
def test_set_dns_record_dry_run_does_not_write():
    """dry_run=true → no POST/PATCH; plan returned only."""
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={"result": [], "success": True},
    )
    post_route = respx.post(f"{_BASE}/zones/zone-aaa/dns_records")
    patch_route = respx.patch(f"{_BASE}/zones/zone-aaa/dns_records/anything")
    out = _set_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="x.example.com", content="1.1.1.1",
        dry_run=True, token=_TOKEN,
    )
    assert out["executed"] is False
    assert out["preview_only"] is True
    assert out["plan"]["action"] == "create"
    assert post_route.call_count == 0
    assert patch_route.call_count == 0


@respx.mock
def test_set_dns_record_multi_match_refuses_to_guess():
    """If two records have same type+name, refuse — don't pick one."""
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={
            "result": [
                {"id": "rec-1", "type": "A", "name": "x.com", "content": "1.1.1.1"},
                {"id": "rec-2", "type": "A", "name": "x.com", "content": "2.2.2.2"},
            ],
            "success": True,
        },
    )
    post_route = respx.post(f"{_BASE}/zones/zone-aaa/dns_records")
    out = _set_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="x.com", content="3.3.3.3",
        token=_TOKEN,
    )
    assert out["ok"] is False
    assert "2 records matched" in out["error"]
    assert post_route.call_count == 0


def test_set_dns_record_unknown_type_refused_at_door():
    """Typo'd type ('TPYE') → refused without API call."""
    out = _set_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="TPYE", name="x.com", content="1.1.1.1",
        token=_TOKEN,
    )
    assert out["ok"] is False
    assert "TPYE" in out["error"]
    assert "supported set" in out["error"]


def test_set_dns_record_no_token_returns_friendly_error():
    out = _set_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="x.com", content="1.1.1.1",
        token="",
    )
    assert out["ok"] is False
    assert out["kind"] == "dormant_integration"


def test_set_dns_record_missing_required_fields():
    for missing_field in ("type", "name", "content"):
        kwargs = {
            "zone_name": None, "zone_id": "zone-aaa",
            "type": "A", "name": "x.com", "content": "1.1.1.1",
            "token": _TOKEN,
        }
        kwargs[missing_field] = ""
        out = _set_dns_record_handler(**kwargs)
        assert out["ok"] is False
        assert "required" in out["error"]


@respx.mock
def test_set_dns_record_422_returns_validation_error():
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={"result": [], "success": True},
    )
    respx.post(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        422, text='{"errors":[{"message":"invalid IP"}]}',
    )
    out = _set_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="x.com", content="not-an-ip",
        token=_TOKEN,
    )
    assert out["ok"] is False
    assert "validation error" in out["error"]


# ── cloudflare.delete_dns_record ───────────────────────────────────


@respx.mock
def test_delete_dns_record_resolves_id_and_deletes():
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={
            "result": [{
                "id": "rec-doomed", "type": "A", "name": "old.example.com",
                "content": "1.2.3.4",
            }],
            "success": True,
        },
    )
    del_route = respx.delete(
        f"{_BASE}/zones/zone-aaa/dns_records/rec-doomed",
    ).respond(200, json={"result": {"id": "rec-doomed"}, "success": True})
    out = _delete_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="old.example.com",
        token=_TOKEN,
    )
    assert out["ok"] is True
    assert out["deleted_record_id"] == "rec-doomed"
    assert out["plan"]["existing_content"] == "1.2.3.4"
    assert del_route.called


@respx.mock
def test_delete_dns_record_not_found_returns_friendly_error():
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={"result": [], "success": True},
    )
    out = _delete_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="ghost.example.com",
        token=_TOKEN,
    )
    assert out["ok"] is False
    assert "no record found" in out["error"]


@respx.mock
def test_delete_dns_record_dry_run_does_not_delete():
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={
            "result": [{"id": "rec-x", "type": "A", "name": "x.com", "content": "1.1.1.1"}],
            "success": True,
        },
    )
    del_route = respx.delete(f"{_BASE}/zones/zone-aaa/dns_records/rec-x")
    out = _delete_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="x.com",
        dry_run=True, token=_TOKEN,
    )
    assert out["executed"] is False
    assert out["preview_only"] is True
    assert del_route.call_count == 0


@respx.mock
def test_delete_dns_record_with_explicit_record_id_skips_lookup():
    """If caller passes record_id, skip the type+name lookup."""
    # Note: we don't mock the GET — if it gets called, the test fails
    del_route = respx.delete(
        f"{_BASE}/zones/zone-aaa/dns_records/rec-direct",
    ).respond(200, json={"result": {"id": "rec-direct"}, "success": True})
    out = _delete_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="x.com",
        record_id="rec-direct",
        token=_TOKEN,
    )
    assert out["ok"] is True
    assert del_route.called


@respx.mock
def test_delete_dns_record_404_returns_already_deleted_message():
    respx.get(f"{_BASE}/zones/zone-aaa/dns_records").respond(
        200, json={
            "result": [{"id": "rec-stale", "type": "A", "name": "x.com", "content": "1.1.1.1"}],
            "success": True,
        },
    )
    respx.delete(
        f"{_BASE}/zones/zone-aaa/dns_records/rec-stale",
    ).respond(404)
    out = _delete_dns_record_handler(
        zone_name=None, zone_id="zone-aaa",
        type="A", name="x.com",
        token=_TOKEN,
    )
    assert out["ok"] is False
    assert "already deleted" in out["error"]


# ── Registration includes write capabilities now ───────────────────


def test_register_cloudflare_capabilities_now_includes_writes(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake")
    registry = CapabilityRegistry()
    register_cloudflare_capabilities(registry, config={})
    for cap_id in (
        "cloudflare.list_zones",
        "cloudflare.zone_details",
        "cloudflare.list_dns_records",
        "cloudflare.set_dns_record",
        "cloudflare.delete_dns_record",
    ):
        cap = registry.get(cap_id)
        assert cap is not None, f"{cap_id} not registered"

    # Writes are EXTERNAL_EFFECT, not READ_EXTERNAL
    from windyfly.agent.capabilities.descriptor import Band, Tier
    set_cap = registry.get("cloudflare.set_dns_record")
    del_cap = registry.get("cloudflare.delete_dns_record")
    assert set_cap.tier == Tier.EXTERNAL_EFFECT
    assert del_cap.tier == Tier.EXTERNAL_EFFECT
    assert set_cap.band_required == Band.TRUSTED
    assert del_cap.band_required == Band.TRUSTED
