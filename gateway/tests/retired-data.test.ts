/**
 * Retired features' leftover data files are removed at gateway start.
 * machines.json held the tokens people typed for Mission Control's remote
 * machines (retired 2026-10-10).
 */
import { describe, expect, test } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "fs";
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

  test("nothing to remove: no error, nothing reported", () => {
    const dir = mkdtempSync(join(tmpdir(), "wf-retired-"));
    expect(removeRetiredDataFiles(dir)).toEqual([]);
  });

  test("server.ts runs the cleanup at start", async () => {
    const src = await Bun.file(import.meta.dir + "/../src/server.ts").text();
    expect(src).toMatch(/async function main\(\) \{\s*for \(const name of removeRetiredDataFiles\(\)\)/);
  });
});
