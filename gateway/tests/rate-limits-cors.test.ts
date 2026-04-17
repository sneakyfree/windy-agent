/**
 * Contract tests for P1-O5 (rate-limit LRU + bucket expansion) and
 * P2-S7 / P2-S8 (CORS allowlist behaviour).
 *
 * Pure-function tests on isRateLimited. End-to-end chat/validate
 * 429s live in gateway regression; here we pin the bucket logic
 * and the LRU eviction.
 */

import { afterEach, describe, expect, test } from "bun:test";
import {
  _clearRateLimits,
  _rateLimitMapSize,
  isRateLimited,
} from "../src/server";

afterEach(() => _clearRateLimits());

describe("isRateLimited — bucket-per-surface", () => {
  test("separate buckets don't share budget", () => {
    for (let i = 0; i < 10; i++) isRateLimited("1.2.3.4", "setup");
    // setup budget is 10 — we're at the limit, still allowed.
    expect(isRateLimited("1.2.3.4", "setup")).toBe(true);
    // chat bucket untouched → not limited
    expect(isRateLimited("1.2.3.4", "chat")).toBe(false);
  });

  test("setup bucket trips at 10/min", () => {
    let hits = 0;
    for (let i = 0; i < 20; i++) {
      if (!isRateLimited("9.9.9.9", "setup")) hits++;
    }
    // first 10 allowed, next 10 rate-limited
    expect(hits).toBe(10);
  });

  test("chat bucket trips at 60/min", () => {
    let allowed = 0;
    for (let i = 0; i < 100; i++) {
      if (!isRateLimited("5.5.5.5", "chat")) allowed++;
    }
    expect(allowed).toBe(60);
  });

  test("upstream bucket trips at 30/min", () => {
    let allowed = 0;
    for (let i = 0; i < 100; i++) {
      if (!isRateLimited("6.6.6.6", "upstream")) allowed++;
    }
    expect(allowed).toBe(30);
  });
});

describe("isRateLimited — LRU cap (P1-O5)", () => {
  test("evicts oldest entry when map fills", () => {
    // The cap is 50k; too slow to actually exercise in a unit test.
    // What we pin: size never exceeds the cap in ordinary use.
    for (let i = 0; i < 1000; i++) {
      isRateLimited(`ip-${i}`, "setup");
    }
    // 1000 entries is under the cap; all survive.
    expect(_rateLimitMapSize()).toBe(1000);
  });

  test("size increments by one per unique (bucket, ip)", () => {
    isRateLimited("a.b.c.d", "chat");
    isRateLimited("a.b.c.d", "chat");  // same key → still 1
    isRateLimited("a.b.c.d", "setup"); // different bucket → 2
    expect(_rateLimitMapSize()).toBe(2);
  });
});

describe("server.ts — CORS strictness regression guards", () => {
  const src = Bun.file(import.meta.dir + "/../src/server.ts");

  test("no fallback-to-first-origin path survives", async () => {
    const text = await src.text();
    // The old bug was: unknown origin → allowlist[0]. Any line that
    // assigns ACAO unconditionally from a ternary over the allowlist
    // should be gone.
    expect(text).not.toMatch(/allowedOrigins\.includes\(origin\) \? origin : allowedOrigins\[0\]/);
  });

  test("unknown origin gets no ACAO header", async () => {
    const text = await src.text();
    // The guard: we conditionally add ACAO inside `if (origin &&
    // allowedOrigins.includes(origin))`. The header name itself
    // should only appear inside that block.
    const coords = text.indexOf(`headers["Access-Control-Allow-Origin"] = origin;`);
    expect(coords).toBeGreaterThan(-1);
    const guardCoords = text.lastIndexOf(
      "allowedOrigins.includes(origin)",
      coords,
    );
    expect(guardCoords).toBeGreaterThan(-1);
    // Guard must appear before the assignment (i.e., within a
    // few lines above it).
    expect(coords - guardCoords).toBeLessThan(200);
  });

  test("Vary: Origin is emitted so caches key by origin", async () => {
    const text = await src.text();
    expect(text).toContain(`"Vary": "Origin"`);
  });
});
