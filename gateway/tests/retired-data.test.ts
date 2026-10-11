/**
 * Retired features' leftover data files are removed at gateway start.
 * machines.json held the tokens people typed for Mission Control's remote
 * machines (retired 2026-10-10).
 */
import { describe, expect, test } from "bun:test";
import { existsSync, lstatSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { RETIRED_DATA_FILES, removeRetiredDataFiles } from "../src/retired-data";

describe("retired data files", () => {
  test("machines.json is on the list", () => {
    expect(RETIRED_DATA_FILES).toContain("machines.json");
  });

  test("a leftover file is removed and reported; other files stay", () => {
    const dir = mkdtempSync(join(tmpdir(), "wf-retired-"));
    writeFileSync(join(dir, "machines.json"), JSON.stringify([{ token: "typed-token" }]));
    writeFileSync(join(dir, "providers.json"), "{}");

    expect(removeRetiredDataFiles(dir)).toEqual(["machines.json"]);
    expect(existsSync(join(dir, "machines.json"))).toBe(false);
    expect(readFileSync(join(dir, "providers.json"), "utf8")).toBe("{}");
  });

  test("a symlinked leftover is not followed: its target and the link stay", () => {
    const dir = mkdtempSync(join(tmpdir(), "wf-retired-"));
    const outside = mkdtempSync(join(tmpdir(), "wf-outside-"));
    const target = join(outside, "precious.txt");
    writeFileSync(target, "keep me");
    symlinkSync(target, join(dir, "machines.json"));

    expect(removeRetiredDataFiles(dir)).toEqual([]);
    expect(readFileSync(target, "utf8")).toBe("keep me");
    expect(lstatSync(join(dir, "machines.json")).isSymbolicLink()).toBe(true);
  });

  test("the old provider files are left for the brain's key salvage", () => {
    for (const name of ["providers.json", "provider_keys.json", "provider_notes.json"]) {
      expect(RETIRED_DATA_FILES).not.toContain(name);
    }
  });

  test("nothing to remove: no error, nothing reported", () => {
    const dir = mkdtempSync(join(tmpdir(), "wf-retired-"));
    expect(removeRetiredDataFiles(dir)).toEqual([]);
  });

  test("server.ts runs the cleanup at start", async () => {
    const src = await Bun.file(import.meta.dir + "/../src/server.ts").text();
    expect(src).toMatch(/async function main\(\) \{\s*for \(const name of removeRetiredDataFiles\(\)\)/);
  });
});
