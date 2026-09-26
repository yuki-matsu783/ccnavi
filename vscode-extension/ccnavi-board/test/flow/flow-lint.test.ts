/**
 * フローの本文を実行ファイルに確かめさせる流れ（`core/flow-lint.ts`）と、その答えの読み方（`core/lintmodel.ts`）。
 * 実行ファイルの答えは、プロジェクト管理画面のテストと同じく lint の JSON を組んで渡す偽物。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { FLOW_TEMP_PREFIX, lintFlowText, type FlowLintRun } from "../../src/core/flow-lint.js";
import { FLOW_WHERE, parseLintJson, problemsOfFlow, unknownOption, type LintJson } from "../../src/core/lintmodel.js";

const SHOWN = ".ccnavi/approved/flows/i0001-01.yml";

function tmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-flow-lint-"));
  return dir;
}

/** 答えに `flow` を載せない（`--flow` を知らない古い実行ファイル） */
const ABSENT = Symbol("absent");

/** 実行ファイルの `--lint --json --flow` の答え（偽物） */
function answer(problems: readonly { severity: string; where: string; detail: string }[], flow: unknown = { path: "/tmp/x.yml", data: { nodes: [] } }): LintJson {
  const parsed = parseLintJson(
    JSON.stringify({
      ...(flow === ABSENT ? {} : { flow }),
      version: 1,
      root: "/ws",
      rules: "/ws/.ccnavi/common/rules.yml",
      mode: "enable",
      ticket_control: "enable",
      projects: [],
      problems,
      errors: problems.filter((p) => p.severity === "error").length,
      warns: problems.filter((p) => p.severity !== "error").length,
    }),
  );
  assert.ok(parsed.ok);
  return parsed.value;
}

test("CB-T242 本文を一時ファイルに書いて実行ファイルに渡し、(flow) の error が無ければ通す。ほかの設定の苦情では止めない", async () => {
  const dir = tmpDir();
  try {
    const asked: string[] = [];
    const verdict = await lintFlowText("nodes: []\n", dir, SHOWN, async (file): Promise<FlowLintRun> => {
      asked.push(file);
      // 渡すときには一時ファイルに本文が書いてある
      assert.equal(fs.readFileSync(file, "utf8"), "nodes: []\n");
      return {
        ok: true,
        value: answer([
          { severity: "error", where: "(rules) r1", detail: "ルールの苦情" },
          { severity: "warn", where: FLOW_WHERE, detail: "言うだけの苦情" },
        ]),
      };
    });
    // 通れば、実行ファイルが読んだ中身を返す
    assert.deepEqual(verdict, { ok: true, data: { nodes: [] } });
    assert.equal(asked.length, 1);
    assert.equal(path.dirname(asked[0]), dir);
    assert.ok(path.basename(asked[0]).startsWith(FLOW_TEMP_PREFIX));
    // 使い終わった一時ファイルは消す
    assert.deepEqual(fs.readdirSync(dir), []);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("CB-T243 (flow) の error があれば、実行ファイルの理由を対象のファイルの綴りに直して断る。走らせられなければ断る", async () => {
  const dir = tmpDir();
  try {
    let tmp = "";
    const refused = await lintFlowText("nodes:\n  - {id: a}\n  - {id: a}\n", dir, SHOWN, async (file) => {
      tmp = file;
      return { ok: true, value: answer([{ severity: "error", where: FLOW_WHERE, detail: `${file}: ノードの id が重なっている（a）` }], { path: file, data: null }) };
    });
    assert.equal(refused.ok, false);
    const error = refused.ok ? "" : refused.error;
    assert.match(error, /ノードの id が重なっている（a）/);
    assert.ok(error.includes(SHOWN), error);
    assert.ok(!error.includes(tmp), error);
    // 実行ファイルを走らせられない（古くて --flow を知らない、など）なら、その理由で断る（開かない・保存しない側）
    const failed = await lintFlowText("nodes: []\n", dir, SHOWN, async () => ({ ok: false, error: "実行ファイルが --flow を知らない（古い）" }));
    assert.deepEqual(failed, { ok: false, error: "実行ファイルが --flow を知らない（古い）" });
    // 一時ファイルを書けなければ、実行ファイルを起こさずに断る
    const missing = path.join(dir, "gone");
    let called = false;
    const unwritable = await lintFlowText("nodes: []\n", missing, SHOWN, async () => {
      called = true;
      return { ok: true, value: answer([]) };
    });
    assert.equal(unwritable.ok, false);
    assert.equal(called, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("CB-T244 lint の JSON から (flow) の苦情だけを引き、古い実行ファイルの「知らないオプション」を見分ける", () => {
  const lint = answer([
    { severity: "error", where: "(flow)", detail: "a" },
    { severity: "error", where: "(flow) x", detail: "b" },
    { severity: "warn", where: "(projects)", detail: "c" },
  ]);
  assert.deepEqual(
    problemsOfFlow(lint).map((p) => p.detail),
    ["a"],
  );
  const stderr = "usage: ccnavi [--root ROOT] ...\n              [command ...]\nccnavi: error: unrecognized arguments: --flow\n";
  assert.equal(unknownOption(stderr, "--flow"), true);
  assert.equal(unknownOption(stderr, "--risk"), false);
  // usage の行に出る綴り（[--flow FLOW]）は数えない
  assert.equal(unknownOption("usage: ccnavi [--flow FLOW]\nccnavi: error: something else\n", "--flow"), false);
});

test("CB-T246 一時ファイルは呼ぶたびに別の名前で書き、終われば消す。重なった呼び出しでも互いの本文を読み違えない", async () => {
  const dir = tmpDir();
  try {
    const seen = new Map<string, string>();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const run = (text: string) =>
      lintFlowText(text, dir, SHOWN, async (file): Promise<FlowLintRun> => {
        seen.set(text, file);
        await gate;
        // 待っている間にもう 1 本が書かれても、自分の本文のまま
        assert.equal(fs.readFileSync(file, "utf8"), text);
        return { ok: true, value: answer([]) };
      });
    const first = run("nodes: [{id: a}]\n");
    const second = run("nodes: [{id: b}]\n");
    await new Promise((resolve) => setImmediate(resolve));
    release();
    assert.deepEqual(await Promise.all([first, second]), [
      { ok: true, data: { nodes: [] } },
      { ok: true, data: { nodes: [] } },
    ]);
    assert.notEqual(seen.get("nodes: [{id: a}]\n"), seen.get("nodes: [{id: b}]\n"));
    assert.deepEqual(fs.readdirSync(dir), []);
    // バイト列はそのまま書く（開くときは読んだバイトのまま確かめさせる）
    const bytes = Buffer.from([0x6e, 0x3a, 0x20, 0xff, 0x0a]);
    await lintFlowText(bytes, dir, SHOWN, async (file) => {
      assert.deepEqual(fs.readFileSync(file), bytes);
      return { ok: true, value: answer([]) };
    });
    // 落ちても消す
    await assert.rejects(lintFlowText("x", dir, SHOWN, async () => {
      throw new Error("boom");
    }));
    assert.deepEqual(fs.readdirSync(dir), []);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("CB-T247 答えに読んだ中身（flow）が無ければ通さない（実行ファイルがフローを見たか分からない）", async () => {
  const dir = tmpDir();
  try {
    for (const flow of [ABSENT, { path: "/x" }, { path: "/x", data: null }]) {
      const verdict = await lintFlowText("nodes: []\n", dir, SHOWN, async () => ({ ok: true, value: answer([], flow) }));
      assert.equal(verdict.ok, false, String(flow === ABSENT ? "absent" : JSON.stringify(flow)));
      assert.match(verdict.ok ? "" : verdict.error, /flow）を返さない（古い）/);
    }
    // lint の JSON の読み手は flow を持ち越す。無ければ欄ごと無い
    assert.deepEqual(answer([], { path: "/p", data: { a: 1 } }).flow, { path: "/p", data: { a: 1 } });
    assert.equal(answer([], ABSENT).flow, undefined);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
