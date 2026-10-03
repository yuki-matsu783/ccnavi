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
 *    指紋が同じなら新しい plan を書く（見せ直さない）。違えば「見直す」。3 周しても書けなければユーザに回す
 * 5. 書いた後、新しい先頭の中身が書いたとおりか確かめる（blob の sha を突き合わせる）
 *
 * 取り下げも同じ流れで書く。見せた指紋は無いが、毎周 Python が条件（8.8）を見直す。
 * レビュー済み（段階 4。8.9）も同じ流れで、毎周 MR のスレッドとレビューを読み直して Python の `confirm`
 * （手元の `ccnavi review confirm` と同じコア）に通るかを決めさせる。
 *
 * 段階 5 から GitLab にも書く。GitLab の Commits API には比較つきの書き込みが無い（8.4）ので、1 段目は事後確認と
 * 打ち消しだけ（D21）: 書いた答えのコミットの親が読んだ先頭と違えば（間に別の書き込みが入った）、自分の書き込みの
 * 直前の姿で判定し直す。同じ書くものになれば、そのまま残す。違えば（結論が変わった）打ち消しのコミットを積み、
 * 新しい先頭から読み直して周を回す。打ち消しもさらに競合して 2 回で収まらなければ、止めてユーザに回す（`attention`）。
 * `seq`（2 段目）は書かない。この段では、取り下げと子の承認が同時に通ったとき「親の無い子」が一時的に残りうる（8.4）。
 */
import { py, PyError, type Actor, type ChangeRow, type PlanResult, type Written } from "./py.js";
import type { RepoConfig } from "./settings.js";
import { findPrior, MovedError, readFamily, type Deps as ReadDeps, type FamilyRead } from "./snapshot.js";
import type { BlobText, FileAddition, PathObject, ReviewCopy } from "./github.js";
import { askConfirm } from "./reviewed.js";
import { underPlaces } from "./guard.js";
import { localStamp } from "./stamp.js";

/** 先頭が動いたときに読み直して書き直す回数の上限（8.3 の 4） */
export const MAX_ROUNDS = 3;

export interface WriteDeps extends ReadDeps {
  /** ホストの種類（応答が落ちた書き込みの見分け方が違う。無ければ github） */
  readonly kind?: "github" | "gitlab";
  /** 拡張の版（跡の `version` とコミットの見出し。7.3） */
  readonly version: string;
  /** 待つ（書いた直後の読み取りの遅れ）。無ければ本物の時計。試験は 0 秒にする */
  readonly sleep?: (ms: number) => Promise<void>;
}

/** 書いた直後の確かめを読み直す回数と間隔（ホストの読み取りの遅れ。レビューの 13） */
const VERIFY_TRIES = 3;
const VERIFY_WAIT_MS = 1000;
/** 見出しに並べる識別子の数。残りは件数にまとめ、全件は本文に書く（レビューの 3） */
const HEADLINE_IDS = 3;

/** service worker が書く頼みを形や守りで断ったときの status（protocol.ts の REFUSED と同じ） */
const REFUSED = 400;
/** GitLab が書き込みを断ったとき（`last_commit_id` が違う＝同じファイルを他人が変えた・無い）の status（`gitlab.HOST_REFUSED`）。ユーザに回す */
const HOST_REFUSED = 409;
/** GitLab へ送る前の確認で先頭が動いていたときの status（`gitlab.HOST_MOVED`）。何も送っていないので読み直して試し直す */
const HOST_MOVED = 412;

/** コミットの見出しと本文。見出しは 200 字に収まるよう先頭の数件と件数にまとめ、全件は本文に書く */
export function commitMessage(ids: readonly string[], verb: string, done: string, version: string): { headline: string; body: string } {
  const shown = ids.slice(0, HEADLINE_IDS).join(", ");
  const rest = ids.length > HEADLINE_IDS ? ` ほか ${ids.length - HEADLINE_IDS} 件` : "";
  const headline = `ccnavi: ${shown}${rest} ${verb}（Chrome 拡張 ${version}）`;
  const body = ids.length > HEADLINE_IDS ? `${done}もの（${ids.length} 件）:\n${ids.map((i) => `- ${i}`).join("\n")}\n` : "";
  return { headline, body };
}

export type Outcome =
  | { readonly kind: "written"; readonly oid: string; readonly rounds: number; readonly lines: readonly string[] }
  | { readonly kind: "changed"; readonly message: string }
  | { readonly kind: "refused"; readonly message: string }
  | { readonly kind: "conflict"; readonly message: string }
  | { readonly kind: "failed"; readonly message: string }
  /** ユーザに回す（打ち消しが収まらない・書いたか確かめられない・書いた後の中身が違う）。ボードは家族を「要確認」で出す（8.4） */
  | { readonly kind: "attention"; readonly message: string };

export interface Shown {
  readonly ids: readonly string[];
  readonly digest: string;
  /** 指紋を出したときの絞り（ボードの答えの `only`） */
  readonly only: readonly string[] | null;
}

export { localStamp } from "./stamp.js";

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

/** 書くもの 1 つ（足す）。`op` は GitLab の Commits API の action（作るか書き換えるか）。GitHub は使わない */
export interface Addition extends FileAddition {
  readonly op: "create" | "update";
}

/** Changes の 1 ブランチぶんを、1 コミットの足す・消すに分ける（8.4） */
export function fileChanges(rows: readonly ChangeRow[]): { additions: Addition[]; deletions: string[] } {
  const additions: Addition[] = [];
  const deletions: string[] = [];
  for (const row of rows) {
    if (row.op === "delete") deletions.push(row.path);
    else additions.push({ op: row.op, path: row.path, contents: base64(rowBytes(row)) });
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

/** 書いた後の確かめ。ホストの読み取りの遅れを見込み、合わなければ少し待って読み直す */
async function verifySettled(deps: WriteDeps, repo: RepoConfig, oid: string, rows: readonly ChangeRow[]): Promise<string[]> {
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  let wrong = await verifyWritten(deps, repo, oid, rows);
  for (let i = 1; i < VERIFY_TRIES && wrong.length > 0; i += 1) {
    await sleep(VERIFY_WAIT_MS);
    wrong = await verifyWritten(deps, repo, oid, rows);
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

/** service worker の `commit` の答え。`parent` はホストが実際に積んだ先 */
interface Committed {
  readonly oid: string;
  readonly parent: string;
}

type Attempt =
  | { kind: "written"; oid: string; parent: string }
  | { kind: "moved" }
  | { kind: "failed"; message: string }
  | { kind: "attention"; message: string };

/**
 * 1 コミットを送る。答えは書いたコミットと、その親。`last` を渡すと、書き換える・消す各ファイルに
 * 「最後に変えたコミット」として付ける（GitLab。打ち消しは自分のコミットを渡す。決定 A）。
 * 渡さなければ service worker が読んだ先頭の上の値を引いて付ける
 */
async function send(
  deps: WriteDeps,
  repo: RepoConfig,
  family: string,
  expected: string,
  message: { headline: string; body: string },
  rows: readonly ChangeRow[],
  last?: string,
): Promise<Committed> {
  const { additions, deletions } = fileChanges(rows);
  const adds = last ? additions.map((x) => (x.op === "update" ? { ...x, last } : x)) : additions;
  const dels = last ? deletions.map((p) => ({ path: p, last })) : deletions;
  return (await deps.call("commit", [repo.owner, repo.repo, family, expected, message.headline, message.body, adds, dels])) as Committed;
}

/** 遡る最初の親の数の上限（応答が落ちた書き込みを探す） */
const FIND_DEPTH = 20;

/**
 * 応答が落ちた書き込みを、今の先頭から最初の親を遡って探す（11.9.1 の 1）。見分け方は「書いた中身と親の組」:
 * そのコミットの上で書いた各パスが書いたとおりで、親の上ではそうでない（この書き込みで変わった）。
 * GitHub は `expectedHeadOid` で書くので、親が読んだ先頭のものだけ。GitLab は親から最初の親を遡って読んだ先頭に
 * 届くものだけ（読んだ後に積まれた）。見つからなければ null
 */
async function findWritten(deps: WriteDeps, repo: RepoConfig, now: string, read: FamilyRead, rows: readonly ChangeRow[]): Promise<Committed | null> {
  const parentsOf = async (sha: string) => (await deps.call("commitParents", [repo.owner, repo.repo, sha])) as string[];
  let at = now;
  for (let i = 0; i < FIND_DEPTH && at !== read.head; i += 1) {
    const ps = await parentsOf(at);
    if (ps.length !== 1) return null;
    if ((await verifyWritten(deps, repo, at, rows)).length === 0 && (await verifyWritten(deps, repo, ps[0], rows)).length > 0) {
      if (ps[0] === read.head) return { oid: at, parent: ps[0] };
      if (deps.kind !== "gitlab") return null;
      // GitLab: 親から最初の親を遡って読んだ先頭に届くか
      let back = ps[0];
      for (let j = 0; j < FIND_DEPTH && back !== read.head; j += 1) {
        const bp = await parentsOf(back);
        if (bp.length === 0) return null;
        back = bp[0];
      }
      return back === read.head ? { oid: at, parent: ps[0] } : null;
    }
    at = ps[0];
  }
  return null;
}

/**
 * 1 コミットで書く。service worker が形や守りで断ったら（400）、原因を言って失敗にする。GitLab で書く前の確認で先頭が
 * 動いていたら（412。何も送っていない）「動いた」。ほかの失敗では先頭を読み直し、動いていなければ失敗。動いていれば、
 * 応答だけが落ちた自分の書き込みを探し（`findWritten`）、見つかれば書けたとする。見つからず、今の先頭に書いた中身が
 * 在るなら（書いたか見分けられない）ユーザに回す。無ければ「動いた」（読み直して周を回す）。
 * 応答が落ちた後の確かめそのものが落ちたら（429・5xx など）、書いたかもしれないが確認できないのでユーザに回す（11.9.3 の 1）
 */
async function commitOnce(
  deps: WriteDeps,
  repo: RepoConfig,
  read: FamilyRead,
  family: string,
  message: { headline: string; body: string },
  rows: readonly ChangeRow[],
): Promise<Attempt> {
  let text: string;
  try {
    const done = await send(deps, repo, family, read.head, message, rows);
    return { kind: "written", oid: done.oid, parent: done.parent };
  } catch (err) {
    text = (err as Error).message ?? String(err);
    const status = (err as { status?: number }).status;
    if (status === REFUSED) return { kind: "failed", message: text };
    if (status === HOST_MOVED) return { kind: "moved" };
  }
  try {
    const now = (await deps.call("branchHead", [repo.owner, repo.repo, family])) as string | null;
    if (now === null) return { kind: "failed", message: `親のブランチ ${family} がホストから消えた（${text}）` };
    if (now === read.head) return { kind: "failed", message: text };
    for (let i = 0; i < VERIFY_TRIES; i += 1) {
      const found = await findWritten(deps, repo, now, read, rows);
      if (found) return { kind: "written", ...found };
      if (i + 1 < VERIFY_TRIES) await (deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms))))(VERIFY_WAIT_MS);
    }
    if ((await verifyWritten(deps, repo, now, rows)).length === 0) {
      return {
        kind: "attention",
        message: `${family} への書き込みの応答が返ってこなかった。書いた中身は先頭にあるが、この拡張が書いたコミットかどうかを見分けられなかった（${text}）。ホストの履歴を確かめてください`,
      };
    }
    return { kind: "moved" };
  } catch (err) {
    return {
      kind: "attention",
      message: `${family} への書き込みの応答が返ってこず（${text}）、書いたかもしれないが確認できなかった（${(err as Error).message ?? String(err)}）。ホストの履歴を確かめてください`,
    };
  }
}

/** 書いた後の確かめ。合わない・確かめが落ちたら、書いたものが残っているのでユーザに回す（failed にしない。11.9.3 の 1） */
async function finish(deps: WriteDeps, repo: RepoConfig, oid: string, rows: readonly ChangeRow[], rounds: number, lines: readonly string[]): Promise<Outcome> {
  let wrong: string[];
  try {
    wrong = await verifySettled(deps, repo, oid, rows);
  } catch (err) {
    return {
      kind: "attention",
      message: `書いた（${oid.slice(0, 7)}）が、書いた後の中身を確認できなかった（${(err as Error).message ?? String(err)}）。ホストの履歴を確かめてください`,
    };
  }
  if (wrong.length > 0) {
    return { kind: "attention", message: `書いた後の中身が書いたものと違う（${oid.slice(0, 7)}: ${wrong.join(", ")}）。ホストの履歴を確かめてください` };
  }
  return { kind: "written", oid, rounds, lines };
}

/** Python の Changes から、この家族の P に書く行を取り出す。P の外・置き場の外に及べば理由（決定 D） */
export function rowsOf(res: Written, family: string, read: Pick<FamilyRead, "place">): readonly ChangeRow[] | string {
  if (res.stopped) return `${res.stopped.ticket}: ${res.stopped.reason}`;
  const names = Object.keys(res.changes ?? {});
  if (names.some((n) => n !== family)) return `書くものが親のブランチ ${family} の外に及ぶ（${names.join(", ")}）`;
  const rows = res.changes?.[family] ?? [];
  if (rows.length === 0) return "書くものが無い";
  const outside = rows.filter((r) => !underPlaces(r.path, read.place)).map((r) => r.path);
  if (outside.length > 0) return `書くパスが置き場の外にある（${outside.join(", ")}）`;
  return rows;
}

/** 読んだ家族から、書くもの（1 コミット）か、書かずに終える答え */
type Planned =
  | { readonly kind: "rows"; readonly rows: readonly ChangeRow[]; readonly message: { headline: string; body: string }; readonly lines: readonly string[] }
  | { readonly kind: "stop"; readonly outcome: Outcome };

/**
 * 読んだ家族と時刻から書くものを決める。時刻（`stamp`）は周ごとに 1 回決め、事後確認の判定し直しにも同じ値を渡す
 * （時計が進んで跡の `at` が変わるだけで「書くものが違う」にしない。11.9.1 の 2）
 */
type Plan = (read: FamilyRead, stamp: string) => Promise<Planned>;

/** 同じ書くものか（パス・作るか書き換えるか消すか・中身のバイト列）。並びは問わない */
async function sameRows(a: readonly ChangeRow[], b: readonly ChangeRow[]): Promise<boolean> {
  if (a.length !== b.length) return false;
  const key = async (r: ChangeRow) => `${r.path}\0${r.op}\0${r.op === "delete" ? "" : await blobSha(rowBytes(r))}`;
  const x = (await Promise.all(a.map(key))).sort();
  const y = (await Promise.all(b.map(key))).sort();
  return x.every((k, i) => k === y[i]);
}

/** 本文を UTF-8 のバイト列のまま base64 にする（BOM を含めて変えない） */
function textBase64(text: string): string {
  return base64(new TextEncoder().encode(text));
}

/** 自分の書き込みを打ち消す書くもの: 書いた各パスを、自分の書き込みの直前（`parent`）の中身に戻す */
async function undoRows(deps: WriteDeps, repo: RepoConfig, parent: string, rows: readonly ChangeRow[]): Promise<ChangeRow[] | string> {
  const out: ChangeRow[] = [];
  for (let i = 0; i < rows.length; i += 20) {
    const part = rows.slice(i, i + 20);
    const objs = (await deps.call("pathObjects", [repo.owner, repo.repo, parent, part.map((r) => r.path)])) as Record<string, PathObject | null>;
    const shas = part.map((r) => objs[r.path]).filter((o): o is PathObject => !!o && o.type === "blob").map((o) => o.oid);
    const texts = shas.length > 0 ? ((await deps.call("blobs", [repo.owner, repo.repo, [...new Set(shas)]])) as Record<string, BlobText>) : {};
    for (const row of part) {
      const obj = objs[row.path];
      if (!obj) {
        // 直前には無かった。自分が作ったものを消す（自分も消していたなら戻すものは無い）
        if (row.op !== "delete") out.push({ op: "delete", path: row.path });
        continue;
      }
      const blob = obj.type === "blob" ? texts[obj.oid] : undefined;
      if (!blob || blob.binary || typeof blob.text !== "string") return `${row.path} の直前の中身を読めない`;
      // バイト列のまま戻す（BOM も落とさない。戻した後に blob の sha で確かめる）
      const undo: ChangeRow = { op: row.op === "delete" ? "create" : "update", path: row.path, base64: textBase64(blob.text) };
      if ((await blobSha(rowBytes(undo))) !== obj.oid) return `${row.path} の直前の中身をバイト列のまま読めない`;
      out.push(undo);
    }
  }
  return out;
}

/** 打ち消しを積む回数の上限（8.4 の「2 回で収まらなければユーザに回す」） */
export const REVERT_TRIES = 2;


/** 書いたが確かめ切れなかった・打ち消せなかったときの答え（ユーザに回す。8.4） */
function attention(family: string, done: Committed, why: string): Outcome {
  return {
    kind: "attention",
    message: `${family} への書き込み（${done.oid.slice(0, 7)}）の後に別の書き込みが入り、書いたものを確かめ切れなかったか、打ち消せなかった（${why}）。ホストの履歴を確かめてください（ADR-0093 の 8.4）`,
  };
}

/**
 * 自分の書き込み（`done`）を打ち消す。今の先頭で、書いた各パスがまだ自分の書いたとおりのときだけ積み、各ファイルに
 * 「最後に変えたのは自分のコミット」を付ける（GitLab が他人の変更の上に書かないよう断る。決定 A）。
 * 送った応答が落ちたら、届いたか（今の先頭の親が送った先で、中身が戻したとおり）を見る。打ち消しがさらに競合して
 * 同じパスが変わっていれば、ユーザに回す。
 */
async function revert(deps: WriteDeps, repo: RepoConfig, family: string, done: Committed, rows: readonly ChangeRow[]): Promise<{ kind: "reverted" } | { kind: "stop"; outcome: Outcome }> {
  const human = (why: string): { kind: "stop"; outcome: Outcome } => ({ kind: "stop", outcome: attention(family, done, why) });
  const undo = await undoRows(deps, repo, done.parent, rows);
  if (typeof undo === "string") return human(undo);
  if (undo.length === 0) return { kind: "reverted" };
  const message = commitMessage([family], "への書き込みを打ち消す", "打ち消した", deps.version);
  for (let attempt = 1; attempt <= REVERT_TRIES; attempt += 1) {
    const cur = (await deps.call("branchHead", [repo.owner, repo.repo, family])) as string | null;
    if (cur === null) return human(`親のブランチ ${family} がホストから消えた`);
    const touched = await verifyWritten(deps, repo, cur, rows);
    if (touched.length > 0) return human(`後から別の書き込みが同じファイルを変えた: ${touched.join(", ")}`);
    let res: Committed;
    try {
      res = await send(deps, repo, family, cur, { headline: message.headline, body: `打ち消すコミット: ${done.oid}\n` }, undo, done.oid);
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === REFUSED || status === HOST_REFUSED) return human((err as Error).message ?? String(err));
      // 送る前の確認で先頭が動いた（何も送っていない）: 上限の範囲で今の先頭から確かめ直す
      if (status === HOST_MOVED) continue;
      // 応答だけが落ちたか: 今の先頭の親が送った先で、中身が戻したとおりなら届いている
      const now = (await deps.call("branchHead", [repo.owner, repo.repo, family])) as string | null;
      if (now !== null && now !== cur) {
        const ps = (await deps.call("commitParents", [repo.owner, repo.repo, now])) as string[];
        if (ps.length === 1 && ps[0] === cur && (await verifySettled(deps, repo, now, undo)).length === 0) return { kind: "reverted" };
      }
      continue;
    }
    // 戻した後に中身を確かめる（バイト列が元と同じか）
    const wrong = await verifySettled(deps, repo, res.oid, undo);
    if (wrong.length > 0) return human(`打ち消した後の中身が戻したものと違う: ${wrong.join(", ")}`);
    if (res.parent === cur) return { kind: "reverted" };
    // 打ち消しの間にも別の書き込みが入った。それが同じパスを変えていなければ打ち消しは反映されている
    if ((await verifyWritten(deps, repo, res.parent, rows)).length === 0) return { kind: "reverted" };
    return human("打ち消しの間にも別の書き込みが同じファイルを変えた");
  }
  return human(`打ち消しが ${REVERT_TRIES} 回とも書けなかった`);
}

/**
 * 事後確認（8.4 の 1 段目）。書いたコミットの親が読んだ先頭と違えば、自分の書き込みの直前（その親）の姿で、
 * 同じ時刻で判定し直す。同じ書くものなら残す（`kept`）。違えば打ち消す（`reverted` で周を回す）。
 * 途中でホストや Python が落ちたら（429・5xx・MR が消えた など）、書いたものを確かめ切れないのでユーザに回す
 */
async function settleRace(
  deps: WriteDeps,
  repo: RepoConfig,
  family: string,
  done: Committed,
  planned: Extract<Planned, { kind: "rows" }>,
  plan: Plan,
  stamp: string,
): Promise<{ kind: "kept" } | { kind: "reverted" } | { kind: "stop"; outcome: Outcome }> {
  try {
    let again: Planned | null = null;
    try {
      again = await plan(await readFamily(repo, family, deps, done.parent), stamp);
    } catch (err) {
      if (!(err instanceof MovedError) && !(err instanceof PyError)) throw err;
      again = null;
    }
    if (again !== null && again.kind === "rows" && (await sameRows(again.rows, planned.rows))) return { kind: "kept" };
    return await revert(deps, repo, family, done, planned.rows);
  } catch (err) {
    return { kind: "stop", outcome: attention(family, done, `書いたが確認できなかった: ${(err as Error).message ?? String(err)}`) };
  }
}

/** 書く流れの周（8.3 の 2〜5 と、GitLab の事後確認 8.4）。周ごとに家族を読み直して書くものを決め直す */
async function writeLoop(repo: RepoConfig, family: string, deps: WriteDeps, what: string, plan: Plan): Promise<Outcome> {
  try {
    for (let round = 1; round <= MAX_ROUNDS; round += 1) {
      const read = await readOrMoved(repo, family, deps);
      if (read === null) continue;
      const stamp = localStamp(deps.now());
      const planned = await plan(read, stamp);
      if (planned.kind === "stop") return planned.outcome;
      const done = await commitOnce(deps, repo, read, family, planned.message, planned.rows);
      if (done.kind === "failed") return { kind: "failed", message: done.message };
      if (done.kind === "attention") return { kind: "attention", message: done.message };
      if (done.kind === "moved") continue;
      if (done.parent === read.head) return await finish(deps, repo, done.oid, planned.rows, round, planned.lines);
      const settled = await settleRace(deps, repo, family, done, planned, plan, stamp);
      if (settled.kind === "kept") return await finish(deps, repo, done.oid, planned.rows, round, planned.lines);
      if (settled.kind === "stop") return settled.outcome;
    }
    return { kind: "conflict", message: `書いている間に ${family} の先頭が ${MAX_ROUNDS} 回動いた。少し待ってからボードを更新して${what}` };
  } catch (err) {
    return { kind: err instanceof PyError ? "refused" : "failed", message: (err as Error).message ?? String(err) };
  }
}

/** 家族 1 つの承認待ちを承認する（8.3） */
export async function approveFamily(repo: RepoConfig, family: string, shown: Shown, deps: WriteDeps): Promise<Outcome> {
  let actor: Actor;
  try {
    actor = await actorOf(deps, repo);
  } catch (err) {
    return { kind: "failed", message: (err as Error).message ?? String(err) };
  }
  return await writeLoop(repo, family, deps, "承認し直してください", async (read, stamp) => {
    const res = await py.plan(deps.py, {
      settings: read.settings,
      snapshot: read.input,
      family,
      only: shown.only,
      shown: { ids: shown.ids, digest: shown.digest },
      stamp,
      actor,
    });
    if (res.mismatch) return { kind: "stop", outcome: { kind: "changed", message: "ボードに表示した後で承認待ちの中身が変わった。中身を見直してから承認し直してください" } };
    if (!res.changes) return { kind: "stop", outcome: { kind: "refused", message: why(res) } };
    const rows = rowsOf(res, family, read);
    if (typeof rows === "string") return { kind: "stop", outcome: { kind: "refused", message: rows } };
    return { kind: "rows", rows, message: commitMessage(res.identifiers, "を承認", "承認した", deps.version), lines: res.lines };
  });
}

/** 家族を読み直す。読んでいる間に先頭が動いたら null（周を回す） */
async function readOrMoved(repo: RepoConfig, family: string, deps: WriteDeps): Promise<FamilyRead | null> {
  try {
    return await readFamily(repo, family, deps);
  } catch (err) {
    if (err instanceof MovedError) return null;
    throw err;
  }
}

/** 承認を取り下げる（8.8）。着手前の新規の承認だけ。元の提案は承認コミットの親から戻す */
export async function withdrawTicket(repo: RepoConfig, family: string, ident: string, reason: string, deps: WriteDeps): Promise<Outcome> {
  let actor: Actor;
  try {
    actor = await actorOf(deps, repo);
  } catch (err) {
    return { kind: "failed", message: (err as Error).message ?? String(err) };
  }
  return await writeLoop(repo, family, deps, "取り下げ直してください", async (read, stamp) => {
    const prior = await findPrior((op, args) => deps.call(op, [repo.owner, repo.repo, ...args]), read.place, read.head, ident);
    const res = await py.withdraw(deps.py, {
      settings: read.settings,
      snapshot: read.input,
      family,
      ids: [ident],
      prior: prior === null ? {} : { [ident]: prior },
      reason,
      stamp,
      actor,
    });
    if (res.problems.length > 0 || !res.changes) {
      return { kind: "stop", outcome: { kind: "refused", message: res.problems.join("\n") || "取り下げるものが無い" } };
    }
    const rows = rowsOf(res, family, read);
    if (typeof rows === "string") return { kind: "stop", outcome: { kind: "refused", message: rows } };
    return { kind: "rows", rows, message: commitMessage([ident], "の承認を取り下げ", "取り下げた", deps.version), lines: res.lines };
  });
}

/**
 * フェーズをレビュー済みにする（8.9。段階 4）。毎周、家族と MR のスレッド・レビューを読み直し、
 * Python の `confirm` が通したときだけ、レビュー待ちの子の `done/` への移動と印（`actor` = PAT の持ち主、
 * `via: chrome`）を 1 コミットで書く。未解決のスレッドや変更要求が残れば書かない。
 */
export async function confirmPhase(repo: RepoConfig, family: string, phase: number, deps: WriteDeps): Promise<Outcome> {
  let actor: Actor;
  try {
    actor = await actorOf(deps, repo);
  } catch (err) {
    return { kind: "failed", message: (err as Error).message ?? String(err) };
  }
  const ask = (op: string, args: readonly unknown[]) => deps.call(op, [repo.owner, repo.repo, ...args]);
  return await writeLoop(repo, family, deps, "レビュー済みにし直してください", async (read, stamp) => {
    const copy = (await ask("reviewCopy", [family])) as ReviewCopy;
    const res = await askConfirm(deps.py, ask, {
      settings: read.settings,
      snapshot: read.input,
      family,
      phase,
      copy,
      stamp,
      actor,
    });
    if (res.problems.length > 0 || !res.changes) {
      return { kind: "stop", outcome: { kind: "refused", message: res.problems.join("\n") || "レビュー済みにするものが無い" } };
    }
    const rows = rowsOf(res, family, read);
    if (typeof rows === "string") return { kind: "stop", outcome: { kind: "refused", message: rows } };
    const message = commitMessage([family], `のフェーズ ${phase} のレビュー済みを置いた`, "レビュー済みを置いた", deps.version);
    return { kind: "rows", rows, message, lines: res.lines };
  });
}
