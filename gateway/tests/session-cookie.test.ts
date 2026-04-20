/**
 * Wave 14 — P1 fix: opaque dashboard session cookie.
 *
 * Pre-fix, the Set-Cookie value was DASHBOARD_PASSWORD verbatim.
 * Any cookie theft = master secret exfil; rotating the password was
 * the only revocation. Post-fix, the cookie is a random 256-bit
 * token looked up in a server-side store with a 24 h TTL.
 *
 * Tests pin:
 *   1. createDashboardSession returns opaque tokens, not the password.
 *   2. Tokens are not guessable (sufficient entropy, no patterns).
 *   3. isValidDashboardSession accepts freshly-minted tokens.
 *   4. Expired tokens fail validation AND are evicted.
 *   5. revokeDashboardSession invalidates a single session.
 *   6. TTL respected (custom short TTL in tests).
 *   7. server.ts no longer writes the password into the cookie.
 */

import { afterEach, describe, expect, test } from "bun:test";
import {
  _clearDashboardSessions,
  _dashboardSessionCount,
  createDashboardSession,
  isValidDashboardSession,
  revokeDashboardSession,
} from "../src/server";

afterEach(() => _clearDashboardSessions());

describe("createDashboardSession", () => {
  test("mints a random base64url token at least 32 bytes long", () => {
    const t = createDashboardSession();
    // 32 raw bytes → base64url encodes to ~43 chars (no padding).
    expect(t.length).toBeGreaterThanOrEqual(42);
    // base64url charset: A-Z a-z 0-9 _ -
    expect(t).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  test("mints unique tokens across calls", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 64; i++) seen.add(createDashboardSession());
    expect(seen.size).toBe(64);
  });

  test("stored token count increments by one per mint", () => {
    expect(_dashboardSessionCount()).toBe(0);
    createDashboardSession();
    expect(_dashboardSessionCount()).toBe(1);
    createDashboardSession();
    expect(_dashboardSessionCount()).toBe(2);
  });
});

describe("isValidDashboardSession", () => {
  test("accepts a freshly minted token", () => {
    const t = createDashboardSession();
    expect(isValidDashboardSession(t)).toBe(true);
  });

  test("rejects an unknown token", () => {
    expect(isValidDashboardSession("not-a-real-token")).toBe(false);
  });

  test("rejects the empty string", () => {
    expect(isValidDashboardSession("")).toBe(false);
  });

  test("rejects an expired token and evicts it from the store", () => {
    // Mint a token with 1 ms TTL (absurdly short).
    const t = createDashboardSession(1);
    // Any non-zero wait — use a loop so we don't rely on fake timers.
    const futureNow = Date.now() + 5;
    expect(isValidDashboardSession(t, futureNow)).toBe(false);
    // After the lazy eviction, the store should no longer hold it.
    expect(_dashboardSessionCount()).toBe(0);
    // And a second check still returns false (no reinstatement).
    expect(isValidDashboardSession(t, futureNow)).toBe(false);
  });
});

describe("revokeDashboardSession", () => {
  test("invalidates a live session and returns true the first time", () => {
    const t = createDashboardSession();
    expect(isValidDashboardSession(t)).toBe(true);

    expect(revokeDashboardSession(t)).toBe(true);
    expect(isValidDashboardSession(t)).toBe(false);
    // Second revoke is a no-op.
    expect(revokeDashboardSession(t)).toBe(false);
  });

  test("revoking one session does not affect peers", () => {
    const a = createDashboardSession();
    const b = createDashboardSession();
    revokeDashboardSession(a);
    expect(isValidDashboardSession(a)).toBe(false);
    expect(isValidDashboardSession(b)).toBe(true);
  });
});

describe("server.ts — regression guards", () => {
  const src = Bun.file(import.meta.dir + "/../src/server.ts");

  test("Set-Cookie no longer embeds DASHBOARD_PASSWORD verbatim", async () => {
    const text = await src.text();
    // The old bug: `windy_auth=${DASHBOARD_PASSWORD}; Path=/; ...`
    expect(text).not.toMatch(/windy_auth=\$\{DASHBOARD_PASSWORD\}/);
    // The fix: cookie value comes from createDashboardSession().
    expect(text).toContain("const sessionToken = createDashboardSession()");
    expect(text).toMatch(/windy_auth=\$\{sessionToken\}/);
  });

  test("isDashboardAuthValid validates the cookie via isValidDashboardSession", async () => {
    const text = await src.text();
    // Pin that the cookie branch uses the session store, not
    // safeStringEqual against DASHBOARD_PASSWORD.
    const cookieCheckRegion = text.substring(
      text.indexOf("cookieVal = parseCookie"),
      text.indexOf("function checkDashboardAuth"),
    );
    expect(cookieCheckRegion).toContain("isValidDashboardSession(cookieVal)");
    expect(cookieCheckRegion).not.toMatch(
      /safeStringEqual\(cookieVal,\s*DASHBOARD_PASSWORD\)/,
    );
  });
});
