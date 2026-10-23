/**
 * 取り込み待ち画面が読む実行ファイルの答えの読み取り（`user handoff --json`）と、画面の判断の部分。
 * 形の契約は `handoff-view.ts`。
 *
 * 判定は実行ファイルが決めて答える（種類・状態・検査・差分・ハッシュ）。ここで決めるのは次の 3 つだけで、
 * どれも実行ファイルの答えを読んだうえでの画面の側の事情にとどめる。
 *
 * - どれを取り込めるか。`board` が真で、状態が `new` か `changed` で、error が無いもの。取り込めない対象には理由を言う
 * - 作業中のチケットが取り込みを止めるか。実行ファイルには錠が無く、`template` と `layer-config` の取り込みにだけ拡張が掛ける
 * - `board` が偽の対象（`defense`）を端末で取り込むときの案内
 *
 * VS Code の API も DOM も node も読まない（単体で試せる）。
 */
import { lockFromBoard, type Lock } from "./lock.js";
import type { BoardJson, TreeJson } from "./model.js";
import type {
  HandoffApplied,
  HandoffEntry,
  HandoffKept,
  HandoffKind,
  HandoffList,
  HandoffResult,
  HandoffTreeOption,
} from "./handoff-view.js";

/** 答えの版。欄を足すだけなら上がらない */
export const HANDOFF_VERSION = 1;

export type HandoffListParse = { readonly ok: true; readonly value: HandoffList } | { readonly ok: false; readonly error: string };
export type HandoffResultParse = { readonly ok: true; readonly value: HandoffResult } | { readonly ok: false; readonly error: string };

// ---- 読み取り

/**
 * 見るだけの答え（`user handoff [<パス>…] --json`）を読む。版が違う・欄が足りないときは文面を返す。
 * `entries` を持たない失敗の答え（`{version, ok:false, error}`）は error をそのまま返す。
 */
export function parseHandoffList(text: string): HandoffListParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.version !== HANDOFF_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  if (!Array.isArray(raw.entries)) {
    return { ok: false, error: str(raw.error) || "取り込み待ちの JSON の entries 欄が配列ではありません" };
  }
  if (typeof raw.expect !== "string") {
    return { ok: false, error: "取り込み待ちの JSON の expect 欄が文字列ではありません" };
  }
  if (typeof raw.ok !== "boolean") {
    return { ok: false, error: "取り込み待ちの JSON の ok 欄が真偽値ではありません" };
  }
  const entries: HandoffEntry[] = [];
  for (const item of raw.entries) {
    if (!isRecord(item) || typeof item.path !== "string" || item.path === "") {
      return { ok: false, error: "取り込み待ちの JSON の entries に、path の無い対象があります" };
    }
    entries.push({
      path: item.path,
      kind: str(item.kind),
      state: str(item.state),
      board: item.board === true,
      selected: item.selected === true,
      errors: strings(item.errors),
      warnings: strings(item.warnings),
      diff: str(item.diff),
      entry_sha: str(item.entry_sha),
    });
  }
  return {
    ok: true,
    value: { version: HANDOFF_VERSION, tree: str(raw.tree), ignored: num(raw.ignored), entries, expect: raw.expect, ok: raw.ok },
  };
}

/**
 * 取り込みの答え（`user handoff <パス…> --yes --expect <ハッシュ> --via board --json`）を読む。
 * 失敗の答え（`{version, ok:false, error}`）は `ok:false` の結果として返す（読み取りの失敗ではない）。
 */
export function parseHandoffResult(text: string): HandoffResultParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.version !== HANDOFF_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  if (typeof raw.ok !== "boolean") {
    return { ok: false, error: "取り込みの JSON の ok 欄が真偽値ではありません" };
  }
  if (!raw.ok) {
    return { ok: true, value: { ok: false, error: str(raw.error) || "取り込めませんでした（理由は返りませんでした）" } };
  }
  if (!Array.isArray(raw.applied)) {
    return { ok: false, error: "取り込みの JSON の applied 欄が配列ではありません" };
  }
  const applied: HandoffApplied[] = list(raw.applied)
    .filter(isRecord)
    .map((a) => ({ path: str(a.path), kind: str(a.kind) }));
  const kept: HandoffKept[] = list(raw.kept_drafts)
    .filter(isRecord)
    .map((k) => ({ path: str(k.path), reason: str(k.reason) }));
  // 消した下書きは文字列でも `{path}` でもよい（どちらで返っても読めるようにする）
  const removed = list(raw.removed_drafts)
    .map((d) => (typeof d === "string" ? d : isRecord(d) ? str(d.path) : ""))
    .filter((d) => d !== "");
  return { ok: true, value: { ok: true, applied, removedDrafts: removed, keptDrafts: kept } };
}

// ---- 取り込めるか

/** 取り込める状態。実行ファイルが知らない状態を足しても、画面が勝手に取り込めると決めないよう、この 2 つだけ */
const IMPORTABLE_STATES: readonly string[] = ["new", "changed"];

/**
 * 取り込めるか。`board` が真で、状態が `new` か `changed` で、error が無い。
 * 作業中の錠はここに含めない（錠は取り込む直前に取り直す。`lockReason`）
 */
export function isImportable(entry: HandoffEntry): boolean {
  return entry.board && IMPORTABLE_STATES.includes(entry.state) && entry.errors.length === 0;
}

/**
 * 取り込めない理由。取り込めるなら空。
 * 順は、端末でだけ取り込む対象（`board` が偽）→ 状態 → error。複数当たるときは、ユーザが次にすることが変わる順に並べた最初の 1 つを言う。
 */
export function refusalReason(entry: HandoffEntry): string {
  if (!entry.board) {
    return "ボードからは取り込めません。端末で取り込みます";
  }
  switch (entry.state) {
    case "conflict":
      return "下書きを写したあとで、取り込み先が変わっています（conflict）。下書きを写し直してから、もう一度確かめてください";
    case "rejected":
      return "取り込み先として受け付けない対象です（rejected）。errors を直してください";
    case "unchanged":
      return "取り込み先と同じ内容なので、取り込むものがありません";
    case "new":
    case "changed":
      return entry.errors.length > 0 ? "検査で error があります。直してから取り込みます" : "";
    default:
      return `知らない状態です（${entry.state}）。実行ファイルを更新してください`;
  }
}

// ---- 作業中の錠

/**
 * 作業中のチケットが取り込みを止める種類。設定と、設定を使うテンプレートは、hook が次の呼び出しから読み直すので、
 * 着手済みのチケットがある間に書くと判定が途中で変わる。`docs` はそうではないので掛けない。
 * `defense` はボードから取り込めない（端末のみ）ので、ここで掛ける意味が無い。
 */
export const LOCKED_KINDS: readonly HandoffKind[] = ["template", "layer-config"];

/** 作業中の錠が、この種類の取り込みを止める理由。止めなければ空 */
export function lockReason(kind: HandoffKind, lock: Lock): string {
  return lock.locked && LOCKED_KINDS.includes(kind) ? lock.reason : "";
}

/** 取り込めない理由（`refusalReason`）か、錠が止める理由（`lockReason`）。取り込めれば空 */
export function blockReason(entry: HandoffEntry, lock: Lock): string {
  const refusal = refusalReason(entry);
  return refusal !== "" ? refusal : lockReason(entry.kind, lock);
}

/** 見るツリーに掛ける錠。プロジェクトのツリーはそのプロジェクトの作業中だけを見る。ワークスペースは全体を見る */
export function lockForTree(board: BoardJson, tree: TreeJson): Lock {
  if (tree.kind === "project") {
    return lockFromBoard(board, tree.name);
  }
  if (tree.kind === "worktree" && tree.project !== "") {
    return lockFromBoard(board, tree.project);
  }
  return lockFromBoard(board);
}

// ---- 見るツリー

/** ツリーの選択肢の `key`。名前に `:` が入っていても、先頭の種類は `:` を含まないので戻せる */
export function treeKey(tree: { readonly kind: string; readonly name: string }): string {
  return `${tree.kind}:${tree.name}`;
}

/** ワークスペースのツリー（ボードの `main`）の `key` */
export const MAIN_TREE_KEY = "main:";

function treeLabel(tree: TreeJson): string {
  return keyLabel(treeKey(tree));
}

/** `key` から表示名を作る。ボードを読む前（読み込み中の一言）にも使う。`main:` はワークスペース */
export function keyLabel(key: string): string {
  const at = key.indexOf(":");
  const kind = at < 0 ? key : key.slice(0, at);
  const name = at < 0 ? "" : key.slice(at + 1);
  switch (kind) {
    case "main":
      return "ワークスペース";
    case "project":
      return `プロジェクト ${name}`;
    default:
      return `ワークツリー ${name}`;
  }
}

/** 切り替えられるツリー。ワークスペース、プロジェクト、ワークツリーの順。ボードを読めていなければワークスペースだけ */
export function treeOptions(board: BoardJson | undefined): HandoffTreeOption[] {
  if (board === undefined) {
    return [{ key: MAIN_TREE_KEY, label: "ワークスペース" }];
  }
  const rank = (t: TreeJson): number => (t.kind === "main" ? 0 : t.kind === "project" ? 1 : 2);
  const sorted = board.trees.map((t, index) => ({ t, index })).sort((a, b) => rank(a.t) - rank(b.t) || a.index - b.index);
  const options = sorted.map(({ t }) => ({ key: treeKey(t), label: treeLabel(t) }));
  return options.some((o) => o.key === MAIN_TREE_KEY) ? options : [{ key: MAIN_TREE_KEY, label: "ワークスペース" }, ...options];
}

/** `key` が指すツリー。ボードに無ければ undefined。`main` は `trees` に無くてもワークスペースのルートを指す */
export function findTree(board: BoardJson, key: string): TreeJson | undefined {
  return board.trees.find((t) => treeKey(t) === key);
}

// ---- 取り込みの手順で使う判断

/** パスを比べるための形。大文字小文字だけ違うパスは同じものとして扱う（どの機械でも区別しない）。表示や書き戻しには使わない */
export function foldPath(path: string): string {
  return path.normalize("NFC").toLowerCase();
}

/** 同じパスか（大文字小文字と NFC/NFD の違いは無視する） */
export function samePath(a: string, b: string): boolean {
  return foldPath(a) === foldPath(b);
}

/** 一覧にあるパスを引く */
export function entryOf(entries: readonly HandoffEntry[], path: string): HandoffEntry | undefined {
  return entries.find((e) => samePath(e.path, path));
}

/** いま選べる（取り込める）対象のパス。錠が止める対象は含めない */
export function selectablePaths(entries: readonly HandoffEntry[], lock: Lock): string[] {
  return entries.filter((e) => blockReason(e, lock) === "").map((e) => e.path);
}

/**
 * 一覧を読み直したあとの選択。選んでいたもののうち、同じパスで `entry_sha` が変わらず、いまも取り込める対象だけを残す
 * （見せた差分と違うものを、選んだままにしない）。選択は パス → 選んだときの `entry_sha`。
 */
export function reconcileSelection(previous: ReadonlyMap<string, string>, entries: readonly HandoffEntry[], lock: Lock): Map<string, string> {
  const kept = new Map<string, string>();
  for (const [path, sha] of previous) {
    const now = entryOf(entries, path);
    if (now !== undefined && now.entry_sha === sha && blockReason(now, lock) === "") {
      kept.set(now.path, sha);
    }
  }
  return kept;
}

/**
 * 端末で打つ取り込みのコマンド。`expect` は、そのパスを指して見るだけをもう一度呼んで返った値。
 * `tree` は見るだけの答えの `tree`（下書きがあるツリー）。実行ファイルは `--root` で指したツリーの
 * `wip/handoff/` だけを見るので、ユーザが別の場所で端末を開いていても同じツリーへ取り込むように付ける。空なら付けない。
 *
 * 引用は POSIX の sh の形（単引用符と `'\''`）。Windows の PowerShell は単引用符の中の `'` を `''` で表し、
 * `'\''` を読めないので、パスかツリーに `'` を含むときは、PowerShell では `'\''` を `''` に打ち直す。
 * 空白や `\` だけなら、単引用符で囲んだ形は PowerShell でも同じに読める。
 */
export function terminalCommand(path: string, expect: string, tree = ""): string {
  const root = tree === "" ? "" : `--root ${quoteArg(tree)} `;
  return `ccnavi ${root}user handoff ${quoteArg(path)} --yes --expect ${expect}`;
}

/**
 * `terminalCommand` が組んだ形か。クリップボードへ写してよい文面の目印に使う（画面が出したコマンド以外は写さない）。
 * `ccnavi user handoff …` か、`ccnavi --root <ツリー> user handoff …`
 */
export function isTerminalCommand(text: string): boolean {
  return /^ccnavi (?:--root (?:[A-Za-z0-9_./@%+=:,-]+|'(?:[^']|'\\'')*') )?user handoff /.test(text);
}

/**
 * 空白や記号を含むときだけ単引用符で囲む（中の単引用符は `'\''` に置き換える）。
 * `commands.ts` の `shellQuote` と同じ規則だが、あちらは node のモジュールを読むので、画面にバンドルされるここでは持ち直す
 */
function quoteArg(text: string): string {
  return /^[A-Za-z0-9_./@%+=:,-]+$/.test(text) ? text : `'${text.replace(/'/g, `'\\''`)}'`;
}

/** 取り込みの結果を、画面の帯に出す 1 行にする */
export function resultSummary(result: HandoffResult): string {
  if (!result.ok) {
    return `取り込めませんでした: ${result.error}`;
  }
  const head = `取り込みました（${result.applied.length} 件）`;
  const kept = result.keptDrafts.length;
  const removed = result.removedDrafts.length;
  const parts = [head];
  if (removed > 0) {
    parts.push(`下書きを ${removed} 件削除しました`);
  }
  if (kept > 0) {
    parts.push(`下書きを ${kept} 件残しました`);
  }
  return parts.join("。");
}

// ---- 共通の部品

/** JSON として読み、最上位がオブジェクトのものを返す。読めなければ文面 */
function parseRecord(text: string): { readonly ok: true; readonly value: Record<string, unknown> } | { readonly ok: false; readonly error: string } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として解釈できません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  return { ok: true, value: raw };
}

function versionMismatch(version: unknown): string {
  return `取り込み待ちの JSON の形が違います（拡張は ${HANDOFF_VERSION}、実行ファイルは ${String(version)}）`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return list(value).filter((v): v is string => typeof v === "string");
}

function num(value: unknown): number {
  return typeof value === "number" ? value : 0;
}
