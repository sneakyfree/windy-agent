/**
 * Wave 3 (2026-10): the gateway's own provider store is retired. Keys live only in
 * the agent's env file; the brain moves any key the old files held (provider_salvage.py).
 */
import { describe, expect, test } from "bun:test";
import { existsSync, readdirSync, readFileSync } from "fs";
import { join } from "path";

const SRC = join(import.meta.dir, "../src");
const PUBLIC = join(import.meta.dir, "../public");

function sources(): [string, string][] {
  return readdirSync(SRC)
    .filter((f) => f.endsWith(".ts"))
    .map((f) => [f, readFileSync(join(SRC, f), "utf8")]);
}

describe("provider store retired", () => {
  test("no /api/providers route and no providers module", () => {
    expect(existsSync(join(SRC, "providers.ts"))).toBe(false);
    for (const [name, text] of sources()) {
      expect(`${name}: ${/\/api\/providers/.test(text)}`).toBe(`${name}: false`);
      expect(`${name}: ${/from "\.\/providers"/.test(text)}`).toBe(`${name}: false`);
    }
  });

  test("no gateway code names a provider data file", () => {
    for (const [name, text] of sources()) {
      if (name === "retired-data.ts") continue; // explains why they are NOT on its list
      expect(`${name}: ${/provider_keys\.json|provider_notes\.json|"providers\.json"/.test(text)}`)
        .toBe(`${name}: false`);
    }
  });

  test("the unlinked legacy pages (the only callers) are gone", () => {
    expect(existsSync(join(PUBLIC, "index.legacy.html"))).toBe(false);
    expect(existsSync(join(PUBLIC, "setup.legacy.html"))).toBe(false);
  });
});
