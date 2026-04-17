/**
 * Contract tests for P1-S5 (empty-password policy), P1-S6
 * (constant-time compare), and the auth-bucket half of P1-O5
 * (rate-limit on failed logins).
 *
 * Covers only the pure helpers — validateDashboardAuthConfig and
 * safeStringEqual. The live-server behaviour (login rate-limit 429s,
 * startup throw) is validated through the regression guards against
 * server.ts in login.test.ts.
 */

import { describe, expect, test } from "bun:test";
import { safeStringEqual, validateDashboardAuthConfig } from "../src/server";

describe("validateDashboardAuthConfig — startup guard", () => {
  test("production + empty password → refuses", () => {
    const r = validateDashboardAuthConfig("", "production");
    expect(r.ok).toBe(false);
    expect(r.message).toContain("required");
  });

  test("production + strong password → accepts silently", () => {
    const r = validateDashboardAuthConfig("a".repeat(24), "production");
    expect(r.ok).toBe(true);
    expect(r.message).toBe("");
  });

  test("dev + empty password → warns but allows", () => {
    const r = validateDashboardAuthConfig("", "dev");
    expect(r.ok).toBe(true);
    expect(r.message).toContain("WARN");
    expect(r.message).toContain("open");
  });

  test("production + short password → warns but allows (does not fail closed on length)", () => {
    const r = validateDashboardAuthConfig("short", "production");
    expect(r.ok).toBe(true);
    expect(r.message).toContain("WARN");
    expect(r.message).toContain("16");
  });
});

describe("safeStringEqual — constant-time compare", () => {
  test("accepts matching strings", () => {
    expect(safeStringEqual("hunter2", "hunter2")).toBe(true);
  });

  test("rejects mismatched same-length", () => {
    expect(safeStringEqual("hunter2", "hunter3")).toBe(false);
  });

  test("rejects different-length without throwing", () => {
    expect(safeStringEqual("a", "ab")).toBe(false);
    expect(safeStringEqual("", "x")).toBe(false);
    expect(safeStringEqual("x", "")).toBe(false);
  });

  test("utf-8 safe", () => {
    expect(safeStringEqual("🪰agent", "🪰agent")).toBe(true);
    expect(safeStringEqual("🪰agent", "🚀agent")).toBe(false);
  });
});

describe("server.ts — regression guards for auth-hardening", () => {
  const src = Bun.file(import.meta.dir + "/../src/server.ts");
  test("no plain-string === on DASHBOARD_PASSWORD", async () => {
    const text = await src.text();
    // The only remaining `=== DASHBOARD_PASSWORD` reference should be
    // inside docstrings / comments. Any live code must use
    // safeStringEqual.
    const live = text
      .split("\n")
      .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
      .join("\n");
    expect(live).not.toContain("=== `Bearer ${DASHBOARD_PASSWORD}`");
    expect(live).not.toMatch(/cookie\.includes\(`windy_auth=\$\{DASHBOARD_PASSWORD\}`\)/);
  });

  test("auth rate-limit bucket is wired", async () => {
    const text = await src.text();
    expect(text).toContain(`isRateLimited(ip, "auth")`);
  });

  test("empty password in production refuses at startup", async () => {
    const text = await src.text();
    expect(text).toContain("validateDashboardAuthConfig");
    expect(text).toMatch(/throw new Error/);
  });
});
