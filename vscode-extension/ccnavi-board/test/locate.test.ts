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

test("CB-T117 E1 振り分けの sh を指していれば、../bin/ のこの機械向けの実体を起動する", () => {
  assert.deepEqual(
    locate(
      input(
        [
          "/ws/.ccnavi/scripts/ccnavi-launcher.sh",
          "/ws/.ccnavi/bin/linux-x86_64/ccnavi",
          "/ws/.ccnavi/bin/windows-x86_64/ccnavi.exe",
        ],
        { settingsEnvBin: ".ccnavi/scripts/ccnavi-launcher.sh" },
      ),
    ),
    { kind: "exe", path: "/ws/.ccnavi/bin/linux-x86_64/ccnavi" },
  );
  // Windows は .exe を探す。
  assert.deepEqual(
    locate(
      input(
        [
          "/ws/.ccnavi/scripts/ccnavi-launcher.sh",
          "/ws/.ccnavi/bin/linux-x86_64/ccnavi",
          "/ws/.ccnavi/bin/windows-x86_64/ccnavi.exe",
        ],
        { settingsEnvBin: ".ccnavi/scripts/ccnavi-launcher.sh", hostTarget: "windows-x86_64" },
      ),
    ),
    { kind: "exe", path: "/ws/.ccnavi/bin/windows-x86_64/ccnavi.exe" },
  );
  // arm64 の macOS は、自分向けが無ければ x86_64 を使う。
  assert.deepEqual(
    locate(
      input(["/ws/.ccnavi/bin/darwin-x86_64/ccnavi"], {
        settingsEnvBin: ".ccnavi/scripts/ccnavi-launcher.sh",
        hostTarget: "darwin-arm64",
      }),
    ),
    { kind: "exe", path: "/ws/.ccnavi/bin/darwin-x86_64/ccnavi" },
  );
  // 設定が無くても既定の綴りとして探す。絶対の綴りでも同じ。
  assert.deepEqual(
    locate(input(["/ws/.ccnavi/bin/linux-x86_64/ccnavi"])),
    { kind: "exe", path: "/ws/.ccnavi/bin/linux-x86_64/ccnavi" },
  );
  assert.deepEqual(
    locate(input(["/opt/x/bin/linux-x86_64/ccnavi"], { setting: "/opt/x/scripts/ccnavi-launcher.sh" })),
    { kind: "exe", path: "/opt/x/bin/linux-x86_64/ccnavi" },
  );
});

test("CB-T118 E2 振り分けの sh そのものは返さない。実体が無ければ次の候補へ進む", () => {
  // Windows では sh を直接起動できない。
  const sh = "/ws/.ccnavi/scripts/ccnavi-launcher.sh";
  assert.equal(locate(input([sh], { settingsEnvBin: ".ccnavi/scripts/ccnavi-launcher.sh" })), undefined);
  assert.equal(locate(input([sh, `${sh}.exe`])), undefined);
  // 次の候補（dist/）へ進む。
  assert.deepEqual(
    locate(input([sh, "/ws/dist/ccnavi/ccnavi"], { settingsEnvBin: ".ccnavi/scripts/ccnavi-launcher.sh" })),
    { kind: "exe", path: "/ws/dist/ccnavi/ccnavi" },
  );
  // sh の隣（.ccnavi/scripts/<os>-<arch>/）は配る場所ではないので探さない。
  assert.equal(
    locate(input([sh, "/ws/.ccnavi/scripts/linux-x86_64/ccnavi"], { settingsEnvBin: ".ccnavi/scripts/ccnavi-launcher.sh" })),
    undefined,
  );
  // 別の機械向けしか無ければ、実体としては選ばずソースへ進む。
  assert.deepEqual(
    locate(input([sh, "/ws/.ccnavi/bin/darwin-arm64/ccnavi", "/ws/ccnavi/__main__.py"])),
    { kind: "uv", root: "/ws" },
  );
});

test("CB-T119 E3 名前が ccnavi-launcher.sh でない綴りは綴りそのものを探し、隣は見ない", () => {
  assert.deepEqual(
    locate(
      input(["/ws/tools/scripts/ccnavi", "/ws/tools/scripts/linux-x86_64/ccnavi"], { settingsEnvBin: "tools/scripts/ccnavi" }),
    ),
    { kind: "exe", path: "/ws/tools/scripts/ccnavi" },
  );
  assert.equal(
    locate(input(["/ws/tools/scripts/linux-x86_64/ccnavi"], { settingsEnvBin: "tools/scripts/ccnavi" })),
    undefined,
  );
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
