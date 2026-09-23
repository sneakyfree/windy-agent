/**
 * Hub JWT verification (SSO #13) — RS256 only, JWKS with kid rotation,
 * issuer allowlist, exp/nbf skew, and the aud flag (default OFF).
 * Plus the owner gate that decides who may open the dashboard.
 */
import { afterEach, describe, expect, test } from "bun:test";
import { identityOf, isOwner } from "../src/hub-auth";
import {
  NOW_MS, humanClaims, jwksServer, makeKey, makeVerifier, signHs256, signRs256,
} from "./helpers/hub-keys";

const k1 = makeKey("kid-1");
const k2 = makeKey("kid-2");
const nowS = Math.floor(NOW_MS / 1000);

describe("HubJwtVerifier", () => {
  test("accepts a valid RS256 hub token", async () => {
    const v = makeVerifier(jwksServer([k1]));
    const r = await v.verify(signRs256(k1, humanClaims()));
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.claims.windy_identity_id).toBe("owner-identity-1");
  });

  test("accepts both hub issuers", async () => {
    const v = makeVerifier(jwksServer([k1]));
    for (const iss of ["windy-identity", "https://account.windyword.ai"]) {
      expect((await v.verify(signRs256(k1, humanClaims({ iss })))).ok).toBe(true);
    }
  });

  test("rejects HS256 (even with a plausible secret) and alg none", async () => {
    const v = makeVerifier(jwksServer([k1]));
    const hs = await v.verify(signHs256("secret", humanClaims(), "kid-1"));
    expect(hs).toEqual({ ok: false, reason: "bad_alg" });

    const head = Buffer.from(JSON.stringify({ alg: "none", kid: "kid-1" })).toString("base64url");
    const body = Buffer.from(JSON.stringify(humanClaims())).toString("base64url");
    expect(await v.verify(`${head}.${body}.x`)).toEqual({ ok: false, reason: "bad_alg" });
  });

  test("rejects a token signed by a key the hub never published", async () => {
    const rogue = makeKey("kid-1"); // same kid, different key
    const v = makeVerifier(jwksServer([k1]));
    expect(await v.verify(signRs256(rogue, humanClaims()))).toEqual({ ok: false, reason: "bad_signature" });
  });

  test("rejects a tampered payload", async () => {
    const v = makeVerifier(jwksServer([k1]));
    const [h, , s] = signRs256(k1, humanClaims()).split(".");
    const forged = Buffer.from(JSON.stringify(humanClaims({ windy_identity_id: "attacker" }))).toString("base64url");
    expect(await v.verify(`${h}.${forged}.${s}`)).toEqual({ ok: false, reason: "bad_signature" });
  });

  test("rejects an unlisted issuer", async () => {
    const v = makeVerifier(jwksServer([k1]));
    expect(await v.verify(signRs256(k1, humanClaims({ iss: "https://evil.example" }))))
      .toEqual({ ok: false, reason: "bad_issuer" });
  });

  test("rejects expired tokens beyond 60s skew, tolerates within it", async () => {
    const v = makeVerifier(jwksServer([k1]));
    expect((await v.verify(signRs256(k1, humanClaims({ exp: nowS - 30 })))).ok).toBe(true);
    expect(await v.verify(signRs256(k1, humanClaims({ exp: nowS - 61 }))))
      .toEqual({ ok: false, reason: "expired" });
    expect(await v.verify(signRs256(k1, humanClaims({ exp: undefined }))))
      .toEqual({ ok: false, reason: "expired" });
  });

  test("rejects not-yet-valid tokens beyond skew", async () => {
    const v = makeVerifier(jwksServer([k1]));
    expect(await v.verify(signRs256(k1, humanClaims({ nbf: nowS + 120 }))))
      .toEqual({ ok: false, reason: "not_yet_valid" });
  });

  test("unknown kid triggers exactly one JWKS refetch (key rotation)", async () => {
    const jwks = jwksServer([k1]);
    const v = makeVerifier(jwks);
    expect((await v.verify(signRs256(k1, humanClaims()))).ok).toBe(true);
    expect(jwks.fetches).toBe(1);

    jwks.setKeys([k1, k2]); // hub rotates in kid-2
    expect((await v.verify(signRs256(k2, humanClaims()))).ok).toBe(true);
    expect(jwks.fetches).toBe(2);
  });

  test("junk kids can't hammer the JWKS (refetch is rate-limited)", async () => {
    const jwks = jwksServer([k1]);
    const v = makeVerifier(jwks);
    await v.verify(signRs256(k1, humanClaims()));
    const junk = makeKey("junk");
    for (let i = 0; i < 5; i++) {
      expect(await v.verify(signRs256(junk, humanClaims()))).toEqual({ ok: false, reason: "unknown_kid" });
    }
    expect(jwks.fetches).toBe(2); // initial + one unknown-kid refetch
  });

  test("JWKS unreachable → jwks_unavailable, not a crash", async () => {
    const v = makeVerifier({
      setKeys() {}, fetches: 0,
      fetchImpl: async () => { throw new Error("down"); },
    } as any);
    expect(await v.verify(signRs256(k1, humanClaims()))).toEqual({ ok: false, reason: "jwks_unavailable" });
  });

  test("malformed tokens", async () => {
    const v = makeVerifier(jwksServer([k1]));
    for (const t of ["", "a.b", "a.b.c.d", "!!.!!.!!"]) {
      expect((await v.verify(t)).ok).toBe(false);
    }
  });

  describe("aud flag (WINDY_FLY_ENFORCE_AUDIENCE)", () => {
    test("OFF (default): missing aud accepted, would-be rejection logged", async () => {
      const logs: string[] = [];
      const v = makeVerifier(jwksServer([k1]), { log: (m) => logs.push(m) });
      expect((await v.verify(signRs256(k1, humanClaims()))).ok).toBe(true);
      expect(logs.join("\n")).toContain("would reject");
    });

    test("ON: aud array containing windy_fly accepted", async () => {
      const v = makeVerifier(jwksServer([k1]), { enforceAudience: true });
      const r = await v.verify(signRs256(k1, humanClaims({ aud: ["windy_pro", "windy_fly"] })));
      expect(r.ok).toBe(true);
    });

    test("ON: missing aud, bare-string aud, or aud without windy_fly rejected", async () => {
      const v = makeVerifier(jwksServer([k1]), { enforceAudience: true });
      for (const aud of [undefined, "windy_fly", ["windy_connect"]]) {
        expect(await v.verify(signRs256(k1, humanClaims({ aud }))))
          .toEqual({ ok: false, reason: "bad_audience" });
      }
    });
  });
});

describe("owner gate", () => {
  const saved = { owner: process.env.WINDY_IDENTITY_ID, claim: process.env.HUB_OWNER_CLAIM };
  afterEach(() => {
    process.env.WINDY_IDENTITY_ID = saved.owner;
    process.env.HUB_OWNER_CLAIM = saved.claim;
    if (saved.owner === undefined) delete process.env.WINDY_IDENTITY_ID;
    if (saved.claim === undefined) delete process.env.HUB_OWNER_CLAIM;
  });

  test("identity comes from windy_identity_id (not sub) by default", () => {
    expect(identityOf(humanClaims())).toBe("owner-identity-1");
  });

  test("NO fallback to sub: a token without windy_identity_id identifies nobody", () => {
    expect(identityOf(humanClaims({ windy_identity_id: undefined }))).toBe("");
    const c = humanClaims({ windy_identity_id: undefined, sub: "owner-identity-1" });
    expect(isOwner(c, "owner-identity-1")).toBe(false);
  });

  test("HUB_OWNER_CLAIM selects a different claim", () => {
    process.env.HUB_OWNER_CLAIM = "windyIdentityId";
    expect(identityOf(humanClaims({ windyIdentityId: "camel-id" }))).toBe("camel-id");
  });

  test("owner matches WINDY_IDENTITY_ID", () => {
    expect(isOwner(humanClaims(), "owner-identity-1")).toBe(true);
    expect(isOwner(humanClaims({ windy_identity_id: "someone-else" }), "owner-identity-1")).toBe(false);
  });

  test("no owner configured → nobody is the owner", () => {
    expect(isOwner(humanClaims(), "")).toBe(false);
  });

  test("agent/bot tokens never pass, even with a matching identity", () => {
    expect(isOwner(humanClaims({ type: "agent" }), "owner-identity-1")).toBe(false);
  });

  test("never matches on email: owner's email with a different identity does not pass", () => {
    const c = humanClaims({ email: "owner@example.com", windy_identity_id: "impostor" });
    expect(isOwner(c, "owner-identity-1")).toBe(false);
    expect(isOwner(c, "owner@example.com")).toBe(false);
  });

  describe("email_verified (HUB_REQUIRE_EMAIL_VERIFIED)", () => {
    const savedFlag = process.env.HUB_REQUIRE_EMAIL_VERIFIED;
    afterEach(() => {
      if (savedFlag === undefined) delete process.env.HUB_REQUIRE_EMAIL_VERIFIED;
      else process.env.HUB_REQUIRE_EMAIL_VERIFIED = savedFlag;
    });

    test("default ON: email_verified=false is refused, true accepted", () => {
      delete process.env.HUB_REQUIRE_EMAIL_VERIFIED;
      expect(isOwner(humanClaims({ email_verified: false }), "owner-identity-1")).toBe(false);
      expect(isOwner(humanClaims({ email_verified: true }), "owner-identity-1")).toBe(true);
    });

    test("claim absent → still accepted (until the hub confirms emission)", () => {
      delete process.env.HUB_REQUIRE_EMAIL_VERIFIED;
      expect(isOwner(humanClaims(), "owner-identity-1")).toBe(true);
    });

    test("flag OFF → email_verified=false tolerated", () => {
      process.env.HUB_REQUIRE_EMAIL_VERIFIED = "0";
      expect(isOwner(humanClaims({ email_verified: false }), "owner-identity-1")).toBe(true);
    });
  });

  test("a token whose sub equals the owner id but whose identity claim differs does not pass", () => {
    const c = humanClaims({ sub: "owner-identity-1", windy_identity_id: "other" });
    expect(isOwner(c, "owner-identity-1")).toBe(false);
  });
});
