/**
 * Executable skills were retired (2026-10-10): skills are text playbooks
 * the agent reads, never run. The gateway must not expose the old
 * evaluate / golden-tests / regression routes, and POST /api/skills must
 * refuse any language other than "playbook" before reaching the brain.
 */

import { describe, expect, test } from "bun:test";

describe("server.ts — skills are text playbooks only", () => {
  const src = Bun.file(import.meta.dir + "/../src/server.ts");

  test("no bridge call to the retired skill execution methods", async () => {
    const text = await src.text();
    expect(text).not.toContain('"skills.evaluate"');
    expect(text).not.toContain('"skills.golden_tests"');
    expect(text).not.toContain('"skills.regression"');
    expect(text).not.toMatch(/\\\/evaluate\$/);
    expect(text).not.toMatch(/golden-tests\$/);
    expect(text).not.toContain('"/api/skills/regression"');
  });

  test("POST /api/skills refuses non-playbook languages", async () => {
    const text = await src.text();
    const create = text.split('path === "/api/skills" && req.method === "POST"')[1] ?? "";
    const beforeBridge = create.split('bridge.call("skills.create"')[0] ?? "";
    expect(beforeBridge).toContain('language !== "playbook"');
    expect(beforeBridge).toContain("status: 400");
  });
});

describe("dashboard Skills page — no execution buttons", () => {
  const page = Bun.file(import.meta.dir + "/../dashboard/src/pages/Skills.tsx");

  test("no regression / evaluate / golden-test calls", async () => {
    const text = await page.text();
    expect(text).not.toContain("/api/skills/regression");
    expect(text).not.toContain("/evaluate");
    expect(text).not.toContain("golden-tests");
  });
});
