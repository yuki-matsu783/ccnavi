/**
 * 実行ファイル（`ccnavi --lint --json --flow <パス>`）にフローを読ませる。案内の見本が、実際の読み取りと
 * 実行ファイルの答え（`(flow)` の warn・担当に渡る手順）と同じかを見るテストが使う。
 *
 * 起こすのはリポジトリのルートで `uv run python -m ccnavi`（無ければ `python3 -m ccnavi`）。どちらも起こせない環境では
 * `reason` に理由を入れ、呼ぶテストは飛ばす。1 つのファイルの中では起こした結果を使い回す（起動に時間がかかる）。
 * このファイルは `out/test/helpers/` から走るので、リポジトリのルートは 6 段上。
 */
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { serializeFlow, type FlowDoc } from "../../src/core/flow-doc.js";

const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..", "..", "..");
const PACKAGE_ROOT = path.join(REPO_ROOT, "src");

export interface ExeRead {
  /** 実行ファイルが読んだ中身（`flow.data`） */
  readonly data: unknown;
  /** `(flow)` の苦情。一時ファイルのパスは `shown` に直してある */
  readonly problems: readonly { readonly severity: string; readonly detail: string }[];
  readonly rendered: readonly string[] | null;
}

export type ExeRunner = { readonly ok: true; readonly read: (doc: FlowDoc, shown: string) => ExeRead } | { readonly ok: false; readonly reason: string };

function runner(): ExeRunner {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-flow-exe-"));
  const said: string[] = [];
  const tries: readonly (readonly string[])[] = [
    ["uv", "run", "python", "-m", "ccnavi"],
    ["python3", "-m", "ccnavi"],
  ];
  for (const [command, ...head] of tries) {
    const run = (file: string) =>
      spawnSync(command, [...head, "--lint", "--json", "--flow", file], {
        cwd: REPO_ROOT,
        encoding: "utf8",
        maxBuffer: 64 * 1024 * 1024,
        env: { ...process.env, PYTHONPATH: PACKAGE_ROOT, PYTHONUTF8: "1" },
      });
    const probeFile = path.join(dir, "probe.yml");
    fs.writeFileSync(probeFile, "id: probe\nnodes: []\nconnections: []\n");
    const probe = run(probeFile);
    let parsed: { flow?: unknown } | undefined;
    try {
      parsed = JSON.parse(probe.stdout) as { flow?: unknown };
    } catch {
      parsed = undefined;
    }
    if (parsed?.flow === undefined) {
      said.push(`${command}: ${probe.error?.message ?? probe.stderr.split("\n").filter(Boolean).pop() ?? `終了 ${String(probe.status)}`}`);
      continue;
    }
    let count = 0;
    return {
      ok: true,
      read: (doc, shown) => {
        count += 1;
        const file = path.join(dir, `flow-${count}.yml`);
        fs.writeFileSync(file, serializeFlow(doc), "utf8");
        const ran = run(file);
        const out = JSON.parse(ran.stdout) as {
          problems: { severity: string; where: string; detail: string }[];
          flow: { data: unknown; rendered: string[] | null };
        };
        return {
          data: out.flow.data,
          problems: out.problems.filter((p) => p.where === "(flow)").map((p) => ({ severity: p.severity, detail: p.detail.split(file).join(shown) })),
          rendered: out.flow.rendered,
        };
      },
    };
  }
  return { ok: false, reason: `実行ファイル（ccnavi --lint --flow）を起こせないので飛ばす（${said.join(" / ")}）` };
}

let cached: ExeRunner | undefined;

/** 実行ファイルに読ませる道具。起こせなければ理由を持つ */
export function exeRunner(): ExeRunner {
  cached ??= runner();
  return cached;
}
