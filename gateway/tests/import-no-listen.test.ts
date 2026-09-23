/**
 * Importing src/server.ts must NOT start the server.
 *
 * Several test files import pure helpers from server.ts. When the module
 * booted on import, each of them tried to bind :3000, and CI failed on any
 * runner where 3000 was already taken ("Failed to start server. Is port
 * 3000 in use?") even though every assertion passed.
 */
import { describe, expect, test } from "bun:test";

describe("server.ts — import has no side effects", () => {
  test("boot is guarded by import.meta.main", async () => {
    const src = await Bun.file(import.meta.dir + "/../src/server.ts").text();
    expect(src).toMatch(/if \(import\.meta\.main\) \{\s*main\(\);\s*\}/);
    expect(src).not.toMatch(/^main\(\);$/m);
  });

});
