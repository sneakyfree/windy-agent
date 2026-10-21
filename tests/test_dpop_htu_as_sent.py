"""The DPoP proof signs the URL exactly as httpx sends it (Hub, from Windy Cloud's htu fix): percent-encoded path,
dot segments removed, default port dropped, no query or fragment."""
import base64
import json

import httpx
import pytest

from windyfly.eternitas import agent_keys as ak
from tests.test_agent_token_mode_b import registered  # noqa: F401  (fixture)
from tests.test_agent_keys import env  # noqa: F401  (fixture)


def _htu(proof: str) -> str:
    s = proof.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))["htu"]


@pytest.mark.parametrize("url,expected", [
    ("https://cloud.windycloud.com/api/v1/archive/retrieve/windy_fly/my backup ü.enc?x=1#f",
     "https://cloud.windycloud.com/api/v1/archive/retrieve/windy_fly/my%20backup%20%C3%BC.enc"),
    ("https://x.example/a/./b/../c", "https://x.example/a/c"),
    ("https://x.example:443/p", "https://x.example/p"),
    ("https://x.example:8443/p", "https://x.example:8443/p"),
    ("https://x.example/already%20encoded", "https://x.example/already%20encoded"),
])
def test_htu_is_the_url_as_sent(url, expected):
    assert ak.htu(url) == expected


def test_the_signed_htu_matches_the_bytes_httpx_puts_on_the_wire(registered):  # noqa: F811
    url = "https://cloud.windycloud.com/api/v1/archive/retrieve/windy_fly/a b ü.enc"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.raw_path.split(b"?", 1)[0].decode()
        seen["proof"] = request.headers["dpop"]
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        c.get(url, params={"x": "1"}, headers={"DPoP": ak.service_dpop("GET", url)})
    assert _htu(seen["proof"]) == "https://cloud.windycloud.com" + seen["path"]
    assert "%20" in seen["path"] and "%C3%BC" in seen["path"]
