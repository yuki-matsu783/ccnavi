/**
 * 「マージ状況を確認」の遷移と、画面に渡す形。「いまの状態 ＋ 入力 → 次の状態 ＋ やること」だけをここに置く。
 * VS Code の API には触れない（外へ出る仕事は `MergeEffect` として返すだけで、行うのは `board-panel.ts`）。
 * `approval-machine.ts` と同じ段取り: 返った状態を持ち直し、描き直し、やることを順に行う。
 *
 * 結果は、ボードが開いている間だけ拡張ホストが持つ（パネルの状態）。タブを隠して画面が作り直されても残り、
 * ボードを閉じて開き直すと消える。**頼んだ状態でなければ受けない**: 確認ごとに世代（`token`）を振り、
 * 閉じたボードへ遅れて返った結果や、前の確認の遅れた答えは捨てる。確認中にもう一度押しても二重に走らせない。
 *
 * ## ガード（消すとどうなるか）
 *
 * | ガード | 消すとどうなる |
 * |---|---|
 * | 確認中の「確認」は受けない | 二重に走って、遅い答えが新しい答えを上書きする |
 * | 一覧と状態を受けるのは、頼んだ世代の確認中だけ | 前の確認の遅れた答えや、閉じたあとの答えが画面に出る |
 * | 状態が分かった親が全部そろったら確認を終える | 確認中のまま戻らない |
 * | 「片付ける」はマージ済みと確認できた親だけ | マージ待ち・閉じた・確認できない親の片付けが起きる |
 * | 同じ親の「片付ける」は、ダイアログを開いている間と送っている間は再入させない | 確認が二重に出て、コマンドが 2 回送られる |
 * | 送ったあとは、次の確認まで押せない | 同じコマンドを何度も送る |
 * | 送れなかったときは「送信しました」と言わず、理由を出して押し直せる | 送れていないのに済んだと読まれる |
 */
import type { IncompleteEntry, MrStateResult, RetiredEntry, RetiredParse } from "./mrstate.js";
import { projectOfRepo } from "./mrstate.js";

/** 画面の 1 行の確認結果 */
export type RowResult = MrStateResult | { readonly kind: "incomplete"; readonly reason: string };

export interface MergeRow {
  readonly parent: string;
  /** ボードの `project`（ワークスペースは空） */
  readonly project: string;
  /** `self` かプロジェクトの名前 */
  readonly repo: string;
  readonly mr: number | null;
  /** 確認の結果。まだなら無い（確認中） */
  readonly result?: RowResult;
}

export type MergeList =
  /** まだ確認していない */
  | { readonly kind: "none" }
  /** 一覧を取れなかった（形式の異常など）。「ありません」とは言わない */
  | { readonly kind: "error"; readonly error: string }
  | { readonly kind: "rows"; readonly rows: readonly MergeRow[] };

export interface MergeState {
  /** 確認中か。真の間は「確認」を受けない */
  readonly checking: boolean;
  /** 確認の世代。確認を始めるたびに増える。遅れて返った答えを見分ける */
  readonly token: number;
  /** 最後に確認し終えた時刻（画面に出す文字列）。まだなら空 */
  readonly checkedAt: string;
  readonly list: MergeList;
  /** 片付けのコマンドを送った親（次の確認まで押せない） */
  readonly sent: readonly string[];
  /** 片付けの確認のダイアログを開いている・コマンドを送っている最中の親（再入させない） */
  readonly confirming: readonly string[];
  /** 送れなかった親と理由（押し直せる） */
  readonly sendErrors: Readonly<Record<string, string>>;
}

export const MERGE_IDLE: MergeState = { checking: false, token: 0, checkedAt: "", list: { kind: "none" }, sent: [], confirming: [], sendErrors: {} };

export type MergeInput =
  /** 「マージ状況を確認」を押した */
  | { readonly kind: "check" }
  /** 一覧（`sync retired`）が返った */
  | { readonly kind: "listed"; readonly token: number; readonly result: RetiredParse; readonly at: string }
  /** 1 件の `mr-state` が返った（失敗・期限切れもここに入る。その親だけに留める） */
  | { readonly kind: "stated"; readonly token: number; readonly parent: string; readonly result: MrStateResult; readonly at: string }
  /** 「片付ける」を押した */
  | { readonly kind: "retire"; readonly parent: string }
  /** 確認のダイアログに答えた（`accepted` は「実行する」を選んだか） */
  | { readonly kind: "retireAnswered"; readonly parent: string; readonly accepted: boolean }
  /** 片付けのコマンドをターミナルへ送った結果（送れなかったときは理由）。送った＝実行の成功ではない */
  | { readonly kind: "retireSent"; readonly parent: string; readonly error: string | undefined };

export type MergeEffect =
  /** 片付けの記録を持つ親の一覧を読む（`ccnavi sync retired`）。返ったら `listed` で戻す */
  | { readonly kind: "loadList"; readonly token: number }
  /** 1 件の MR の状況を聞く（そのリポジトリを cwd にして `ccnavi-review.sh mr-state <番号>`）。返ったら `stated` で戻す */
  | { readonly kind: "loadState"; readonly token: number; readonly parent: string; readonly repo: string; readonly mr: number }
  /** 片付けの確認のダイアログを出す。答えは `retireAnswered` で戻す */
  | { readonly kind: "confirmRetire"; readonly parent: string }
  /** 片付けのコマンドをターミナルへ送る。結果は `retireSent` で戻す */
  | { readonly kind: "sendRetire"; readonly parent: string }
  /** ユーザに言う（警告） */
  | { readonly kind: "warn"; readonly text: string };

export interface MergeStep {
  readonly state: MergeState;
  /** 画面に渡す中身が変わったので描き直す。やることより先に行う */
  readonly redraw: boolean;
  readonly effects: readonly MergeEffect[];
}

function stay(state: MergeState, ...effects: MergeEffect[]): MergeStep {
  return { state, redraw: false, effects };
}

function move(state: MergeState, ...effects: MergeEffect[]): MergeStep {
  return { state, redraw: true, effects };
}

export function mergeStep(state: MergeState, input: MergeInput): MergeStep {
  switch (input.kind) {
    case "check":
      return checkStarted(state);
    case "listed":
      return listed(state, input);
    case "stated":
      return stated(state, input);
    case "retire":
      return retireAsked(state, input.parent);
    case "retireAnswered":
      return retireAnswered(state, input.parent, input.accepted);
    case "retireSent":
      return retireSent(state, input.parent, input.error);
  }
}

/** 確認を始める。確認中は受けない。送った印と送れなかった理由は、ここで消える（次の確認まで押せない、の「次」） */
function checkStarted(state: MergeState): MergeStep {
  if (state.checking) {
    return stay(state);
  }
  const token = state.token + 1;
  return move(
    { ...state, checking: true, token, list: { kind: "none" }, sent: [], sendErrors: {} },
    { kind: "loadList", token },
  );
}

/** 頼んだ世代の確認中か */
function current(state: MergeState, token: number): boolean {
  return state.checking && token === state.token;
}

function listed(state: MergeState, input: { readonly token: number; readonly result: RetiredParse; readonly at: string }): MergeStep {
  if (!current(state, input.token)) {
    return stay(state);
  }
  const { result } = input;
  if (!result.ok) {
    return move({ ...state, checking: false, checkedAt: input.at, list: { kind: "error", error: result.error } });
  }
  const rows: MergeRow[] = [
    ...result.entries.map((e) => rowOf(e)),
    ...result.incomplete.map((e) => incompleteRow(e)),
  ];
  const effects: MergeEffect[] = result.entries.flatMap((e): MergeEffect[] =>
    e.mr === null ? [] : [{ kind: "loadState", token: input.token, parent: e.parent, repo: e.repo, mr: e.mr }],
  );
  const next: MergeState = { ...state, list: { kind: "rows", rows } };
  return move(settle(next, input.at), ...effects);
}

function rowOf(e: RetiredEntry): MergeRow {
  const base = { parent: e.parent, project: projectOfRepo(e.repo), repo: e.repo, mr: e.mr };
  // 記録に MR の番号が無い（読めない）親は聞きようが無い。確認できないものとして、その親だけに留める
  return e.mr === null
    ? { ...base, result: { kind: "unknown", reason: "no-number", detail: "片付けの記録に MR の番号がありません" } }
    : base;
}

function incompleteRow(e: IncompleteEntry): MergeRow {
  return { parent: e.parent, project: "", repo: "", mr: null, result: { kind: "incomplete", reason: e.reason === "" ? "理由は分かりません" : e.reason } };
}

function stated(
  state: MergeState,
  input: { readonly token: number; readonly parent: string; readonly result: MrStateResult; readonly at: string },
): MergeStep {
  if (!current(state, input.token) || state.list.kind !== "rows") {
    return stay(state);
  }
  const rows = state.list.rows;
  const target = rows.find((r) => r.parent === input.parent);
  // 頼んでいない親の答えは受けない。すでに結果がある親（二重の答え）も受けない
  if (target === undefined || target.result !== undefined) {
    return stay(state);
  }
  const next: MergeState = {
    ...state,
    list: { kind: "rows", rows: rows.map((r) => (r === target ? { ...r, result: input.result } : r)) },
  };
  return move(settle(next, input.at));
}

/** 全部の親に結果がそろったら、確認を終える */
function settle(state: MergeState, at: string): MergeState {
  if (state.list.kind !== "rows" || state.list.rows.some((r) => r.result === undefined)) {
    return state;
  }
  return { ...state, checking: false, checkedAt: at };
}

function rowOfParent(state: MergeState, parent: string): MergeRow | undefined {
  return state.list.kind === "rows" ? state.list.rows.find((r) => r.parent === parent) : undefined;
}

function retireAsked(state: MergeState, parent: string): MergeStep {
  const row = rowOfParent(state, parent);
  // マージ済みと確認できた親だけ。画面が古いボードのまま押した場合もここで止める
  if (row === undefined || row.result?.kind !== "merged") {
    return stay(state, { kind: "warn", text: `親 ${parent} は、マージ済みと確認できていません。もう一度「マージ状況を確認」を押してください` });
  }
  // ダイアログを開いている間・送っている間・送ったあとは再入させない
  if (state.confirming.includes(parent) || state.sent.includes(parent)) {
    return stay(state);
  }
  return move({ ...state, confirming: [...state.confirming, parent] }, { kind: "confirmRetire", parent });
}

function retireAnswered(state: MergeState, parent: string, accepted: boolean): MergeStep {
  if (!state.confirming.includes(parent)) {
    return stay(state);
  }
  if (!accepted) {
    return move({ ...state, confirming: state.confirming.filter((p) => p !== parent) });
  }
  // 送り終わるまで confirming に残す（その間の再入を止める）
  return stay(state, { kind: "sendRetire", parent });
}

function retireSent(state: MergeState, parent: string, error: string | undefined): MergeStep {
  if (!state.confirming.includes(parent)) {
    return stay(state);
  }
  const confirming = state.confirming.filter((p) => p !== parent);
  const rest = Object.fromEntries(Object.entries(state.sendErrors).filter(([p]) => p !== parent));
  if (error !== undefined) {
    return move({ ...state, confirming, sendErrors: { ...rest, [parent]: error } });
  }
  return move({ ...state, confirming, sent: [...state.sent, parent], sendErrors: rest });
}

// --- 画面に渡す形 ---

export type MergeRowState = "checking" | "open" | "merged" | "closed" | "unknown" | "incomplete";

export interface MergeViewRow {
  readonly parent: string;
  readonly project: string;
  readonly mr: number | null;
  readonly state: MergeRowState;
  /** open のとき Draft か */
  readonly draft: boolean;
  /** open / merged / closed のとき、MR の URL（画面は http(s) のときだけリンクにする） */
  readonly url: string;
  /** unknown のとき、種類（`UnknownKind`）。incomplete のときは空 */
  readonly reason: string;
  /** unknown / incomplete のとき、理由 */
  readonly detail: string;
  /** 片付けのコマンドを送った（次の確認まで押せない） */
  readonly sent: boolean;
  /** 確認のダイアログを開いている・送っている最中 */
  readonly busy: boolean;
  /** 送れなかった理由。空なら無い */
  readonly sendError: string;
  /**
   * closed のとき、捨てた親の記録を消すコマンド（`sh <ルート>/.ccnavi/scripts/ccnavi-launcher.sh worktree retire --abandon <親>`）。
   * 見せるだけで、ボードからは送らない（コピーのボタンも拡張ホストが同じ関数で組み直したものをコピーする）
   */
  readonly abandon: string;
}

export interface MergeView {
  readonly checking: boolean;
  /** 最後に確認し終えた時刻。まだなら空 */
  readonly checkedAt: string;
  /** 一覧を取れなかった理由（形式の異常など）。空なら取れた */
  readonly error: string;
  /** 取れた一覧。取れなかったとき・まだ取れていないときは空 */
  readonly rows: readonly MergeViewRow[];
}

/**
 * 画面に渡す形にする。まだ一度も確認していなければ undefined（帯を出さない）。
 * `abandonOf` は捨てた親の記録を消すコマンドの組み方（`commands.ts` の `abandonCommand`。node を読む部品はここに入れない）
 */
export function mergeView(state: MergeState, abandonOf: (parent: string) => string): MergeView | undefined {
  if (!state.checking && state.checkedAt === "" && state.list.kind === "none") {
    return undefined;
  }
  return {
    checking: state.checking,
    checkedAt: state.checkedAt,
    error: state.list.kind === "error" ? state.list.error : "",
    rows: state.list.kind === "rows" ? state.list.rows.map((row) => viewRow(state, row, abandonOf)) : [],
  };
}

function viewRow(state: MergeState, row: MergeRow, abandonOf: (parent: string) => string): MergeViewRow {
  const result = row.result;
  const base = {
    parent: row.parent,
    project: row.project,
    mr: row.mr,
    sent: state.sent.includes(row.parent),
    busy: state.confirming.includes(row.parent),
    sendError: state.sendErrors[row.parent] ?? "",
    abandon: result?.kind === "closed" ? abandonOf(row.parent) : "",
  };
  if (result === undefined) {
    return { ...base, state: "checking", draft: false, url: "", reason: "", detail: "" };
  }
  switch (result.kind) {
    case "open":
      return { ...base, state: "open", draft: result.draft, url: result.url, reason: "", detail: "" };
    case "merged":
      return { ...base, state: "merged", draft: false, url: result.url, reason: "", detail: "" };
    case "closed":
      return { ...base, state: "closed", draft: false, url: result.url, reason: "", detail: "" };
    case "unknown":
      return { ...base, state: "unknown", draft: false, url: "", reason: result.reason, detail: result.detail };
    case "incomplete":
      return { ...base, state: "incomplete", draft: false, url: "", reason: "", detail: result.reason };
  }
}
