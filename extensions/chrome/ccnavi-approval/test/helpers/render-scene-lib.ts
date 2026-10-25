/**
 * `scripts/render-scene.js`（実リポジトリの状態を模擬ホストに載せて Chromium に描かせる開発用ツール）の部品。
 * 引数の解釈・出力先の判定・失敗の条件・ログの整形。ブラウザも通信も持たないので、単体で試験できる。
 */
import fs from "node:fs";
import path from "node:path";

export const EXIT = { OK: 0, FAILED: 1, USAGE: 2, NOT_READY: 3 } as const;

export const DEFAULT_WIDTH = 1400;
export const MIN_WIDTH = 320;
export const MAX_WIDTH = 3000;
export type HostKind = "github" | "gitlab";

export interface Options {
  readonly repo: string;
  readonly out: string;
  readonly host: HostKind;
  readonly width: number;
}

export const USAGE = [
  "使い方: node scripts/render-scene.js --repo <リポジトリ> --out <出力ディレクトリ> [--host github|gitlab] [--width 1400]",
  "  --repo   ccnavi を配布済みのリポジトリ。ローカルブランチごとの許可リストのファイルを模擬ホストに載せる（読むだけ）",
  "  --out    出力ディレクトリ（無ければ作る。リポジトリの下は不可）",
  "  --host   模擬にするホスト。既定は github",
  `  --width  ページの幅（${MIN_WIDTH}〜${MAX_WIDTH} の整数。既定 ${DEFAULT_WIDTH}。高さは全体が入るまで伸ばす）`,
].join("\n");

/** 終了コードを持つ失敗。呼び手が理由を 1 行出して、その終了コードで終わる */
export class ToolError extends Error {
  constructor(
    readonly code: number,
    message: string,
  ) {
    super(message);
  }
}

/** 引数を解く。誤りは `{ ok: false, error }`（終了コード 2） */
export function parseArgs(argv: readonly string[]): { ok: true; options: Options } | { ok: false; error: string } {
  const valued: Record<string, "repo" | "out" | "host" | "width"> = { "--repo": "repo", "--out": "out", "--host": "host", "--width": "width" };
  const got: Partial<Record<"repo" | "out" | "host" | "width", string>> = {};
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const key = valued[flag];
    if (key === undefined) return { ok: false, error: `不明な引数: ${flag}` };
    if (key in got) return { ok: false, error: `${flag} が 2 度指定されている` };
    const value = argv[i + 1];
    if (value === undefined || value === "" || value.startsWith("--")) return { ok: false, error: `${flag} に値が要る` };
    got[key] = value;
    i += 1;
  }
  if (got.repo === undefined) return { ok: false, error: "--repo が要る" };
  if (got.out === undefined) return { ok: false, error: "--out が要る" };
  const host = got.host ?? "github";
  if (host !== "github" && host !== "gitlab") return { ok: false, error: `--host は github|gitlab: ${host}` };
  let width = DEFAULT_WIDTH;
  if (got.width !== undefined) {
    if (!/^[0-9]+$/.test(got.width)) return { ok: false, error: `--width は整数（${MIN_WIDTH}〜${MAX_WIDTH}）: ${got.width}` };
    width = Number(got.width);
    if (width < MIN_WIDTH || width > MAX_WIDTH) return { ok: false, error: `--width は ${MIN_WIDTH}〜${MAX_WIDTH}: ${got.width}` };
  }
  return { ok: true, options: { repo: got.repo, out: got.out, host, width } };
}

/**
 * パスを比べるための形。大文字小文字だけ違うパスは同じものとして扱う（どの機械でも区別しない）。
 * VS Code 拡張の `foldPath`（`handoffmodel.ts`）と同じ規則。表示や書き戻しには使わない。
 */
export function foldPath(p: string): string {
  return p.normalize("NFC").toLowerCase();
}

/** 存在する一番深い祖先を実体のパスにそろえ、残りはそのまま付ける（出力先はまだ無いことがある） */
export function realPathOf(target: string): string {
  const absolute = path.resolve(target);
  const rest: string[] = [];
  let current = absolute;
  for (;;) {
    try {
      return path.join(fs.realpathSync(current), ...rest);
    } catch {
      const parent = path.dirname(current);
      if (parent === current) return absolute;
      rest.unshift(path.basename(current));
      current = parent;
    }
  }
}

/** `child` が `parent` と同じか、その下か。実体にしてから `foldPath` を通し、区切りは `/` にそろえて比べる */
export function isInside(child: string, parent: string): boolean {
  const norm = (p: string) => foldPath(realPathOf(p).replace(/\\/g, "/")).replace(/\/+$/, "");
  const c = norm(child);
  const p = norm(parent);
  return c === p || c.startsWith(`${p}/`);
}

/** ページの記録。`kind` で出どころを示す */
export interface Entry {
  readonly kind: "console" | "pageerror" | "worker-console" | "worker-error";
  readonly type?: string;
  readonly text: string;
  readonly url?: string;
}

/** 数えない `error`（favicon の取得失敗） */
function ignorable(entry: Entry): boolean {
  return /favicon/i.test(entry.text) || /favicon/i.test(entry.url ?? "");
}

/** 失敗の条件に当たる記録（ページエラー・コンソールの error・service worker の error） */
export function failuresOf(entries: readonly Entry[]): Entry[] {
  return entries.filter((e) => {
    if (e.kind === "pageerror" || e.kind === "worker-error") return true;
    return (e.kind === "console" || e.kind === "worker-console") && e.type === "error" && !ignorable(e);
  });
}

/** 文字列から PAT を消す（ログと失敗の文面に出さないため） */
export function redact(text: string, secrets: readonly string[]): string {
  let out = text;
  for (const s of secrets) if (s !== "") out = out.split(s).join("[PAT]");
  return out.replace(/(authorization:?\s*)(bearer|token)\s+\S+/gi, "$1$2 [PAT]").replace(/private-token:?\s*\S+/gi, "private-token: [PAT]");
}

/** board.log の 1 行 */
export function logLine(entry: Entry, secrets: readonly string[] = []): string {
  const text = redact(entry.text, secrets).replace(/\r?\n/g, "\\n");
  if (entry.kind === "console" || entry.kind === "worker-console") return `[${entry.kind === "console" ? "page" : "worker"}.${entry.type ?? "log"}] ${text}`;
  return `[${entry.kind}] ${text}`;
}
