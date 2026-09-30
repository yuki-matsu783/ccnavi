/**
 * Chrome からの承認と承認の取り下げ（ADR-0093 の段階 3。8.3・8.4・8.8）。
 *
 * 判定は Python（同梱の ccnavi）が出し、ここは Python が返した書くもの（Changes）を
 * 親のブランチ `P` への 1 コミットにして送るだけ（ADR-0035 の改訂の範囲）。流れは 8.3 のとおり。
 *
 * 1. preview: ボードが見せた画面の指紋（`digest`）と一覧（`ids`）を持っている
 * 2. yes: 家族 1 つぶんを読み直して Snapshot を組み直し、Python に見せたものと比べさせる。
 *    違えば書かずに「見直す」（preview からやり直し）
 * 3. `plan` の変更を `createCommitOnBranch` の 1 コミットで書く。条件は `expectedHeadOid` = 読んだ `P` の先頭
 * 4. 先頭が動いていたら（D20）、新しい先頭で Snapshot を組み直して判定と plan を必ずやり直す。
 *    指紋が同じなら新しい plan を書く（見せ直さない）。違えば「見直す」。3 周しても書けなければ人に回す
 * 5. 書いた後、新しい先頭の中身が書いたとおりか確かめる（blob の sha を突き合わせる）
 *
 * 取り下げも同じ流れで書く。見せた指紋は無いが、毎周 Python が条件（8.8）を見直す。
 */
import { py, PyError, type Actor, type ChangeRow, type PlanResult, type Written } from "./py.js";
import type { RepoConfig } from "./settings.js";
import { readFamily, type Deps as ReadDeps, type FamilyRead } from "./snapshot.js";
import type { ApprovalCommit, BlobText, FileAddition, PathObject } from "./github.js";

/** 先頭が動いたときに読み直して書き直す回数の上限（8.3 の 4） */
export const MAX_ROUNDS = 3;

export interface WriteDeps extends ReadDeps {
  /** 拡張の版（跡の `version` とコミットの見出し。7.3） */
  readonly version: string;
}

export type Outcome =
  | { readonly kind: "written"; readonly oid: string; readonly rounds: number; readonly lines: readonly string[] }
  | { readonly kind: "changed"; readonly message: string }
  | { readonly kind: "refused"; readonly message: string }
  | { readonly kind: "conflict"; readonly message: string }
  | { readonly kind: "failed"; readonly message: string };

export interface Shown {
  readonly ids: readonly string[];
  readonly digest: string;
  /** 指紋を出したときの絞り（ボードの答えの `only`） */
  readonly only: readonly string[] | null;
}

/** `fsio.stamp` と同じ形の今の時刻（現地時刻とオフセット。`2026-09-30T12:00:00+0900`） */
export function localStamp(now: Date): string {
  const pad = (n: number, w = 2) => String(Math.abs(n)).padStart(w, "0");
  const off = -now.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  return (
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T` +
    `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}` +
    `${sign}${pad(Math.floor(Math.abs(off) / 60))}${pad(Math.abs(off) % 60)}`
  );
}

/** 本文（UTF-8）か base64 の中身をバイト列にする */
export function rowBytes(row: ChangeRow): Uint8Array {
  if (typeof row.base64 === "string") {
    const bin = atob(row.base64);
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  }
  return new TextEncoder().encode(row.content ?? "");
}

function base64(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** Changes の 1 ブランチぶんを、1 コミットの足す・消すに分ける（8.4） */
export function fileChanges(rows: readonly ChangeRow[]): { additions: FileAddition[]; deletions: string[] } {
  const additions: FileAddition[] = [];
  const deletions: string[] = [];
  for (const row of rows) {
    if (row.op === "delete") deletions.push(row.path);
    else additions.push({ path: row.path, contents: base64(rowBytes(row)) });
  }
  return { additions, deletions };
}

/** git の blob の sha（`blob <大きさ>\0<中身>` の SHA-1） */
export async function blobSha(bytes: Uint8Array): Promise<string> {
  const head = new TextEncoder().encode(`blob ${bytes.length}\0`);
  const buf = new Uint8Array(head.length + bytes.length);
  buf.set(head);
  buf.set(bytes, head.length);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-1", buf));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 書いた後の確かめ: `oid` の上の各パスが書いたとおりか。違うパスの並びを返す（空なら合っている） */
export async function verifyWritten(deps: ReadDeps, repo: RepoConfig, oid: string, rows: readonly ChangeRow[]): Promise<string[]> {
  const wrong: string[] = [];
  for (let i = 0; i < rows.length; i += 20) {
    const part = rows.slice(i, i + 20);
    const objs = (await deps.call("pathObjects", [repo.owner, repo.repo, oid, part.map((r) => r.path)])) as Record<string, PathObject | null>;
    for (const row of part) {
      const obj = objs[row.path];
      if (row.op === "delete") {
        if (obj !== null && obj !== undefined) wrong.push(row.path);
      } else if (!obj || obj.type !== "blob" || obj.oid !== (await blobSha(rowBytes(row)))) {
        wrong.push(row.path);
      }
    }
  }
  return wrong;
}

async function actorOf(deps: WriteDeps, repo: RepoConfig): Promise<Actor> {
  const login = (await deps.call("viewer", [repo.owner, repo.repo])) as string;
  return { account: login, version: deps.version };
}

function why(res: PlanResult): string {
  const lines = [res.refused, ...res.rejected.map((r) => `${r.ticket}: ${r.problems.join(" / ")}`)].filter((s) => s);
  return lines.join("\n") || "承認するものが無い";
}

type Attempt = { kind: "written"; oid: string } | { kind: "moved" } | { kind: "failed"; message: string };

/**
 * 1 コミットで書く。落ちたら先頭を読み直し、動いていれば「動いた」（やり直す）。
 * ただし動いた先が自分の書いたとおりなら（応答だけが落ちた）、書けたとする。
 */
async function commitOnce(
  deps: WriteDeps,
  repo: RepoConfig,
  read: FamilyRead,
  family: string,
  headline: string,
  rows: readonly ChangeRow[],
): Promise<Attempt> {
  const { additions, deletions } = fileChanges(rows);
  try {
    const oid = (await deps.call("commit", [
      repo.owner,
      repo.repo,
      family,
      read.head,
      headline,
      additions,
      deletions,
      read.integration.name,
    ])) as string;
    return { kind: "written", oid };
  } catch (err) {
    const message = (err as Error).message ?? String(err);
    const now = (await deps.call("branchHead", [repo.owner, repo.repo, family])) as string | null;
    if (now === null) return { kind: "failed", message: `親のブランチ ${family} がホストから消えた（${message}）` };
    if (now === read.head) return { kind: "failed", message };
    if ((await verifyWritten(deps, repo, now, rows)).length === 0) return { kind: "written", oid: now };
    return { kind: "moved" };
  }
}

async function finish(deps: WriteDeps, repo: RepoConfig, oid: string, rows: readonly ChangeRow[], rounds: number, lines: readonly string[]): Promise<Outcome> {
  const wrong = await verifyWritten(deps, repo, oid, rows);
  if (wrong.length > 0) {
    return { kind: "failed", message: `書いた後の中身が書いたものと違う（${wrong.join(", ")}）。ホストの履歴を人が確かめる` };
  }
  return { kind: "written", oid, rounds, lines };
}

function rowsOf(res: Written, family: string): readonly ChangeRow[] | string {
  if (res.stopped) return `${res.stopped.ticket}: ${res.stopped.reason}`;
  const names = Object.keys(res.changes ?? {});
  if (names.some((n) => n !== family)) return `書くものが親のブランチ ${family} の外に及ぶ（${names.join(", ")}）`;
  const rows = res.changes?.[family] ?? [];
  return rows.length === 0 ? "書くものが無い" : rows;
}

/** 家族 1 つの承認待ちを承認する（8.3） */
export async function approveFamily(repo: RepoConfig, family: string, shown: Shown, deps: WriteDeps): Promise<Outcome> {
  try {
    const actor = await actorOf(deps, repo);
    for (let round = 1; round <= MAX_ROUNDS; round += 1) {
      const read = await readFamily(repo, family, deps);
      const res = await py.plan(deps.py, {
        settings: read.settings,
        snapshot: read.input,
        family,
        only: shown.only,
        shown: { ids: shown.ids, digest: shown.digest },
        stamp: localStamp(deps.now()),
        actor,
      });
      if (res.mismatch) {
        return { kind: "changed", message: "見せた後に承認待ちの中身が変わった。見直してから承認し直す" };
      }
      if (!res.changes) return { kind: "refused", message: why(res) };
      const rows = rowsOf(res, family);
      if (typeof rows === "string") return { kind: "refused", message: rows };
      const headline = `ccnavi: ${res.identifiers.join(", ")} を承認（Chrome 拡張 ${deps.version}）`;
      const done = await commitOnce(deps, repo, read, family, headline, rows);
      if (done.kind === "written") return await finish(deps, repo, done.oid, rows, round, res.lines);
      if (done.kind === "failed") return { kind: "failed", message: done.message };
    }
    return { kind: "conflict", message: `書く間に ${family} が ${MAX_ROUNDS} 回動いた。少し待ってからボードを更新して承認し直す` };
  } catch (err) {
    return { kind: err instanceof PyError ? "refused" : "failed", message: (err as Error).message ?? String(err) };
  }
}

/** 承認コミットの親にあった提案の本文（8.8 の 2）。引けなければ null */
async function priorProposal(deps: WriteDeps, repo: RepoConfig, read: FamilyRead, ident: string): Promise<string | null> {
  const doing = `${read.place.approved}/doing/${ident}.md`;
  const todo = `${read.place.tickets}/todo/${ident}.md`;
  const found = (await deps.call("approvalCommit", [repo.owner, repo.repo, read.head, doing])) as ApprovalCommit | null;
  if (found === null) return null;
  const objs = (await deps.call("pathObjects", [repo.owner, repo.repo, found.parent, [todo]])) as Record<string, PathObject | null>;
  const obj = objs[todo];
  if (!obj || obj.type !== "blob") return null;
  const texts = (await deps.call("blobs", [repo.owner, repo.repo, [obj.oid]])) as Record<string, BlobText>;
  const blob = texts[obj.oid];
  return blob && !blob.binary && typeof blob.text === "string" ? blob.text : null;
}

/** 承認を取り下げる（8.8）。着手前の新規の承認だけ。元の提案は承認コミットの親から戻す */
export async function withdrawTicket(repo: RepoConfig, family: string, ident: string, reason: string, deps: WriteDeps): Promise<Outcome> {
  try {
    const actor = await actorOf(deps, repo);
    for (let round = 1; round <= MAX_ROUNDS; round += 1) {
      const read = await readFamily(repo, family, deps);
      const prior = await priorProposal(deps, repo, read, ident);
      const res = await py.withdraw(deps.py, {
        settings: read.settings,
        snapshot: read.input,
        family,
        ids: [ident],
        prior: prior === null ? {} : { [ident]: prior },
        reason,
        stamp: localStamp(deps.now()),
        actor,
      });
      if (res.problems.length > 0 || !res.changes) {
        return { kind: "refused", message: res.problems.join("\n") || "取り下げるものが無い" };
      }
      const rows = rowsOf(res, family);
      if (typeof rows === "string") return { kind: "refused", message: rows };
      const headline = `ccnavi: ${ident} の承認を取り下げ（Chrome 拡張 ${deps.version}）`;
      const done = await commitOnce(deps, repo, read, family, headline, rows);
      if (done.kind === "written") return await finish(deps, repo, done.oid, rows, round, res.lines);
      if (done.kind === "failed") return { kind: "failed", message: done.message };
    }
    return { kind: "conflict", message: `書く間に ${family} が ${MAX_ROUNDS} 回動いた。少し待ってからボードを更新して取り下げ直す` };
  } catch (err) {
    return { kind: err instanceof PyError ? "refused" : "failed", message: (err as Error).message ?? String(err) };
  }
}
