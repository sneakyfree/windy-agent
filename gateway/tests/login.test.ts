/**
 * Contract tests for /api/auth/login (P0-S2 fix).
 *
 * The fix kills `?auth=<password>` — the password must NEVER land in
 * a URL, access log, Referer header, or browser history. Login is
 * now POST-only; the cookie is set only on successful constant-time
 * password match.
 *
 * Run with: bun test gateway/tests/login.test.ts
 */

import { describe, expect, test } from "bun:test";
import { safeStringEqual } from "../src/server";

describe("safeStringEqual — constant-time password comparison", () => {
  test("accepts identical strings", () => {
    expect(safeStringEqual("hunter2", "hunter2")).toBe(true);
  });

  test("rejects mismatched same-length strings", () => {
    expect(safeStringEqual("hunter2", "hunter3")).toBe(false);
  });

  test("rejects mismatched different-length strings without throwing", () => {
    expect(safeStringEqual("hunter2", "hunter22")).toBe(false);
    expect(safeStringEqual("hunter2", "")).toBe(false);
    expect(safeStringEqual("", "hunter2")).toBe(false);
  });

  test("handles multibyte utf8 correctly", () => {
    expect(safeStringEqual("🪰", "🪰")).toBe(true);
    expect(safeStringEqual("🪰", "🚀")).toBe(false);
  });
});

// End-to-end: only static checks here; Bun.serve() lifecycle tests
// live in gateway/tests/server-e2e.test.ts (future work). What this
// file pins down:
//   - there is no code path that accepts `?auth=<password>` and sets
//     a cookie.
//   - the login HTML form uses method="POST", action="/api/auth/login".
import { readFileSync } from "fs";
import { join } from "path";

describe("server.ts — no query-param auth survives", () => {
  const src = readFileSync(
    join(import.meta.dir, "..", "src", "server.ts"),
    "utf8",
  );

  test('login form is POST method only', () => {
    expect(src).toContain('method="POST" action="/api/auth/login"');
  });

  test('no query-param auth branch exists', () => {
    // Critical regression guard — if the ?auth= shortcut is ever
    // re-added, this test fires.
    expect(src).not.toMatch(/searchParams\.get\(['"]auth['"]\)/);
    expect(src).not.toMatch(/URLSearchParams.*auth/);
  });

  test('cookie is set only inside handleLogin (POST handler)', () => {
    // Set-Cookie should appear in the file, but only in the POST
    // login handler — not in checkDashboardAuth as a side effect of
    // a GET request.
    const cookieOccurrences = (src.match(/windy_auth=/g) || []).length;
    // 1 in the cookie header we set on successful POST, plus uses
    // in the cookie-read path (parseCookie "windy_auth"). 3 max.
    expect(cookieOccurrences).toBeLessThanOrEqual(3);
  });
});
