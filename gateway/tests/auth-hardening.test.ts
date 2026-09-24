/**
 * Contract tests for the dashboard startup guard (owner identity
 * required in production — SSO #13 replaced the shared password), P1-S6
 * (constant-time compare), and the auth-bucket half of P1-O5.
 *
 * Covers only the pure helpers — validateDashboardAuthConfig and
 * safeStringEqual. Hub sign-in behaviour lives in hub-login.test.ts.
 */

import { describe, expect, test } from "bun:test";
import { safeStringEqual, validateDashboardAuthConfig } from "../src/server";

describe("validateDashboardAuthConfig — startup guard", () => {
  test("production + no owner identity → refuses to start", () => {
    const r = validateDashboardAuthConfig("", "production");
    expect(r.ok).toBe(false);
    expect(r.message).toContain("WINDY_IDENTITY_ID");
    expect(r.message).toContain("required");
  });

  test("production + owner identity → accepts silently", () => {
    const r = validateDashboardAuthConfig("00000000-0000-4000-8000-000000000001", "production");
    expect(r.ok).toBe(true);
    expect(r.message).toBe("");
  });

  test("dev + no owner → warns but allows (loopback-only)", () => {
    const r = validateDashboardAuthConfig("", "dev");
    expect(r.ok).toBe(true);
    expect(r.message).toContain("WARN");
    expect(r.message).toContain("loopback");
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
  test("the shared dashboard password is gone from live code", async () => {
    const text = await src.text();
    const live = text
      .split("\n")
      .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
      .join("\n");
    expect(live).not.toContain("DASHBOARD_PASSWORD");
    expect(live).not.toContain("/api/auth/login");
    expect(live).not.toContain('type="password"');
  });

  test("auth rate-limit bucket is wired", async () => {
    const text = await src.text();
    expect(text).toContain(`isRateLimited(ip, "auth")`);
  });

  test("missing owner in production refuses at startup", async () => {
    const text = await src.text();
    expect(text).toContain("validateDashboardAuthConfig");
    expect(text).toMatch(/throw new Error/);
  });
});
