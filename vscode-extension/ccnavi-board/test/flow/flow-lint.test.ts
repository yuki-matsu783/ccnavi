/**
 * フローの本文を実行ファイルに確かめさせる流れ（`core/flow-lint.ts`）と、その答えの読み方（`core/lintmodel.ts`）。
 * 実行ファイルの答えは、プロジェクト管理画面のテストと同じく lint の JSON を組んで渡す偽物。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { FLOW_TEMP_NAME, lintFlowText, type FlowLintRun } from "../../src/core/flow-lint.js";
import { FLOW_WHERE, parseLintJson, problemsOfFlow, unknownOption, type LintJson } from "../../src/core/lintmodel.js";

const SHOWN = ".ccnavi/approved/flows/i0001-01.yml";

function tmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-flow-lint-"));
  return dir;
}

/** 実行ファイルの `--lint --json --flow` の答え（偽物） */
function answer(problems: readonly { severity: string; where: string; detail: string }[]): LintJson {
  const parsed = parseLintJson(
    JSON.stringify({
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
    assert.deepEqual(verdict, { ok: true });
    assert.deepEqual(asked, [path.join(dir, FLOW_TEMP_NAME)]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("CB-T243 (flow) の error があれば、実行ファイルの理由を対象のファイルの綴りに直して断る。走らせられなければ断る", async () => {
  const dir = tmpDir();
  try {
    const tmp = path.join(dir, FLOW_TEMP_NAME);
    const refused = await lintFlowText("nodes:\n  - {id: a}\n  - {id: a}\n", dir, SHOWN, async () => ({
      ok: true,
      value: answer([{ severity: "error", where: FLOW_WHERE, detail: `${tmp}: ノードの id が重なっている（a）` }]),
    }));
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
