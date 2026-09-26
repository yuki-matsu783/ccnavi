/**
 * フローの本文が正しいかを実行ファイルに聞く（ADR-0035・ADR-0085）。フロー編集画面の、開くときと保存の前。
 *
 * 読めるか（大きさ・UTF-8 として読めるか・YAML として読めるか・別名）と形（`nodes` が無い、`id` が無い・重なる など）の答えは
 * 実行ファイルの `--lint --json --flow <パス>` が出す。読み手も検査も SubagentStart と同じもの
 * （`ccnavi/flow.py` の `load`）で、拡張は自分で判定し直さない。ルール設定・リスク管理・フェーズ管理の
 * 画面が `--lint --rules` / `--risk` / `--phases` に一時ファイルで聞くのと同じ形。
 *
 * 一時ファイルは画面ごとの一時ディレクトリ（`os.tmpdir()` の下の `ccnavi-flow-*`）に、**呼ぶたびに別の名前**
 * （`flow-<番号>-<乱数>.yml`）で `wx` で書き、確かめ終わったら消す。同じ画面で開くときと保存が重なっても
 * 互いの本文を読み違えない。苦情は一時ファイルのパスを名乗るので、画面に出すときは対象のファイルの綴りに直す。
 *
 * 通ったときは、実行ファイルが読んだ中身（`flow.data`）を返す。画面はそれを自分の中身と見比べる
 * （`flow-agree.ts`）。答えに `flow` が無ければ、実行ファイルが本当にフローを見たか分からないので通さない
 * （`--flow` を知らない古い実行ファイルと同じ扱い）。
 *
 * VS Code の API は使わない（単体テストで確かめる。実行ファイルの答えは偽物を渡す）。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

import { problemsOfFlow, type LintJson } from "./lintmodel.js";

/** 実行ファイルを走らせた結果（`ccnavi.ts` の `RunResult<LintJson>` と同じ形） */
export type FlowLintRun = { readonly ok: true; readonly value: LintJson } | { readonly ok: false; readonly error: string };

/** 通ったときは、実行ファイルが読んだ中身（`flow.as_json` の形） */
export type FlowLintVerdict = { readonly ok: true; readonly data: unknown } | { readonly ok: false; readonly error: string };

/** 一時ファイルの名前の頭 */
export const FLOW_TEMP_PREFIX = "flow-";

let counter = 0;

function tempName(): string {
  counter += 1;
  return `${FLOW_TEMP_PREFIX}${process.pid}-${counter}-${crypto.randomBytes(6).toString("hex")}.yml`;
}

/**
 * 本文を一時ファイルに書いて実行ファイルに確かめさせる。`(flow)` の error が 1 件でもあれば理由を返す。
 * ほかの設定の苦情（ルールなど）では止めない。実行ファイルを走らせられない・答えを読めない・答えに
 * 読んだ中身（`flow`）が無いときも止める側（確かめられないものを正しいとは言わない）。
 * 本文はバイト列でも渡せる（開くときは読んだバイトのまま渡し、UTF-8 として壊れているかも実行ファイルに言わせる）。
 */
export async function lintFlowText(
  text: string | Uint8Array,
  tmpDir: string,
  shown: string,
  lint: (file: string) => Promise<FlowLintRun>,
): Promise<FlowLintVerdict> {
  const tmp = path.join(tmpDir, tempName());
  try {
    try {
      fs.writeFileSync(tmp, text, typeof text === "string" ? { encoding: "utf8", flag: "wx" } : { flag: "wx" });
    } catch (error) {
      return { ok: false, error: `確かめるための一時ファイルを書けない: ${(error as Error).message}` };
    }
    const ran = await lint(tmp);
    if (!ran.ok) {
      return { ok: false, error: ran.error };
    }
    const errors = problemsOfFlow(ran.value).filter((p) => p.severity === "error");
    if (errors.length > 0) {
      // 苦情は渡した一時ファイルのパスを名乗るので、対象のファイルの綴りに直す
      const said = errors.map((p) => p.detail.split(tmp).join(shown)).join("\n");
      return { ok: false, error: `実行ファイル（--lint --flow）が読めないと言った: ${said}` };
    }
    const flow = ran.value.flow;
    if (flow === undefined || flow.data === null || flow.data === undefined) {
      return {
        ok: false,
        error: "実行ファイル（--lint --flow）が読んだ中身（flow）を返さない（古い）。確かめられないので進めない。実行ファイルを新しくする",
      };
    }
    return { ok: true, data: flow.data };
  } finally {
    try {
      fs.rmSync(tmp, { force: true });
    } catch {
      // 消せなければ画面ごとの一時ディレクトリに残り、画面を閉じるときに消える
    }
  }
}
