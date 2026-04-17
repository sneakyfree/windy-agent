/**
 * Contract tests for isLoopbackAddress (P0-S1 fix).
 *
 * The fix swaps the forgeable Host header for the peer socket's
 * actual IP. These tests pin the decision set for that IP check:
 * loopback → true, everything else → false, even strings that
 * LOOK loopback-ish (e.g. "127.example.com") must not pass.
 *
 * Run with: bun test gateway/tests/localhost.test.ts
 */

import { describe, expect, test } from "bun:test";
import { isLoopbackAddress } from "../src/server";

describe("isLoopbackAddress — loopback addresses pass", () => {
  test.each([
    ["127.0.0.1"],
    ["127.0.0.2"],
    ["127.42.255.1"],
    ["::1"],
    ["::ffff:127.0.0.1"],
    ["::ffff:127.1.2.3"],
  ])("accepts %s", (addr) => {
    expect(isLoopbackAddress(addr)).toBe(true);
  });
});

describe("isLoopbackAddress — non-loopback addresses are refused", () => {
  test.each([
    ["8.8.8.8"],
    ["93.184.216.34"],
    ["10.0.0.1"],        // RFC1918 — not loopback
    ["192.168.1.1"],     // RFC1918 — not loopback
    ["169.254.169.254"], // link-local — NOT loopback
    ["2606:4700:4700::1111"],
    ["::ffff:8.8.8.8"],  // IPv4-mapped v6 public
  ])("refuses %s", (addr) => {
    expect(isLoopbackAddress(addr)).toBe(false);
  });

  test.each([
    [null],
    [undefined],
    [""],
    ["localhost"],               // Host *header*, not an IP — NEVER trust
    ["127.example.com"],         // string prefix trick
    ["127-0-0-1.nip.io"],        // DNS lookalike
    ["0.0.0.0"],                 // binds loopback on some boxes, but not peer
  ])("refuses non-IP or spoofed %s", (addr) => {
    expect(isLoopbackAddress(addr)).toBe(false);
  });
});
