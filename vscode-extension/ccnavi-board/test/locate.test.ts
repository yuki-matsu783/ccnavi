import { test } from "node:test";
import assert from "node:assert/strict";
import { binFromSettingsJson, locate, type LocateInput } from "../src/core/locate.js";

function input(existing: readonly string[], overrides: Partial<LocateInput> = {}): LocateInput {
  return {
    root: "/ws",
    setting: "",
    settingsEnvBin: undefined,
    exists: (p) => existing.includes(p),
    join: (...parts) => parts.join("/"),
    isAbsolute: (p) => p.startsWith("/"),
    ...overrides,
  };
}

test("CB-T20 設定 → settings.json → 既定 → ソースの順に探す", () => {
  assert.deepEqual(
    locate(input(["/ws/dist/ccnavi/ccnavi", "/ws/ccnavi/__main__.py"])),
    { kind: "exe", path: "/ws/dist/ccnavi/ccnavi" },
  );
  assert.deepEqual(
    locate(input(["/ws/dist/ccnavi/ccnavi", "/ws/build/ccnavi"], { settingsEnvBin: "build/ccnavi" })),
    { kind: "exe", path: "/ws/build/ccnavi" },
  );
  assert.deepEqual(
    locate(input(["/opt/ccnavi", "/ws/build/ccnavi"], { setting: "/opt/ccnavi", settingsEnvBin: "build/ccnavi" })),
    { kind: "exe", path: "/opt/ccnavi" },
  );
  assert.deepEqual(locate(input(["/ws/ccnavi/__main__.py"])), { kind: "uv", root: "/ws" });
  assert.equal(locate(input([])), undefined);
});

test("CB-T21 .exe は綴りに無くても試す", () => {
  assert.deepEqual(
    locate(input(["/ws/dist/ccnavi/ccnavi.exe"])),
    { kind: "exe", path: "/ws/dist/ccnavi/ccnavi.exe" },
  );
});

test("CB-T22 settings.json の env.CCNAVI_BIN_PATH を読む", () => {
  assert.equal(binFromSettingsJson(JSON.stringify({ env: { CCNAVI_BIN_PATH: "dist/x" } })), "dist/x");
  assert.equal(binFromSettingsJson(JSON.stringify({ env: { CCNAVI_BIN_PATH: "" } })), undefined);
  assert.equal(binFromSettingsJson(JSON.stringify({ env: {} })), undefined);
  assert.equal(binFromSettingsJson("{"), undefined);
  assert.equal(binFromSettingsJson("[]"), undefined);
});
