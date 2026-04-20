/**
 * Wave 14 — P0 fix: nginx→Bun loopback trust bypassed dashboard auth.
 *
 * When Bun is behind an nginx reverse proxy, every public request
 * arrives at Bun from the socket peer 127.0.0.1. The pre-fix code
 * called `isLocalhostRequest` → returned true → skipped
 * `checkDashboardAuth` → served the entire route surface unauthed.
 *
 * The fix is `shouldBypassAuthForLocalhost`, which:
 *   (a) never bypasses in production, and
 *   (b) never bypasses if any X-Forwarded-For / X-Real-IP header is
 *       present (proving a proxy was in the path, even in dev).
 *
 * These contract tests pin the decision table.
 */

import { describe, expect, test } from "bun:test";
import { shouldBypassAuthForLocalhost } from "../src/server";

// Bun's actual `Server` type is complex; for a pure decision test we
// only need `requestIP`. A minimal stub keeps the tests readable.
function mockServer(peerAddress: string | null): any {
  return {
    requestIP: (_req: Request) =>
      peerAddress === null ? null : { address: peerAddress, port: 0, family: "IPv4" },
  };
}

function reqWith(headers: Record<string, string> = {}): Request {
  return new Request("http://fly.windyword.ai/", { headers });
}

describe("shouldBypassAuthForLocalhost — production", () => {
  test("refuses bypass even when peer is 127.0.0.1 (nginx→Bun loopback)", () => {
    // This is the exact scenario from the smoke report: nginx
    // forwards a public request to 127.0.0.1:3000 and Bun sees the
    // loopback peer. Pre-fix this returned true. Post-fix: false.
    const req = reqWith({ "X-Forwarded-For": "203.0.113.7" });
    expect(shouldBypassAuthForLocalhost(req, mockServer("127.0.0.1"), "production")).toBe(false);
  });

  test("refuses bypass for a direct loopback client too", () => {
    // Even on an in-VPC direct connect with no proxy header, prod
    // never bypasses — the dashboard is locked down symmetrically.
    const req = reqWith();
    expect(shouldBypassAuthForLocalhost(req, mockServer("127.0.0.1"), "production")).toBe(false);
  });

  test("refuses bypass for public IPs (obvious)", () => {
    expect(shouldBypassAuthForLocalhost(reqWith(), mockServer("8.8.8.8"), "production")).toBe(false);
  });

  test("refuses bypass when peer is unknown", () => {
    expect(shouldBypassAuthForLocalhost(reqWith(), mockServer(null), "production")).toBe(false);
  });
});

describe("shouldBypassAuthForLocalhost — non-production (dev)", () => {
  test("allows bypass for direct loopback with no proxy headers", () => {
    // Developer hitting http://localhost:3000 during dev should not
    // be forced through the password form.
    const req = reqWith();
    expect(shouldBypassAuthForLocalhost(req, mockServer("127.0.0.1"), "dev")).toBe(true);
    expect(shouldBypassAuthForLocalhost(req, mockServer("::1"), "dev")).toBe(true);
  });

  test("refuses bypass when X-Forwarded-For is present (proxy in path)", () => {
    // Even in dev, the moment a proxy forwards the request, the
    // peer-IP signal becomes untrustworthy.
    const req = reqWith({ "X-Forwarded-For": "203.0.113.7" });
    expect(shouldBypassAuthForLocalhost(req, mockServer("127.0.0.1"), "dev")).toBe(false);
  });

  test("refuses bypass when X-Real-IP is present (proxy in path)", () => {
    const req = reqWith({ "X-Real-IP": "203.0.113.7" });
    expect(shouldBypassAuthForLocalhost(req, mockServer("127.0.0.1"), "dev")).toBe(false);
  });

  test("refuses bypass for public peer even in dev", () => {
    expect(shouldBypassAuthForLocalhost(reqWith(), mockServer("203.0.113.7"), "dev")).toBe(false);
  });

  test("ignores forgeable Host header", () => {
    // Attacker-controlled Host is never the basis for trust. The
    // helper reads peer IP from `server.requestIP`, not the header.
    const req = new Request("http://attacker.tld/", {
      headers: { Host: "localhost" },
    });
    expect(shouldBypassAuthForLocalhost(req, mockServer("203.0.113.7"), "dev")).toBe(false);
  });
});

describe("server.ts — regression guard", () => {
  const src = Bun.file(import.meta.dir + "/../src/server.ts");

  test("checkDashboardAuth uses the production-aware helper, not the raw localhost check", async () => {
    const text = await src.text();
    // The fix swaps `isLocalhostRequest` for `shouldBypassAuthForLocalhost`
    // at the auth-gate call site. If someone regresses back to the
    // raw form, this catches it.
    expect(text).toContain("shouldBypassAuthForLocalhost(req, server, WINDYFLY_ENV)");
    const authGateSection = text.split("checkDashboardAuth")[2] ?? "";
    expect(authGateSection).not.toMatch(/if\s*\(\s*isLocalhostRequest\s*\(\s*req\s*,\s*server\s*\)\s*\)\s*return null/);
  });
});
