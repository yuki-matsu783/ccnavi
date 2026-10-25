/**
 * 実リポジトリ（ccnavi を配布済みのもの）を、模擬のホスト（`MockGitHub` / `MockGitLab`）の入力にする。開発用。
 *
 * ローカルブランチごとに「許可リストに入るファイルの内容」と「先頭のコミットの日付」を読む。
 * 読むのは `git for-each-ref`・`git ls-tree`・`git cat-file`・`git symbolic-ref` だけで、リポジトリには何も書かず、
 * git の書き込み系のコマンドも打たない。コミットの履歴と sha は持ち込まない（模擬が中身から 40 桁の sha を組む）。
 *
 * 載せるのは許可リストのパスだけ。拡張が読む・書くパス（チケットの置き場・承認済み・設定の層・互換のマーカー）に絞り、
 * `.env` や鍵、`.git/` は許可リストの外なので載せない。許可リストの下でも、秘密情報の名前のファイルは載せない。
 * `.claude/settings.json` は `env` のうち `CCNAVI_` で始まる名前だけを残して載せる（ほかの設定は秘密を含みうる）。
 * 1 ファイルと全体の大きさに上限があり、超えたら `FixtureLimitError` で止まる。バイナリ・リンク・サブモジュールは載せない。
 */
import { execFileSync } from "node:child_process";

import { NOW, type FixtureBranch } from "../fixtures/repo.js";

/** 1 ファイルの上限（バイト） */
export const MAX_FILE_BYTES = 256 * 1024;
/** 載せる全体の上限（ブランチごとの数え。バイト） */
export const MAX_TOTAL_BYTES = 8 * 1024 * 1024;

/** 載せる場所（接頭辞）。拡張が読む・書くパス（`src/core/guard.ts`・`py/ccnavi_chrome.py`）に合わせる */
export const ALLOW_PREFIXES: readonly string[] = ["wip/", ".ccnavi/approved/", ".ccnavi/config/common/", ".ccnavi/config/project/"];
/** 載せる 1 ファイル */
export const ALLOW_FILES: readonly string[] = [".ccnavi/scripts/ccnavi-common.sh", ".claude/settings.json"];
const SETTINGS = ".claude/settings.json";

/** 許可リストの中でも載せない名前（秘密情報になりやすいもの。大文字小文字は区別しない） */
const SECRET_NAME = /(^|\/)(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|id_(rsa|dsa|ecdsa|ed25519)|credentials?(\..*)?|secrets?(\..*)?|.*\.(pem|key|p12|pfx|keystore))$/i;

/** 上限を超えた。呼び手は終了コード 1 にする */
export class FixtureLimitError extends Error {}

export interface RepoFixture {
  readonly branches: Record<string, FixtureBranch>;
  readonly defaultBranch: string;
  /** 模擬へ渡す「いま」。日付をずらさない（`NOW` 基準のずらしを 0 にする）ために、模擬の基準の時刻をそのまま返す */
  readonly now: Date;
  /** 載せなかったものの数え（理由 → パスの一覧） */
  readonly skipped: Record<string, string[]>;
}

function git(repo: string, args: string[], encoding: BufferEncoding | "buffer" = "utf8"): string | Buffer {
  // 読み取りのコマンドだけを打つ（呼び元はこの関数に書き込み系を渡さない）
  const out = execFileSync("git", ["-C", repo, "-c", "core.quotepath=off", ...args], { maxBuffer: 256 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
  return encoding === "buffer" ? out : out.toString(encoding);
}

/** パスが許可リストに入るか（大文字小文字は区別しない） */
export function allowed(path: string): boolean {
  const p = path.toLowerCase();
  if (ALLOW_FILES.some((f) => p === f)) return true;
  return ALLOW_PREFIXES.some((prefix) => p.startsWith(prefix));
}

export function isSecretName(path: string): boolean {
  return SECRET_NAME.test(path);
}

/** `.claude/settings.json` を、`env` の `CCNAVI_` で始まる名前だけにして返す。読めなければ null（載せない） */
export function sanitizeSettings(text: string): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const env = (parsed as { env?: unknown }).env;
  const kept: Record<string, unknown> = {};
  if (typeof env === "object" && env !== null && !Array.isArray(env)) {
    for (const [k, v] of Object.entries(env)) if (/^CCNAVI_/.test(k) && typeof v === "string") kept[k] = v;
  }
  return JSON.stringify({ env: kept }, null, 2) + "\n";
}

function defaultBranchOf(repo: string, names: readonly string[]): string {
  const has = (n: string) => names.includes(n);
  try {
    const remote = (git(repo, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]) as string).trim().replace(/^origin\//, "");
    if (has(remote)) return remote;
  } catch {
    // origin/HEAD が無いリポジトリ（ローカルだけ）。次を見る
  }
  for (const n of ["main", "master"]) if (has(n)) return n;
  try {
    const head = (git(repo, ["symbolic-ref", "--short", "HEAD"]) as string).trim();
    if (has(head)) return head;
  } catch {
    // detached HEAD
  }
  throw new FixtureLimitError(`統合先にするブランチを決められない（main・master・origin/HEAD・HEAD のどれもローカルブランチに無い）: ${names.join(", ")}`);
}

export function repoToFixture(repo: string): RepoFixture {
  const refs = (git(repo, ["for-each-ref", "--format=%(refname:lstrip=2)%09%(committerdate:iso-strict)", "refs/heads"]) as string)
    .split("\n")
    .filter((l) => l !== "")
    .map((l) => {
      const [name, date] = l.split("\t");
      return { name, date: new Date(date).toISOString() };
    });
  if (refs.length === 0) throw new FixtureLimitError("ローカルブランチが無い");
  const skipped: Record<string, string[]> = {};
  const skip = (why: string, path: string) => (skipped[why] ??= []).push(path);
  const blobs = new Map<string, string | null>();
  const branches: Record<string, FixtureBranch> = {};
  for (const { name, date } of refs) {
    const files: Record<string, string> = {};
    let total = 0;
    const listing = git(repo, ["ls-tree", "-r", "-z", "--long", `refs/heads/${name}`]) as string;
    for (const entry of listing.split("\0")) {
      if (entry === "") continue;
      // <mode> SP <type> SP <sha> SP+ <size> TAB <path>
      const m = /^(\d+) (\w+) ([0-9a-f]+)\s+(\S+)\t(.*)$/s.exec(entry);
      if (!m) continue;
      const [, mode, type, sha, size, path] = m;
      if (!allowed(path)) continue;
      if (type !== "blob" || mode === "120000") {
        skip("blob 以外（リンク・サブモジュール）", path);
        continue;
      }
      if (isSecretName(path)) {
        skip("秘密情報の名前", path);
        continue;
      }
      if (Number(size) > MAX_FILE_BYTES) throw new FixtureLimitError(`${name}:${path} が ${size} バイトで、1 ファイルの上限 ${MAX_FILE_BYTES} を超えた`);
      let text = blobs.get(sha);
      if (text === undefined) {
        const buf = git(repo, ["cat-file", "blob", sha], "buffer") as Buffer;
        text = buf.includes(0) ? null : buf.toString("utf8");
        blobs.set(sha, text);
      }
      if (text === null) {
        skip("バイナリ", path);
        continue;
      }
      const body = path.toLowerCase() === SETTINGS ? sanitizeSettings(text) : text;
      if (body === null) {
        skip("読めない設定", path);
        continue;
      }
      total += Buffer.byteLength(body);
      if (total > MAX_TOTAL_BYTES) throw new FixtureLimitError(`${name} に載せるファイルが全体の上限 ${MAX_TOTAL_BYTES} バイトを超えた（${path} で）`);
      files[path] = body;
    }
    branches[name] = { committedDate: date, files };
  }
  return { branches, defaultBranch: defaultBranchOf(repo, refs.map((r) => r.name)), now: new Date(NOW), skipped };
}
