import { test } from "node:test";
import assert from "node:assert/strict";
import {
  binFromSettingsJson,
  hostTarget,
  locate,
  runnableTargets,
  type LocateInput,
} from "../src/core/locate.js";

function input(existing: readonly string[], overrides: Partial<LocateInput> = {}): LocateInput {
  return {
    root: "/ws",
    setting: "",
    settingsEnvBin: undefined,
    hostTarget: "linux-x86_64",
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

test("CB-T23 振り分けの sh を指していれば、隣のこの機械向けの実体を起動する", () => {
  // Windows では sh を直接起動できない。sh より先に隣の実体を探す。
  assert.deepEqual(
    locate(
      input(["/ws/.ccnavi/bin/ccnavi", "/ws/.ccnavi/bin/windows-x86_64/ccnavi.exe", "/ws/.ccnavi/bin/linux-x86_64/ccnavi"], {
        settingsEnvBin: ".ccnavi/bin/ccnavi",
        hostTarget: "windows-x86_64",
      }),
    ),
    { kind: "exe", path: "/ws/.ccnavi/bin/windows-x86_64/ccnavi.exe" },
  );
  // 設定が無くても既定の配布先を探す。
  assert.deepEqual(
    locate(input(["/ws/.ccnavi/bin/ccnavi", "/ws/.ccnavi/bin/linux-x86_64/ccnavi"])),
    { kind: "exe", path: "/ws/.ccnavi/bin/linux-x86_64/ccnavi" },
  );
  // arm64 の macOS は、自分向けが無ければ x86_64 を使う。
  assert.deepEqual(
    locate(input(["/ws/.ccnavi/bin/darwin-x86_64/ccnavi"], { hostTarget: "darwin-arm64" })),
    { kind: "exe", path: "/ws/.ccnavi/bin/darwin-x86_64/ccnavi" },
  );
  // 別の機械向けしか無ければ、実体としては選ばない。
  assert.equal(locate(input(["/ws/.ccnavi/bin/darwin-arm64/ccnavi"])), undefined);
});

test("CB-T24 機械の語は ccnavi/platformtag.py と揃える", () => {
  assert.equal(hostTarget("win32", "x64"), "windows-x86_64");
  assert.equal(hostTarget("darwin", "arm64"), "darwin-arm64");
  assert.equal(hostTarget("linux", "x64"), "linux-x86_64");
  assert.deepEqual(runnableTargets("windows-arm64"), ["windows-arm64", "windows-x86_64"]);
  assert.deepEqual(runnableTargets("linux-arm64"), ["linux-arm64"]);
});

test("CB-T22 settings.json の env.CCNAVI_BIN_PATH を読む", () => {
  assert.equal(binFromSettingsJson(JSON.stringify({ env: { CCNAVI_BIN_PATH: "dist/x" } })), "dist/x");
  assert.equal(binFromSettingsJson(JSON.stringify({ env: { CCNAVI_BIN_PATH: "" } })), undefined);
  assert.equal(binFromSettingsJson(JSON.stringify({ env: {} })), undefined);
  assert.equal(binFromSettingsJson("{"), undefined);
  assert.equal(binFromSettingsJson("[]"), undefined);
});
