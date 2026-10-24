/**
 * Chrome からの承認とレビュー済み。
 *
 * 判定は Python（同梱の ccnavi）が出し、ここは Python が返した書くもの（Changes）を
 * 親のブランチ `P` への 1 コミットにして送るだけ。TS が判定を出すと手元と答えが 2 か所に分かれるため。流れは次のとおり。
 *
 * 1. preview: ボードが見せた画面のダイジェスト（`digest`）と一覧（`ids`）を持っている
 * 2. yes: 親子のチケット 1 組ぶんを読み直して Snapshot を組み直し、Python に見せたものと比べさせる。
 *    違えば書かずに「見直す」（preview からやり直し）
 * 3. `plan` の変更を `createCommitOnBranch` の 1 コミットで書く。条件は `expectedHeadOid` = 読んだ `P` の先頭
 * 4. 先頭が動いていたら、新しい先頭で Snapshot を組み直して判定と plan を必ずやり直す。
 *    ダイジェストが同じなら新しい plan を書く。違えば「見直す」。3 周しても書けなければユーザの対応に切り替える
 * 5. 書いた後、新しい先頭の中身が書いたとおりか確かめる（blob の sha を突き合わせる）
 *
 * レビュー済みも同じ流れで、毎周 MR のスレッドとレビューを読み直して Python の `confirm`
 * （手元の `ccnavi review confirm` と同じコア）に通るかを決めさせる。
 *
 * GitLab にも書く。GitLab の Commits API には比較つきの書き込みが無いので、事後確認と元に戻すコミット（revert）で守る: 書いた答えのコミットの親が読んだ先頭と違えば（間に別の書き込みが入った）、自分の書き込みの
 * 直前の状態で判定し直す。同じ書くものになれば、そのまま残す。違えば（結論が変わった）元に戻すコミットを積み、
 * 新しい先頭から読み直して再試行する。元に戻すコミットもさらに競合して 2 回で収まらなければ、止めてユーザの対応に切り替える（`attention`）。
 * 書き込みどうしの競合は検出しない。
 */
import { APPROVE_DIGEST_CHANGED, APPROVE_LIST_CHANGED, py, PyError, tagged, type Actor, type ChangeRow, type PlanResult, type ReasonCode, type Written } from "./py.js";
import type { RepoConfig } from "./settings.js";
import { MovedError, readFamily, type Deps as ReadDeps, type FamilyRead } from "./snapshot.js";
import type { BlobText, FileAddition, PathObject, ReviewCopy } from "./github.js";
import { askConfirm, askDecide, type DecideChoice, type DecidePreview, type DecideThread } from "./reviewed.js";
import { callOps, describeHostFailure, ISSUE_LINKS_SLOT, precheckHostPlan, runHostPlan, unsupportedReason, type HostPlan } from "./decide-host.js";
import { underPlaces } from "./guard.js";
import { localStamp } from "./stamp.js";

/** 先頭が動いたときに読み直して書き直す回数の上限 */
export const MAX_ROUNDS = 3;

export interface WriteDeps extends ReadDeps {
  /** ホストの種類（応答が届かなかった書き込みの見分け方が違う。無ければ github） */
  readonly kind?: "github" | "gitlab";
  /** 拡張の版（コミットの見出し） */
  readonly version: string;
  /** 待つ（書いた直後の読み取りの遅れ）。無ければ実物の時計。試験は 0 秒にする */
  readonly sleep?: (ms: number) => Promise<void>;
}

/** 書いた直後の確かめを読み直す回数と間隔（ホストの読み取りの遅れ） */
const VERIFY_TRIES = 3;
const VERIFY_WAIT_MS = 1000;
/** 見出しに並べる識別子の数。残りは件数にまとめ、全件は本文に書く */
const HEADLINE_IDS = 3;

/** service worker が書く頼みを形や保護で断ったときの status（protocol.ts の REFUSED と同じ） */
const REFUSED = 400;
/** GitLab が書き込みを断ったとき（`last_commit_id` が違う＝同じファイルを他人が変えた・無い）の status（`gitlab.HOST_REFUSED`）。ユーザの対応に切り替える */
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
  /** `code` は Python が返した理由コード（`CORE_*`）。文面の頭にも `[<コード>]` で載る */
  | { readonly kind: "changed"; readonly message: string; readonly code?: string }
  | { readonly kind: "refused"; readonly message: string; readonly code?: string }
  | { readonly kind: "conflict"; readonly message: string }
  | { readonly kind: "failed"; readonly message: string }
  /** ユーザの対応に切り替える（元に戻すコミットでも競合が収まらない・書いたか確かめられない・書いた後の中身が違う）。ボードは親子のチケットを「要確認」で出す */
  | { readonly kind: "attention"; readonly message: string };

export interface Shown {
  readonly ids: readonly string[];
  readonly digest: string;
  /** ダイジェストを出したときの絞り（ボードの答えの `only`） */
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

/** Changes の 1 ブランチぶんを、1 コミットの足す・消すに分ける */
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

/** 書いた後の確かめ: `oid` の上の各パスが書いたとおりか。違うパスの配列を返す（空なら合っている） */
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
  const text = lines.join("\n") || "承認の対象がありません";
  return res.code ? tagged(res.code, text) : text;
}

/** 答えの理由コードを Outcome の `code` にする（通った答え・コードの無い答えなら付けない） */
function codeOf(res: { readonly code?: ReasonCode }): { code?: string } {
  return res.code ? { code: res.code } : {};
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
 * 「最後に変えたコミット」として付ける（GitLab）。元に戻すときは、自分が書いたコミットを `last` に渡す。
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

/** 遡る最初の親の数の上限（応答が届かなかった書き込みを探す） */
const FIND_DEPTH = 20;

/**
 * 応答が届かなかった書き込みを、今の先頭から最初の親を遡って探す。見分け方は「書いた中身と親の組」による。
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
 * 1 コミットで書く。service worker が形や保護で断ったら（400）、原因を言って失敗にする。GitLab で書く前の確認で先頭が
 * 動いていたら（412。何も送っていない）「動いた」。ほかの失敗では先頭を読み直し、動いていなければ失敗。動いていれば、
 * 応答だけが届かなかった自分の書き込みを探し（`findWritten`）、見つかれば書けたとする。見つからず、今の先頭に書いた中身が
 * 在るなら（書いたか見分けられない）ユーザの対応に切り替える。無ければ「動いた」（読み直して再試行する）。
 * 応答が届かなかった後の確かめそのものが失敗したら（429・5xx など）、書いたかもしれないが確認できないのでユーザの対応に切り替える
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
    if (now === null) return { kind: "failed", message: `親のブランチ ${family} がホストから見つかりません（${text}）` };
    if (now === read.head) return { kind: "failed", message: text };
    for (let i = 0; i < VERIFY_TRIES; i += 1) {
      const found = await findWritten(deps, repo, now, read, rows);
      if (found) return { kind: "written", ...found };
      if (i + 1 < VERIFY_TRIES) await (deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms))))(VERIFY_WAIT_MS);
    }
    if ((await verifyWritten(deps, repo, now, rows)).length === 0) {
      return {
        kind: "attention",
        message: `${family} への書き込みの応答が返ってきませんでした。反映した内容は先頭にありますが、この拡張が作成したコミットかどうかを判別できませんでした（${text}）。ホストの履歴を確認してください。`,
      };
    }
    return { kind: "moved" };
  } catch (err) {
    return {
      kind: "attention",
      message: `${family} への書き込みの応答が返ってこず（${text}）、反映されたかどうか確認できませんでした（${(err as Error).message ?? String(err)}）。ホストの履歴を確認してください。`,
    };
  }
}

/** 書いた後の確かめ。合わない・確かめが失敗したら、書いたものが残っているのでユーザの対応に切り替える（failed にしない） */
async function finish(deps: WriteDeps, repo: RepoConfig, oid: string, rows: readonly ChangeRow[], rounds: number, lines: readonly string[]): Promise<Outcome> {
  let wrong: string[];
  try {
    wrong = await verifySettled(deps, repo, oid, rows);
  } catch (err) {
    return {
      kind: "attention",
      message: `反映しました（${oid.slice(0, 7)}）が、反映後の内容を確認できませんでした（${(err as Error).message ?? String(err)}）。ホストの履歴を確認してください。`,
    };
  }
  if (wrong.length > 0) {
    return { kind: "attention", message: `反映後の内容が、反映したものと異なります（${oid.slice(0, 7)}：${wrong.join(", ")}）。ホストの履歴を確認してください。` };
  }
  return { kind: "written", oid, rounds, lines };
}

/** Python の Changes から、この親子のチケット P に書く行を取り出す。P の外・置き場の外に及べば理由 */
export function rowsOf(res: Written, family: string, read: Pick<FamilyRead, "place">): readonly ChangeRow[] | string {
  if (res.stopped) return `${res.stopped.ticket}: ${res.stopped.reason}`;
  const names = Object.keys(res.changes ?? {});
  if (names.some((n) => n !== family)) return `反映する内容が親ブランチ ${family} の外に及んでいます（${names.join(", ")}）`;
  const rows = res.changes?.[family] ?? [];
  if (rows.length === 0) return "反映する内容がありません";
  const outside = rows.filter((r) => !underPlaces(r.path, read.place)).map((r) => r.path);
  if (outside.length > 0) return `反映先のパスが置き場の外にあります（${outside.join(", ")}）`;
  return rows;
}

/** 読んだ親子のチケットから、書くもの（1 コミット）か、書かずに終える答え */
type Planned =
  | { readonly kind: "rows"; readonly rows: readonly ChangeRow[]; readonly message: { headline: string; body: string }; readonly lines: readonly string[] }
  | { readonly kind: "stop"; readonly outcome: Outcome };

/**
 * 読んだ親子のチケットと時刻から書くものを決める。時刻（`stamp`）は周ごとに 1 回決め、事後確認の判定し直しにも同じ値を渡す
 * （時計が進んでマーカーの `at` が変わるだけで「書くものが違う」にしない）
 */
type Plan = (read: FamilyRead, stamp: string) => Promise<Planned>;

/** 同じ書くものか（パス・作るか書き換えるか消すか・中身のバイト列）。順序は問わない */
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

/** 自分の書き込みを元に戻す書くもの: 書いた各パスを、自分の書き込みの直前（`parent`）の中身に戻す */
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
      if (!blob || blob.binary || typeof blob.text !== "string") return `${row.path} の直前の内容を取得できません`;
      // バイト列のまま戻す（BOM も除かない。戻した後に blob の sha で確かめる）
      const undo: ChangeRow = { op: row.op === "delete" ? "create" : "update", path: row.path, base64: textBase64(blob.text) };
      if ((await blobSha(rowBytes(undo))) !== obj.oid) return `${row.path} の直前の内容をバイト列のまま取得できません`;
      out.push(undo);
    }
  }
  return out;
}

/** 元に戻すコミットを積む回数の上限（2 回で収まらなければユーザの対応に切り替える） */
export const REVERT_TRIES = 2;


/** 書いたが確かめ切れなかった・元に戻せなかったときの答え（ユーザの対応に切り替える） */
function attention(family: string, done: Committed, why: string): Outcome {
  return {
    kind: "attention",
    message: `${family} への書き込み（${done.oid.slice(0, 7)}）の後に別の書き込みが入り、反映した内容を確認しきれなかったか、元に戻せませんでした（${why}）。ホストの履歴を確認してください。`,
  };
}

/**
 * 自分の書き込み（`done`）を元に戻す。今の先頭で、書いた各パスがまだ自分の書いたとおりのときだけ積み、各ファイルに
 * 「最後に変えたのは自分のコミット」を付ける（GitLab が他人の変更の上に書かないよう断る）。
 * 送ったのに応答が届かなかったら、届いたか（今の先頭の親が送った先で、中身が戻したとおり）を見る。元に戻すコミットがさらに競合して
 * 同じパスが変わっていれば、ユーザの対応に切り替える。
 */
async function revert(deps: WriteDeps, repo: RepoConfig, family: string, done: Committed, rows: readonly ChangeRow[]): Promise<{ kind: "reverted" } | { kind: "stop"; outcome: Outcome }> {
  const human = (why: string): { kind: "stop"; outcome: Outcome } => ({ kind: "stop", outcome: attention(family, done, why) });
  const undo = await undoRows(deps, repo, done.parent, rows);
  if (typeof undo === "string") return human(undo);
  if (undo.length === 0) return { kind: "reverted" };
  const message = commitMessage([family], "への書き込みを元に戻す", "元に戻した", deps.version);
  for (let attempt = 1; attempt <= REVERT_TRIES; attempt += 1) {
    const cur = (await deps.call("branchHead", [repo.owner, repo.repo, family])) as string | null;
    if (cur === null) return human(`親ブランチ ${family} がホストから見つかりません`);
    const touched = await verifyWritten(deps, repo, cur, rows);
    if (touched.length > 0) return human(`後から別の書き込みが同じファイルを変更しました：${touched.join(", ")}`);
    let res: Committed;
    try {
      res = await send(deps, repo, family, cur, { headline: message.headline, body: `元に戻したコミット: ${done.oid}\n` }, undo, done.oid);
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === REFUSED || status === HOST_REFUSED) return human((err as Error).message ?? String(err));
      // 送る前の確認で先頭が動いた（何も送っていない）: 上限の範囲で今の先頭から確かめ直す
      if (status === HOST_MOVED) continue;
      // 応答だけが届かなかったか: 今の先頭の親が送った先で、中身が戻したとおりなら届いている
      const now = (await deps.call("branchHead", [repo.owner, repo.repo, family])) as string | null;
      if (now !== null && now !== cur) {
        const ps = (await deps.call("commitParents", [repo.owner, repo.repo, now])) as string[];
        if (ps.length === 1 && ps[0] === cur && (await verifySettled(deps, repo, now, undo)).length === 0) return { kind: "reverted" };
      }
      continue;
    }
    // 戻した後に中身を確かめる（バイト列が元と同じか）
    const wrong = await verifySettled(deps, repo, res.oid, undo);
    if (wrong.length > 0) return human(`元に戻した後の内容が、戻したものと異なります：${wrong.join(", ")}`);
    if (res.parent === cur) return { kind: "reverted" };
    // 元に戻すあいだにも別の書き込みが入った。それが同じパスを変えていなければ、元に戻すコミットは反映されている
    if ((await verifyWritten(deps, repo, res.parent, rows)).length === 0) return { kind: "reverted" };
    return human("元に戻す間にも、別の書き込みが同じファイルを変更しました");
  }
  return human(`元に戻すコミットを ${REVERT_TRIES} 回とも反映できませんでした`);
}

/**
 * 事後確認。書いたコミットの親が読んだ先頭と違えば、自分の書き込みの直前（その親）の状態で、
 * 同じ時刻で判定し直す。同じ書くものなら残す（`kept`）。違えば元に戻す（`reverted` で読み直して再試行する）。
 * 途中でホストや Python が失敗したら（429・5xx・MR が消えた など）、書いたものを確かめ切れないのでユーザの対応に切り替える
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
    return { kind: "stop", outcome: attention(family, done, `反映しましたが、確認できませんでした：${(err as Error).message ?? String(err)}`) };
  }
}

/** 書く流れの周（読み直し・ダイジェストの比べ・書き込み・先頭が動いたときのやり直しと、GitLab の事後確認）。周ごとに親子のチケットを読み直して書くものを決め直す */
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
    return { kind: "conflict", message: `反映中に ${family} の先頭が ${MAX_ROUNDS} 回更新されました。少し待ってからボードを更新し、${what}` };
  } catch (err) {
    return { kind: err instanceof PyError ? "refused" : "failed", message: (err as Error).message ?? String(err) };
  }
}

/** 親子のチケット 1 組の承認待ちを承認する */
export async function approveFamily(repo: RepoConfig, family: string, shown: Shown, deps: WriteDeps): Promise<Outcome> {
  let actor: Actor;
  try {
    actor = await actorOf(deps, repo);
  } catch (err) {
    return { kind: "failed", message: (err as Error).message ?? String(err) };
  }
  return await writeLoop(repo, family, deps, "承認し直してください。", async (read, stamp) => {
    const res = await py.plan(deps.py, {
      snapshot: read.input,
      family,
      only: shown.only,
      shown: { ids: shown.ids, digest: shown.digest },
      stamp,
      actor,
    });
    if (res.code === APPROVE_LIST_CHANGED || res.code === APPROVE_DIGEST_CHANGED) {
      const message = tagged(res.code, "内容が更新されました。ボードに表示した後で承認待ちの提案が変わったので、何も承認していません。最新の内容を表示し直しました。確認してから、もう一度承認してください。");
      return { kind: "stop", outcome: { kind: "changed", message, code: res.code } };
    }
    if (!res.changes) return { kind: "stop", outcome: { kind: "refused", message: why(res), ...codeOf(res) } };
    const rows = rowsOf(res, family, read);
    if (typeof rows === "string") return { kind: "stop", outcome: { kind: "refused", message: rows } };
    return { kind: "rows", rows, message: commitMessage(res.identifiers, "を承認", "承認した", deps.version), lines: res.lines };
  });
}

/** 親子のチケットを読み直す。読んでいる間に先頭が動いたら null（読み直して再試行する） */
async function readOrMoved(repo: RepoConfig, family: string, deps: WriteDeps): Promise<FamilyRead | null> {
  try {
    return await readFamily(repo, family, deps);
  } catch (err) {
    if (err instanceof MovedError) return null;
    throw err;
  }
}

/**
 * フェーズをレビュー済みにする。毎周、親子のチケットと MR のスレッド・レビューを読み直し、
 * Python の `confirm` が通したときだけ、レビュー待ちの子の `done/` への移動とマーカー（`actor` = PAT の持ち主、
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
  return await writeLoop(repo, family, deps, "レビュー済みにし直してください。", async (read, stamp) => {
    const copy = (await ask("reviewCopy", [family])) as ReviewCopy;
    const res = await askConfirm(deps.py, ask, {
      snapshot: read.input,
      family,
      phase,
      copy,
      stamp,
      actor,
    });
    if (res.problems.length > 0 || !res.changes) {
      return { kind: "stop", outcome: { kind: "refused", message: res.problems.join("\n") || "レビュー済みにする対象がありません", ...codeOf(res) } };
    }
    const rows = rowsOf(res, family, read);
    if (typeof rows === "string") return { kind: "stop", outcome: { kind: "refused", message: rows } };
    const message = commitMessage([family], `のフェーズ ${phase} のレビュー済みを置いた`, "レビュー済みを置いた", deps.version);
    return { kind: "rows", rows, message, lines: res.lines };
  });
}


// ---- 残った指摘の行き先を決める（decide） --------------------------------------------------------

/** 途中で止まったときの、済んだ分 */
export interface DecideDone {
  /** 作れた issue の URL */
  readonly issues: readonly string[];
  /** 同じ印の issue が既にあったので作らず使った issue の URL */
  readonly reusedIssues?: readonly string[];
  /** 返信できたスレッドの数 */
  readonly replied: number;
  /** 解決できたスレッドの数 */
  readonly resolved: number;
}

/**
 * 決めた結果。ホストへ書く順の途中で止まったら `halted`（済んだ分と理由）。
 * `hostWritten` は、ホストへの書き込みが済んだあとで積めなかったことを表す（古いプレビューを開いたままにしない）
 */
export type DecideOutcome =
  /** `oid` はコミットの sha。積むものが無かったときは空。`warning` は反映が済んだあとに起きた失敗（MR へのコメントなど）。`pendingExtra` は見せたあとに増えた指摘の数 */
  | {
      readonly kind: "written";
      readonly oid: string;
      readonly reviewed: boolean;
      readonly followup: string;
      readonly issues: readonly string[];
      readonly reusedIssues?: readonly string[];
      readonly pendingExtra?: number;
      readonly warning?: string;
    }
  /** 見せた指摘が今と違う（画面を読み直して選び直させる） */
  | { readonly kind: "mismatch"; readonly message: string; readonly hostWritten?: boolean }
  | { readonly kind: "halted"; readonly done: DecideDone; readonly message: string }
  | (Exclude<Outcome, { readonly kind: "written" }> & { readonly hostWritten?: boolean });

/** 決める判断の内側で、書かずに終える答え（コミットを積む中身が無い） */
const NO_COMMIT: Outcome = { kind: "refused", message: "親のブランチへ反映する内容がありません" };

/** Python の `CORE_DECIDE_MISMATCH`（見せた指摘と今の指摘が違う） */
const DECIDE_MISMATCH = "CORE_DECIDE_MISMATCH";

/** Python の `CORE_DECIDE_CHOICES`（選択の形が合わない。`carry` が読み直した結果で解決済みでないときも） */
const DECIDE_CHOICES = "CORE_DECIDE_CHOICES";

/** 指摘のキー（URL か、無ければスレッドの id）→ ホストのスレッドの id。同じキーが複数あれば null（取り違えない） */
function threadIds(copy: ReviewCopy): Map<string, string | null> {
  const out = new Map<string, string | null>();
  for (const t of copy.threads) {
    if (t.resolved) continue;
    const key = t.url || t.id;
    out.set(key, out.has(key) ? null : t.id);
  }
  return out;
}

function reasonOf(err: unknown): string {
  return (err as Error).message ?? String(err);
}

/** 親子のチケットを読み直す。読んでいる間に先頭が動いたら、上限まで読み直す。収まらなければ null */
async function readSettled(repo: RepoConfig, family: string, deps: WriteDeps): Promise<FamilyRead | null> {
  for (let round = 1; round <= MAX_ROUNDS; round += 1) {
    const read = await readOrMoved(repo, family, deps);
    if (read !== null) return read;
  }
  return null;
}

/**
 * 決める画面の材料を読む（書かない）。親子のチケットと MR のスレッド・レビューを読み、Python の `decide`（preview）が
 * 通したときだけ、残った指摘とダイジェストを返す。通らなければ理由を投げる。
 *
 * 各指摘に、Chrome で扱えない理由（`unsupported`）を付ける。保留中のレビューのスレッド・解決できない種類のスレッド・
 * 差分以外の discussion（書く前の確かめと同じ見分け方）と、ホストのスレッドの id を特定できないもの。
 * 書く前の確かめが読めなかったときは付けず、確定のときの確かめで止める。
 */
export async function decidePreview(repo: RepoConfig, family: string, phase: number, deps: WriteDeps): Promise<DecidePreview> {
  const ask = (op: string, args: readonly unknown[]) => deps.call(op, [repo.owner, repo.repo, ...args]);
  const read = await readSettled(repo, family, deps);
  if (read === null) throw new Error(`読み取り中に ${family} の先頭が ${MAX_ROUNDS} 回更新されました。少し待ってからボードを更新してください`);
  const copy = (await ask("reviewCopy", [family])) as ReviewCopy;
  const res = await askDecide(deps.py, ask, { snapshot: read.input, family, phase, copy, stamp: localStamp(deps.now()), mode: "preview" });
  if (res.problems.length > 0 || !res.preview) throw new Error(res.problems.join("\n") || "残った指摘を読み取れませんでした");
  const ids = threadIds(copy);
  let abilities: Map<string, string> | null = null;
  try {
    const info = await callOps(deps.call).writeInfo(repo, copy.mr.number);
    abilities = new Map(info.threads.filter((t) => t.kind !== "ok").map((t) => [t.id, unsupportedReason(t.kind as Exclude<typeof t.kind, "ok">)]));
  } catch {
    abilities = null;
  }
  const threads: DecideThread[] = res.preview.threads.map((t) => {
    const id = ids.get(t.key);
    if (!id) return { ...t, unsupported: "ホストのスレッドを特定できません" };
    const kind = abilities?.get(id);
    return kind ? { ...t, unsupported: kind } : t;
  });
  return { ...res.preview, threads };
}

/** ホストへ書いた後に積めなかった答えには、ホストの側は済んでいることを先に言い、画面を閉じさせる */
function afterHost(out: Exclude<Outcome, { readonly kind: "written" }>, note: string): DecideOutcome {
  if (note === "") return out;
  return { ...out, message: `${note}${out.message}`, hostWritten: true };
}

/** 書き終えた keep・issue のキーを `carry` にしたとき、Python が読み直した結果で解決済みと確かめる回数 */
const CARRY_TRIES = 3;

/**
 * 選んだ行き先を反映する。順序は次のとおり。(1) 見せた指摘のまま通るかを Python に聞き、計画（`host_plan`）を得る。
 * (2) 書く前の確かめ（権限・Chrome で扱えない種類）。(3) ホストへ issue・返信・解決を書く（`writeLoop` の外で 1 度だけ）。
 * (4) 読み直して、`writeLoop` で親のブランチへ 1 コミットする。(5) MR にコメントを投稿する。
 *
 * - (1)(2) で通らなければ何も書かない。(3) で止まればコミットせず、済んだ分と理由を返す。
 *   止まったあとは、ボードを読み直し、未解決の指摘だけ決め直せば続きが進む。
 * - (4) のループはホストに書かない。先頭が動いて再試行しても、issue・返信・解決は重ならない。
 *   ホストで書き終えた `keep`・`issue` のキーは `carry` で渡し、読み直した結果で解決済みであることを Python が確かめる。
 *   ホストの読み取りが遅れて確かめが通らないときだけ、短く待って取り直す。
 * - 指摘が 0 件（`choices` が空）なら、ホストには書かず、「レビュー済みにする」と同じコミットを積む。コメントも投稿しない。
 *   従来の「レビュー済みにする」は、MR への書き込み権限を必要としなかった。
 * - 積むものが無ければ（`fix` が無く、見せたあとに増えた指摘がある）コミットを作らない（`oid` は空）
 * - (5) のコメントは、(1) の計画のもの（`{issue_links}` を作った・使った issue の URL の一覧に置き換える）。
 *   (1) を通らなかったとき（`fix` だけ）は、(4) の応答のもの。コミットが積めなかったときは投稿しない。
 *   投稿の失敗はコミットを戻さず、警告として結果に載せる（マーカーを置いた後は同じ入口で再送できない）
 */
export async function decidePhase(
  repo: RepoConfig,
  family: string,
  phase: number,
  choices: Readonly<Record<string, DecideChoice>>,
  digest: string,
  deps: WriteDeps,
): Promise<DecideOutcome> {
  let actor: Actor;
  try {
    actor = await actorOf(deps, repo);
  } catch (err) {
    return { kind: "failed", message: reasonOf(err) };
  }
  const ask = (op: string, args: readonly unknown[]) => deps.call(op, [repo.owner, repo.repo, ...args]);
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const ops = callOps(deps.call);
  const target = { owner: repo.owner, repo: repo.repo };
  const base = { family, phase, actor, mode: "apply" as const, choices, digest };
  const hostKeys = Object.keys(choices).filter((k) => choices[k] !== "fix");
  let mr = 0;
  const made: { key: string; url: string; reused: boolean }[] = [];
  let hostNote = "";
  let carry: string[] = [];
  /** (1) の計画のコメント（枠つき）。無ければ null */
  let planned: { comment: string | null } | null = null;
  let truncated = false;

  try {
    // (1) 見せた指摘のまま通るか。ホストへ書く計画を得る（`keep`・`issue` があるときだけ）
    if (hostKeys.length > 0) {
      const read = await readSettled(repo, family, deps);
      if (read === null) return { kind: "conflict", message: `反映前の読み取り中に ${family} の先頭が ${MAX_ROUNDS} 回更新されました。少し待ってからボードを更新し、もう一度決めてください。` };
      const copy = (await ask("reviewCopy", [family])) as ReviewCopy;
      mr = copy.mr.number;
      const res = await askDecide(deps.py, ask, { ...base, snapshot: read.input, copy, stamp: localStamp(deps.now()), carry: [] });
      if (res.code === DECIDE_MISMATCH || res.mismatch) return { kind: "mismatch", message: res.problems.join("\n") || "見せた指摘が今の指摘と違います" };
      if (res.problems.length > 0 || !res.host_plan) {
        return { kind: "refused", message: res.problems.join("\n") || "残った指摘の行き先を反映できません", ...codeOf(res) };
      }
      const plan = res.host_plan;
      planned = { comment: plan.comment };
      if (plan.issues.length > 0 || plan.replies.length > 0 || plan.resolves.length > 0) {
        // (2) 書く前の確かめ。足りなければ何も書かない
        const pre = await precheckHostPlan(ops, target, mr, plan);
        if (!pre.ok) return { kind: "refused", message: pre.reason };
        // (3) ホストへ書く。済んだ分を控える
        const done = await runHostPlan(ops, target, mr, { ...plan, comment: null } satisfies HostPlan);
        for (const i of done.issues) made.push({ key: i.key, url: i.url, reused: i.reused });
        truncated = done.searchTruncated;
        if (!done.ok) {
          return {
            kind: "halted",
            done: {
              issues: done.issues.filter((i) => !i.reused).map((i) => i.url),
              reusedIssues: done.issues.filter((i) => i.reused).map((i) => i.url),
              replied: done.replied.length,
              resolved: done.resolved.length,
            },
            message: done.failed ? describeHostFailure(done.failed) : "ホストへの書き込みが止まりました",
          };
        }
        carry = plan.resolves.map((r) => r.key);
        const reused = made.filter((i) => i.reused).length;
        hostNote = `ホストへの書き込み（issue ${made.length - reused} 件${reused > 0 ? `・既にあった issue ${reused} 件` : ""}・返信 ${done.replied.length} 件・解決 ${done.resolved.length} 件）は済んでいますが、親のブランチへ反映できませんでした。MR のコメントは投稿していません。ボードを読み直して、残った指摘だけ決め直してください。`;
      }
    }
  } catch (err) {
    return { kind: err instanceof PyError ? "refused" : "failed", message: reasonOf(err) };
  }

  // (4) 読み直して、親のブランチへ 1 コミット（ループの中ではホストに書かない）
  let last: Awaited<ReturnType<typeof askDecide>> | null = null;
  const out = await writeLoop(repo, family, deps, "決め直してください。", async (read, stamp) => {
    let res!: Awaited<ReturnType<typeof askDecide>>;
    for (let attempt = 1; attempt <= CARRY_TRIES; attempt += 1) {
      const copy = (await ask("reviewCopy", [family])) as ReviewCopy;
      mr = copy.mr.number;
      res = await askDecide(deps.py, ask, { ...base, snapshot: read.input, copy, stamp, carry });
      // 書き終えたキーが、読み直した結果でまだ解決済みに見えない（ホストの読み取りの遅れ）ときだけ、待って取り直す
      if (carry.length === 0 || res.code !== DECIDE_CHOICES) break;
      if (attempt < CARRY_TRIES) await sleep(VERIFY_WAIT_MS);
      else {
        return {
          kind: "stop",
          outcome: {
            kind: "attention",
            message: `書いた結果がホストの読み直しに反映されず、確かめられませんでした。ホストには書けています。読み直してから決め直してください（${res.problems.join("\n") || DECIDE_CHOICES}）`,
          },
        };
      }
    }
    if (res.code === DECIDE_MISMATCH || res.mismatch) {
      return { kind: "stop", outcome: { kind: "changed", message: res.problems.join("\n") || "見せた指摘が今の指摘と違います", code: DECIDE_MISMATCH } };
    }
    if (res.problems.length > 0 || res.stopped) {
      return { kind: "stop", outcome: { kind: "refused", message: res.problems.join("\n") || (res.stopped ? `${res.stopped.ticket}: ${res.stopped.reason}` : "残った指摘の行き先を反映できません"), ...codeOf(res) } };
    }
    last = res;
    if (!res.changes || Object.keys(res.changes).length === 0) return { kind: "stop", outcome: NO_COMMIT };
    const rows = rowsOf(res, family, read);
    if (typeof rows === "string") return { kind: "stop", outcome: { kind: "refused", message: rows } };
    const message = res.reviewed
      ? commitMessage([family], `のフェーズ ${phase} のレビュー済みを置いた`, "レビュー済みを置いた", deps.version)
      : commitMessage([family], `のフェーズ ${phase} の残った指摘の行き先を反映`, "行き先を反映した", deps.version);
    return { kind: "rows", rows, message, lines: res.lines };
  });

  let oid = "";
  if (out.kind === "written") oid = out.oid;
  else if (out !== NO_COMMIT) {
    // 積めなかった。コメントは投稿しない（コミットが無いのに「決めた」と書かない）
    return afterHost(out, hostNote);
  }

  // (5) 決めた内容のコメント。0 件の確定では投稿しない。失敗はコミットを戻さず警告にする
  const decided = last as Awaited<ReturnType<typeof askDecide>> | null;
  const warnings: string[] = [];
  if (truncated) warnings.push("issue の検索が上限まで読んでも見つからず、探し切れていません（以前に作った issue があれば重複します）");
  const comment = planned ? planned.comment : (decided?.host_plan?.comment ?? null);
  if (comment && Object.keys(choices).length > 0) {
    try {
      await ops.postComment(target, mr, comment.split(ISSUE_LINKS_SLOT).join(made.map((i) => `- ${i.url}`).join("\n")));
    } catch (err) {
      warnings.push(`決めた内容のコメントだけ MR に投稿できませんでした（${reasonOf(err)}）。反映は済んでいて同じ入口から再送できないため、必要なら MR に手で書いてください`);
    }
  }
  return {
    kind: "written",
    oid,
    reviewed: decided?.reviewed ?? false,
    followup: decided?.followup ?? "",
    issues: made.filter((i) => !i.reused).map((i) => i.url),
    ...(made.some((i) => i.reused) ? { reusedIssues: made.filter((i) => i.reused).map((i) => i.url) } : {}),
    ...(decided && decided.pending_extra.length > 0 ? { pendingExtra: decided.pending_extra.length } : {}),
    ...(warnings.length > 0 ? { warning: warnings.join("。") } : {}),
  };
}
