/**
 * フローの本文が正しいかを実行ファイルに聞く（ADR-0035・ADR-0085）。フロー編集画面の、開くときと保存の前。
 *
 * 読めるか（大きさ・YAML として読めるか・別名）と形（`nodes` が無い、`id` が無い・重なる など）の答えは
 * 実行ファイルの `--lint --json --flow <パス>` が出す。読み手も検査も SubagentStart と同じもの
 * （`ccnavi/flow.py` の `load`）で、拡張は自分で判定し直さない。ルール設定・リスク管理・フェーズ管理の
 * 画面が `--lint --rules` / `--risk` / `--phases` に一時ファイルで聞くのと同じ形。
 *
 * 一時ファイルは画面ごとの一時ディレクトリ（`os.tmpdir()` の下の `ccnavi-flow-*`）に置き、名前は
 * 決まった `flow.yml`（ルール設定の画面の `rules.yml` と同じ）。苦情は一時ファイルのパスを名乗るので、
 * 画面に出すときは対象のファイルの綴りに直す。
 *
 * VS Code の API は使わない（単体テストで確かめる。実行ファイルの答えは偽物を渡す）。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { problemsOfFlow, type LintJson } from "./lintmodel.js";

/** 実行ファイルを走らせた結果（`ccnavi.ts` の `RunResult<LintJson>` と同じ形） */
export type FlowLintRun = { readonly ok: true; readonly value: LintJson } | { readonly ok: false; readonly error: string };

export type FlowLintVerdict = { readonly ok: true } | { readonly ok: false; readonly error: string };

/** 一時ファイルの名前 */
export const FLOW_TEMP_NAME = "flow.yml";

/**
 * 本文を一時ファイルに書いて実行ファイルに確かめさせる。`(flow)` の error が 1 件でもあれば理由を返す。
 * ほかの設定の苦情（ルールなど）では止めない。実行ファイルを走らせられない・答えを読めないときも
 * 止める側（確かめられないものを正しいとは言わない）。
 */
export async function lintFlowText(
  text: string,
  tmpDir: string,
  shown: string,
  lint: (file: string) => Promise<FlowLintRun>,
): Promise<FlowLintVerdict> {
  const tmp = path.join(tmpDir, FLOW_TEMP_NAME);
  try {
    fs.writeFileSync(tmp, text, "utf8");
  } catch (error) {
    return { ok: false, error: `確かめるための一時ファイルを書けない: ${(error as Error).message}` };
  }
  const ran = await lint(tmp);
  if (!ran.ok) {
    return { ok: false, error: ran.error };
  }
  const errors = problemsOfFlow(ran.value).filter((p) => p.severity === "error");
  if (errors.length === 0) {
    return { ok: true };
  }
  // 苦情は渡した一時ファイルのパスを名乗るので、対象のファイルの綴りに直す
  const said = errors.map((p) => p.detail.split(tmp).join(shown)).join("\n");
  return { ok: false, error: `実行ファイル（--lint --flow）が読めないと言った: ${said}` };
}
