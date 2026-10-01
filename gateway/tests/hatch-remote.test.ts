import { describe, expect, test } from "bun:test";
import { readFileSync } from "fs";
import { join } from "path";
import { handleHatchRemote } from "../src/hatch-remote";

describe("POST /hatch/remote — retired (ADR-059, one hallway)", () => {
  test("answers 410 hatch_moved and spawns nothing", async () => {
    const req = new Request("http://localhost/hatch/remote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ broker_token: "bk_live_x" }),
    });
    const resp = handleHatchRemote(req);
    expect(resp.status).toBe(410);
    expect(await resp.json()).toEqual({
      error: "hatch_moved",
      message: "Hatching happens in the Windy hatch ceremony. Run windy go or use the dashboard.",
    });
  });

  test("the module no longer spawns the Python hatch", () => {
    const src = readFileSync(join(import.meta.dir, "../src/hatch-remote.ts"), "utf8");
    expect(src).not.toContain('from "bun"');
    expect(src).not.toContain('"-m"');
  });
});
