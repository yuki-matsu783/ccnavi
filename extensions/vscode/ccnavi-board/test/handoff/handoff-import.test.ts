/**
 * 取り込み待ち画面の「取り込む」の手順（`src/core/handoff-import.ts`）を、実行ファイルを使わずに確かめる。
 * 呼び出しの層（`HandoffDeps`）を差し替え、呼ぶ順・渡す引数・`expect` の取り方・錠の掛かり方・端末の案内を見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { importSelected, terminalGuide, type HandoffDeps, type ViewAnswer } from "../../src/core/handoff-import.js";
import type { HandoffEntry, HandoffList, HandoffResult } from "../../src/core/handoff-view.js";
import type { Lock } from "../../src/core/lock.js";
import { entry, list, sampleEntries } from "../helpers/handoff.js";

const OPEN: Lock = { locked: false, reason: "", doing: [] };
const LOCKED: Lock = { locked: true, reason: "作業中のチケットがあります（T1）", doing: ["T1"] };

const TEMPLATE = ".ccnavi/config/project/templates/login.yml";
const RULES = ".ccnavi/config/project/rules.yml";
const DOCS = ".ccnavi/docs/guide.md";
const HOOK = ".claude/hooks/guard.sh";

const DONE: HandoffResult = { ok: true, applied: [{ path: TEMPLATE, kind: "template" }], removedDrafts: [TEMPLATE], keptDrafts: [] };

/** 実行ファイルの見るだけを真似る。パスを指した対象だけが selected になり、expect はその集合ごとに別の値を返す */
function viewer(entries: readonly HandoffEntry[], override: (paths: readonly string[], answer: HandoffList) => HandoffList = (_, a) => a) {
  return (paths: readonly string[]): ViewAnswer => {
    const chosen = entries.map((e) => ({ ...e, selected: paths.includes(e.path) }));
    return { ok: true, value: override(paths, list(chosen, { expect: `expect-of(${paths.join(",")})`, ok: chosen.filter((e) => e.selected).every((e) => e.errors.length === 0 && e.state !== "conflict" && e.state !== "rejected") })) };
  };
}

interface Rig {
  readonly deps: HandoffDeps;
  readonly calls: string[];
  readonly applied: { paths: readonly string[]; expect: string }[];
}

function rig(options: { readonly lock?: Lock; readonly view?: (paths: readonly string[]) => ViewAnswer; readonly result?: HandoffResult } = {}): Rig {
  const calls: string[] = [];
  const applied: { paths: readonly string[]; expect: string }[] = [];
  const view = options.view ?? viewer(sampleEntries());
  return {
    calls,
    applied,
    deps: {
      view: async (paths) => {
        calls.push(`view:${paths.join(",")}`);
        return view(paths);
      },
      apply: async (paths, expect) => {
        calls.push(`apply:${paths.join(",")}`);
        applied.push({ paths, expect });
        return options.result ?? DONE;
      },
      lock: async () => {
        calls.push("lock");
        return options.lock ?? OPEN;
      },
    },
  };
}

const shown = list(sampleEntries());

test("CB-T1122 取り込みは、錠を取り直し、選んだパスだけを指して見るだけをもう一度呼び、その expect で取り込む。一覧の expect は使わない", async () => {
  const r = rig();
  const outcome = await importSelected(r.deps, shown, [TEMPLATE, DOCS]);
  assert.deepEqual(r.calls, ["lock", `view:${TEMPLATE},${DOCS}`, `apply:${TEMPLATE},${DOCS}`]);
  assert.deepEqual(r.applied, [{ paths: [TEMPLATE, DOCS], expect: `expect-of(${TEMPLATE},${DOCS})` }]);
  assert.notEqual(r.applied[0].expect, shown.expect, "画面に見せた一覧の expect（省略時の集合）を使わない");
  assert.deepEqual(outcome, { result: DONE, attempted: true });
});

test("CB-T1123 取り込みが断った（ok が偽）ときも、呼んだことは伝わり、理由はそのまま返る", async () => {
  const failed: HandoffResult = { ok: false, error: "--expect が合いません" };
  const outcome = await importSelected(rig({ result: failed }).deps, shown, [DOCS]);
  assert.deepEqual(outcome, { result: failed, attempted: true });
});

test("CB-T1124 作業中の錠は template と layer-config の取り込みを止める。止めたときは見るだけも取り込みも呼ばない", async () => {
  for (const path of [TEMPLATE, RULES]) {
    const r = rig({ lock: LOCKED });
    const outcome = await importSelected(r.deps, shown, [path]);
    assert.equal(outcome.attempted, false);
    assert.equal(outcome.result.ok, false);
    assert.match(outcome.result.ok ? "" : outcome.result.error, new RegExp(`${path.replace(/\./g, "\\.")}: 作業中のチケットがあります（T1）`));
    assert.deepEqual(r.calls, ["lock"]);
  }
  // 1 本でも止まる対象が混じれば、全体を止める（部分的に取り込まない）
  const mixed = rig({ lock: LOCKED });
  assert.equal((await importSelected(mixed.deps, shown, [DOCS, TEMPLATE])).attempted, false);
  assert.deepEqual(mixed.calls, ["lock"]);
});

test("CB-T1125 docs には錠を掛けない。錠が掛かっていても docs だけの取り込みは通る", async () => {
  const r = rig({ lock: LOCKED, result: { ok: true, applied: [{ path: DOCS, kind: "docs" }], removedDrafts: [], keptDrafts: [] } });
  const outcome = await importSelected(r.deps, shown, [DOCS]);
  assert.equal(outcome.attempted, true);
  assert.deepEqual(r.calls, ["lock", `view:${DOCS}`, `apply:${DOCS}`]);
});

test("CB-T1126 錠は押した時点で取り直す。画面の錠が空いて見えても、いま掛かっていれば止める", async () => {
  // 画面に見せた時点では錠が無かったという前提。`deps.lock()` が掛かった錠を返せば、一覧の古い判断では通さない
  const r = rig({ lock: LOCKED });
  const outcome = await importSelected(r.deps, shown, [TEMPLATE]);
  assert.equal(outcome.attempted, false);
  assert.deepEqual(r.applied, []);
});

test("CB-T1127 defense は端末のみ。錠が空いていてもボードからは取り込まず、実行ファイルを呼ばない", async () => {
  const r = rig();
  const outcome = await importSelected(r.deps, shown, [HOOK]);
  assert.equal(outcome.attempted, false);
  assert.match(outcome.result.ok ? "" : outcome.result.error, /ボードからは取り込めません。端末で取り込みます/);
  assert.deepEqual(r.calls, ["lock"]);
  // docs と混ぜても、defense が混じれば全体を止める
  const mixed = rig();
  assert.equal((await importSelected(mixed.deps, shown, [DOCS, HOOK])).attempted, false);
  assert.deepEqual(mixed.applied, []);
});

test("CB-T1128 取り込めない対象（conflict・error・unchanged）と、一覧にない対象と、空の選択は、実行ファイルを呼ばずに理由を返す", async () => {
  const cases: [string, readonly string[], RegExp][] = [
    ["conflict", [".ccnavi/config/project/phases.yml"], /conflict/],
    ["error あり", [".ccnavi/docs/broken.md"], /error/],
    ["unchanged", [".ccnavi/docs/same.md"], /同じ内容/],
    ["一覧にない", [".ccnavi/docs/none.md"], /一覧にない対象です/],
    ["空", [], /選ばれていません/],
  ];
  for (const [name, paths, pattern] of cases) {
    const r = rig();
    const outcome = await importSelected(r.deps, shown, paths);
    assert.equal(outcome.attempted, false, name);
    assert.match(outcome.result.ok ? "" : outcome.result.error, pattern, name);
    assert.deepEqual(r.calls.filter((c) => c.startsWith("view") || c.startsWith("apply")), [], name);
  }
  const rejected = list([entry({ path: "r.md", kind: "docs", state: "rejected" })]);
  const r = rig();
  const outcome = await importSelected(r.deps, rejected, ["r.md"]);
  assert.equal(outcome.attempted, false);
  assert.match(outcome.result.ok ? "" : outcome.result.error, /rejected/);
});

test("CB-T1129 見せたあとに下書きか取り込み先が変わった（entry_sha が違う）ら、取り込まない", async () => {
  const changed = viewer(sampleEntries(), (_, answer) => ({
    ...answer,
    entries: answer.entries.map((e) => (e.path === DOCS ? { ...e, entry_sha: "0".repeat(16) } : e)),
  }));
  const r = rig({ view: changed });
  const outcome = await importSelected(r.deps, shown, [DOCS]);
  assert.equal(outcome.attempted, false);
  assert.match(outcome.result.ok ? "" : outcome.result.error, /見せたあとに下書きか取り込み先が変わりました/);
  assert.deepEqual(r.applied, []);
});

test("CB-T1130 もう一度の見るだけが食い違う（選んだ集合が違う・検査を通らない・expect が空・失敗）ときは取り込まない", async () => {
  const bad: [string, (paths: readonly string[]) => ViewAnswer, RegExp][] = [
    ["集合が違う", (paths) => ({ ok: true, value: list(sampleEntries().map((e) => ({ ...e, selected: paths.includes(e.path) || e.path === DOCS })), { expect: "e" }) }), /見せた一覧と違います/],
    ["ok が偽", (paths) => ({ ok: true, value: list(sampleEntries().map((e) => ({ ...e, selected: paths.includes(e.path) })), { expect: "e", ok: false }) }), /検査を通らない/],
    ["expect が空", (paths) => ({ ok: true, value: list(sampleEntries().map((e) => ({ ...e, selected: paths.includes(e.path) })), { expect: "" }) }), /expect を返しませんでした/],
    ["呼び出しの失敗", () => ({ ok: false, error: "実行ファイルが返らない" }), /実行ファイルが返らない/],
    ["選んだ対象が消えた", () => ({ ok: true, value: list([], { expect: "e" }) }), /見せた一覧と違います/],
  ];
  for (const [name, view, pattern] of bad) {
    const r = rig({ view });
    const outcome = await importSelected(r.deps, shown, [TEMPLATE]);
    assert.equal(outcome.attempted, false, name);
    assert.match(outcome.result.ok ? "" : outcome.result.error, pattern, name);
    assert.deepEqual(r.applied, [], name);
  }
});

test("CB-T1131 パスは大文字小文字を無視して引くが、実行ファイルには一覧のとおりの表記を渡す。同じ対象を 2 回指しても 1 本", async () => {
  const r = rig();
  const outcome = await importSelected(r.deps, shown, [DOCS.toUpperCase(), DOCS]);
  assert.equal(outcome.attempted, true);
  assert.deepEqual(r.calls, ["lock", `view:${DOCS}`, `apply:${DOCS}`]);
});

test("CB-T1132 端末の案内は、そのパスだけを指して見るだけをもう一度呼び、返った expect でコマンドを組む", async () => {
  const r = rig();
  const guide = await terminalGuide(r.deps, shown, HOOK);
  assert.deepEqual(r.calls, [`view:${HOOK}`]);
  // 見るだけの答えの expect（この試験では集合ごとに別の値）をそのまま使う。拡張が組み直さない
  // ツリーは見るだけの答えの tree（`--root` で、下書きのあるツリーへ取り込む）
  assert.deepEqual(guide, { command: `sh .ccnavi/scripts/ccnavi-user-handoff.sh --root /ws ${HOOK} --yes --expect expect-of(${HOOK})`, error: "" });
});

test("CB-T1133 端末の案内を出さない場合。ボードから取り込める対象・取り込めない状態・error・一覧にない・見せたあとの変更・見るだけの失敗", async () => {
  const r = rig();
  assert.match((await terminalGuide(r.deps, shown, DOCS)).error, /ボードから取り込めます/);
  assert.match((await terminalGuide(r.deps, shown, "none")).error, /一覧にない対象です/);
  const conflictHook = list([entry({ path: HOOK, kind: "defense", state: "conflict", board: false })]);
  assert.match((await terminalGuide(r.deps, conflictHook, HOOK)).error, /conflict/);
  const errorHook = list([entry({ path: HOOK, kind: "defense", board: false, errors: ["CRLF"] })]);
  assert.match((await terminalGuide(r.deps, errorHook, HOOK)).error, /error/);
  assert.deepEqual(r.calls, [], "出さない場合は見るだけも呼ばない");
  const stale = rig({ view: viewer(sampleEntries(), (_, a) => ({ ...a, entries: a.entries.map((e) => (e.path === HOOK ? { ...e, entry_sha: "9".repeat(16) } : e)) })) });
  assert.match((await terminalGuide(stale.deps, shown, HOOK)).error, /変わりました/);
  const failed = rig({ view: () => ({ ok: false, error: "失敗した" }) });
  assert.deepEqual(await terminalGuide(failed.deps, shown, HOOK), { command: "", error: "失敗した" });
});
