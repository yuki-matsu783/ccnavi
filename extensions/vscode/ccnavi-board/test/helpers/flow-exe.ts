/**
 * 実行ファイル（`ccnavi --lint --json --flow <パス>`）にフローを読ませる。案内の見本が、実際の読み取りと
 * 実行ファイルの答え（`(flow)`のwarn・担当に渡る手順）と同じかを見るテストと、画面が書き出す本文を
 * 実行ファイルのYAMLの読み手で読み戻すテストが使う。
 *
 * 実行ファイルは、リポジトリの`go/`から一時の置き場へ組み立てる（`go build ./cmd/ccnavi`）。組み立てられない
 * 環境では`reason`に理由を入れ、呼ぶテストは飛ばす。1つのファイルの中では組み立てた結果を使い回す。
 * このファイルは`out/test/helpers/`から走るので、リポジトリのルートは6階層上。
 */
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..", "..", "..");

export interface ExeRead {
  /** 実行ファイルが読んだ中身（`flow.data`）。読めなければnull */
  readonly data: unknown;
  /** `(flow)`の苦情。一時ファイルのパスは`shown`に直してある */
  readonly problems: readonly { readonly severity: string; readonly detail: string }[];
  readonly rendered: readonly string[] | null;
}

export type ExeRunner =
  | {
      readonly ok: true;
      /** `body`を一時ファイルに書いて読ませる。`root`を渡すとそこをワークスペースにする（既定はリポジトリのルート） */
      readonly read: (body: string | Uint8Array, shown: string, root?: string) => ExeRead;
    }
  | { readonly ok: false; readonly reason: string };

function runner(): ExeRunner {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-flow-exe-"));
  // 組み立てた実行ファイルは大きいので、テストの終わりに消す
  process.on("exit", () => fs.rmSync(dir, { recursive: true, force: true }));
  const exe = path.join(dir, process.platform === "win32" ? "ccnavi.exe" : "ccnavi");
  const built = spawnSync("go", ["build", "-o", exe, "./cmd/ccnavi"], { cwd: path.join(REPO_ROOT, "go"), encoding: "utf8" });
  if (built.status !== 0) {
    const why = built.error?.message ?? built.stderr.split("\n").filter(Boolean).pop() ?? `終了${String(built.status)}`;
    return { ok: false, reason: `実行ファイル（go build ./cmd/ccnavi）を組み立てられないので飛ばす（${why}）` };
  }
  let count = 0;
  return {
    ok: true,
    read: (body, shown, root = REPO_ROOT) => {
      count += 1;
      const file = path.join(dir, `flow-${count}.yml`);
      fs.writeFileSync(file, body);
      const ran = spawnSync(exe, ["--root", root, "--lint", "--json", "--flow", file], {
        cwd: root,
        encoding: "utf8",
        maxBuffer: 64 * 1024 * 1024,
      });
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

let cached: ExeRunner | undefined;

/** 実行ファイルに読ませる道具。組み立てられなければ理由を持つ */
export function exeRunner(): ExeRunner {
  cached ??= runner();
  return cached;
}
