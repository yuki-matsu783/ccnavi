/**
 * フロー編集画面の、拡張ホストと Webview の間の契約。フェーズ管理（`phases-view.ts`）と同じ作り。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない。更新のたびに画面を作り直さず、画面の中身にも型検査を効かせるため。渡すのは「いま何を見せるか」
 * （`FlowData`）だけで、画面が返すのはユーザが押した操作（`FlowMessage`）だけ。
 *
 * **着手中かどうかを画面は決めない**。錠は実行ファイルの `--explain --json` の
 * `tickets[].flow.locked` をそのまま使う（`flowTargetOf`）。画面はそれを見て欄を止めるだけで、
 * `started_at` などから組み直さない。保存の直前にも拡張ホストが実行ファイルに聞き直す。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、
 * 入れ物は 1 度しか入らない（入れ直すと打ちかけの編集が消える）。中身が届くのは、画面の編集を捨ててよいときだけ。
 *
 * ここには VS Code の API も DOM も node も入れない。
 */
import type { AppearanceMessage } from "./appearance.js";
import { asFlowDoc, type FlowDoc } from "./flow-doc.js";
import type { LintFlowCandidates } from "./lintmodel.js";
import type { BoardJson, FlowJson } from "./model.js";
import { embedJson, type DataMessage } from "./screen-host.js";

/** 書けるか。止めているなら理由 */
export interface FlowLock {
  readonly locked: boolean;
  readonly reason: string;
}

export const OPEN_LOCK: FlowLock = { locked: false, reason: "" };

/** 実行ファイルが着手中と言った子の錠の文面。いつ外れるかまで言う */
export function lockedReason(ticket: string): string {
  return (
    `子チケット ${ticket} は着手中のため、フローを書き換えられません（ccnavi が DENY_TICKET_FLOW_LOCKED で止めています）。` +
    "担当のサブエージェントが読んでいる手順が、作業の途中で変わるのを防ぐためです。" +
    `ロックは、${ticket} が finish で終わるか cancel で取り消されると外れます。手順を直すなら、終わってから直すか、次の子チケットのフローに書いてください`
  );
}

/** 置き場の途中かファイルがシンボリックリンク。読まないし書かない */
export function linkedReason(rel: string): string {
  return `フローの置き場（${rel}）か、そこへ至る途中のフォルダがシンボリックリンクのため、読み書きしません。リンク先は承認済みの領域の外かもしれません。リンクを外してから開き直してください`;
}

/** 確かめられなかったとき。書けない扱いにする */
export function lockFromFailure(error: string): FlowLock {
  return { locked: true, reason: `着手中かどうかを確かめられないため、書き込みません: ${error}` };
}

/** ボードの JSON から引いた、この子のフロー */
export interface FlowTarget {
  readonly ticket: string;
  readonly title: string;
  readonly parent: string;
  readonly project: string;
  readonly flow: FlowJson;
  readonly lock: FlowLock;
  /**
   * 着手の前（承認待ちか、承認済みで未着手）か。エージェントへの依頼のボタンを出すかだけに使い、
   * 保護には使わない（取り込みと保存を止めるのは錠。錠は実行ファイルの答えのまま）
   */
  readonly beforeStart: boolean;
}

export type FlowTargetResult = { readonly ok: true; readonly target: FlowTarget } | { readonly ok: false; readonly error: string };

/**
 * ボードの JSON から子のフローを引く。錠は `flow.locked` をそのまま使う。無い子・親・フローの欄が無いものは引けない。
 */
export function flowTargetOf(board: BoardJson, ticket: string): FlowTargetResult {
  const found = board.tickets.find((t) => t.ticket === ticket);
  if (found === undefined) {
    return { ok: false, error: `チケット ${ticket} が実行ファイルの出力にありません` };
  }
  if (found.parent === "") {
    return { ok: false, error: `${ticket} は親チケットです。フローを持つのは子チケットだけです` };
  }
  if (found.flow === null) {
    return { ok: false, error: `${ticket} のフローの置き場が実行ファイルの出力にありません（完了・取り消しの子でファイルが無いか、実行ファイルが古いかのどちらかです）` };
  }
  return {
    ok: true,
    target: {
      ticket,
      title: found.title,
      parent: found.parent,
      project: found.project,
      flow: found.flow,
      beforeStart: found.started_at === "" && found.completed_at === "" && found.cancelled_at === "",
      lock: found.flow.locked
        ? { locked: true, reason: lockedReason(ticket) }
        : found.flow.linked
          ? { locked: true, reason: linkedReason(found.flow.rel) }
          : found.flow.tree === ""
            ? lockFromFailure("フローを持つツリーが実行ファイルの出力にありません（実行ファイルが古いためです）")
            : OPEN_LOCK,
    },
  };
}

/** カードの「フロー」ボタンの言葉。在るか・着手中かで変わる（どちらも実行ファイルの答えのまま） */
export function flowButtonLabel(flow: FlowJson): string {
  if (flow.locked) {
    return "フロー: 閲覧（着手中）";
  }
  return flow.exists ? "フロー: 編集" : "フロー: 作成";
}

// ---- エージェントへの依頼と、下書きの取り込み

/**
 * 「エージェントにフローの作成を頼む」ボタンの言葉。出さないなら undefined。
 *
 * 出すのは着手の前（承認待ち・承認済みで未着手）で、錠が掛かっていない（着手中・リンク・確認中でない）ときだけ。
 * 着手中は取り込めないので、頼んでも使えない下書きができる。下書きが既に在れば「頼み直す」
 */
export function requestLabel(state: {
  readonly beforeStart: boolean;
  readonly locked: boolean;
  readonly flowExists: boolean;
  readonly draftExists: boolean;
}): string | undefined {
  if (!state.beforeStart || state.locked) {
    return undefined;
  }
  if (state.draftExists) {
    return "エージェントにフローを頼み直す";
  }
  return state.flowExists ? "エージェントにフローの直しを頼む" : "エージェントにフローの作成を頼む";
}

/** 依頼の文の材料。パスは絶対（エージェントはどの cwd からでも開ける） */
export interface FlowRequest {
  readonly ticket: string;
  readonly title: string;
  readonly parent: string;
  /** 下書きを書く置き場（`tickets[].flow.draft.path`） */
  readonly draftPath: string;
  /** いまのフロー（在るときだけ。`tickets[].flow.path`） */
  readonly flowPath?: string;
  /** 下書きが既に在る（頼み直す） */
  readonly redo: boolean;
  /** 確かめる 1 行（`ccnavi --lint --flow <下書き>` を打てる形） */
  readonly lintCommand: string;
}

/**
 * エージェントへの依頼の文。承認の文と同じく「コピー / 新しいセッションで開く」で渡す。
 * 依頼は書き込みの許可条件ではない（下書きの置き場は頼まなくても書ける）。文が言うのは、どの子の・どこに書くか・
 * 何を確かめてから渡すか・どこに書かないか、だけ
 */
export function flowRequestPrompt(request: FlowRequest): string {
  const what = request.flowPath === undefined ? "作成" : "直し";
  const head = request.title === "" ? request.ticket : `${request.ticket}（${request.title}）`;
  const lines = [`[ccnavi] ユーザが子チケット ${head} のフローの${what}を頼んだ（親 ${request.parent}）。`];
  lines.push(`- 下書きを書く置き場: ${request.draftPath}（YAML。形はフロー編集画面が書くものと同じ: id・name・version・nodes・connections）`);
  if (request.flowPath !== undefined) {
    lines.push(`- いまのフロー: ${request.flowPath}（読んで、直すところだけ変えた全体を下書きに書く）`);
  }
  if (request.redo) {
    lines.push("- 前の下書きが残っている。読んで、書き直してよい");
  }
  lines.push(
    `- 書いたら \`${request.lintCommand}\` で確かめ、error が無くなってからユーザに渡す`,
    "- 効力のあるフロー（.ccnavi/approved/flows/）には書かない。書くのは下書きだけで、ユーザがフロー編集画面で差分を読んで取り込む",
  );
  return lines.join("\n");
}

/** 「提案あり」。下書きが在り、中身がいまのフローと違う（`sameFlow` が偽）か、読めない */
export interface FlowOffer {
  /** 下書きのファイル（ワークスペースルートからの相対で見せる。外なら絶対） */
  readonly draftPath: string;
  /** 読めない理由（リンク・ハードリンク・YAML として読めない など）。読めれば無い */
  readonly problem?: string;
}

/** 開いた下書き。実行ファイルの `--lint --flow` が通り、画面の読みとも食い違わなかった中身 */
export interface FlowProposal {
  readonly doc: FlowDoc;
  /** 読んだバイトのハッシュ（sha256 の 16 進）。取り込んで保存したあと、同じ中身のときだけ下書きを消す */
  readonly hash: string;
  readonly draftPath: string;
}

// ---- 画面に見せる形

/**
 * 実行ファイル（`--lint --json --flow`）がフローについて言ったこと。画面は判定し直さず、そのまま見せる。
 */
export interface FlowChecks {
  /** `(flow)` の warn（線の構造・名前の表記）。一時ファイルのパスは対象のファイルのパスに直してある */
  readonly warns: readonly string[];
  /**
   * `SubagentStart` で担当に渡る手順の行。null は並べられなかった。無ければ実行ファイルが古くて答えに欄が無い
   */
  readonly rendered?: readonly string[] | null;
  /** フローで選べるサブエージェントとスキルの名前。無ければ実行ファイルが古い */
  readonly candidates?: LintFlowCandidates;
}

export interface FlowPage {
  readonly root: string;
  readonly ticket: string;
  readonly title: string;
  readonly parent: string;
  /** フローのファイル（ワークスペースルートからの相対で見せる。外なら絶対） */
  readonly flowPath: string;
  /** ツリーのルートからの相対（承認済みの領域の固定の置き場） */
  readonly flowRel: string;
  /** ファイルが在るか。無ければ雛形（開始 → 終了）を見せ、保存でファイルを作る */
  readonly exists: boolean;
  readonly doc: FlowDoc;
  readonly lock: FlowLock;
  /**
   * 保存の前に差分の一覧を見せて確かめるか（設定 `ccnaviBoard.flowSaveReview`）。真のときだけ見せ、無ければ見せない。
   * 拡張ホストは設定の値を必ず渡す（設定の既定は見せる）
   */
  readonly reviewSave?: boolean;
  /**
   * 未保存のまま閉じた画面から戻す編集中のコピー。あれば画面は `doc` の代わりにこれを開き、
   * `doc`（読み込んだ中身）と比べて未保存にする
   */
  readonly draft?: FlowDoc;
  /** 開くときに実行ファイルが `doc` について言ったこと */
  readonly checks?: FlowChecks;
  /** エージェントへの依頼のボタンの言葉（`requestLabel`）。無ければ出さない */
  readonly request?: string;
  /** 「提案あり」。無ければ下書きが無いか、いまのフローと同じ */
  readonly offer?: FlowOffer;
}

export type FlowData =
  | { readonly kind: "page"; readonly page: FlowPage }
  | { readonly kind: "error"; readonly error: string }
  | { readonly kind: "loading"; readonly text: string };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める */
export type ToFlow =
  | DataMessage<FlowData>
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "lock"; readonly lock: FlowLock }
  | { readonly type: "changed" }
  /** 頼んだ往復が起きなかった（ユーザが確認をやめた）。画面は欄を戻す */
  | { readonly type: "cancelled" }
  | { readonly type: "tour" }
  /** 頼まれた確かめ（`check`）の答え。`seq` は頼んだときの番号。確かめられなければ `error` */
  | { readonly type: "checked"; readonly seq: number; readonly checks?: FlowChecks; readonly error?: string }
  /** 下書きか錠が動いた。依頼のボタンの言葉と「提案あり」を出し直す（編集は捨てない） */
  | { readonly type: "offer"; readonly request?: string; readonly offer?: FlowOffer }
  /** 開いた下書き（`openProposal` の答え）。取り込めなければ `error` */
  | { readonly type: "proposal"; readonly proposal?: FlowProposal; readonly error?: string }
  /** 依頼の文（`request` の答え）。画面は文と「コピー / 新しいセッションで開く」を出す */
  | { readonly type: "requestText"; readonly prompt: string }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側は `asFlowMessage` で形を確かめてから使う */
export type FlowMessage =
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "dirty"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  /** `imported` は、読み込んでから取り込んだ下書きのハッシュ。保存が通ったら、下書きがまだ同じ中身なら消す */
  | { readonly type: "save"; readonly doc: FlowDoc; readonly imported?: string }
  /** 編集中のコピー。未保存のまま閉じられたときに戻すため、拡張ホストが覚えておく。未保存でなくなったら null */
  | { readonly type: "draft"; readonly doc: FlowDoc | null }
  /** 保存の前に差分を確かめるか（設定に書く） */
  | { readonly type: "reviewSave"; readonly value: boolean }
  /** 編集中のコピーを実行ファイルに確かめさせる（渡る手順・warn・候補を取り直す）。書きはしない */
  | { readonly type: "check"; readonly seq: number; readonly doc: FlowDoc }
  | { readonly type: "tourDone" }
  /** 「提案あり」を開く。拡張ホストが下書きを読んで実行ファイルに確かめさせ、`proposal` で返す */
  | { readonly type: "openProposal" }
  /** エージェントへの依頼の文を組む */
  | { readonly type: "request" }
  /** 依頼の文をコピーする・新しいセッションで開く。文は拡張ホストが持っている分を使う */
  | { readonly type: "requestCopy" }
  | { readonly type: "requestOpen" };

/**
 * 画面から届いたものを確かめる。**形が崩れていたら捨てる**（保存の中身が読めないものを書かない）。
 */
export function asFlowMessage(message: unknown): FlowMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; doc?: unknown; value?: unknown; seq?: unknown; imported?: unknown };
  switch (m.type) {
    case "ready":
    case "openFile":
    case "tourDone":
    case "openProposal":
    case "request":
    case "requestCopy":
    case "requestOpen":
      return { type: m.type };
    case "reload":
      return { type: "reload", dirty: m.dirty === true };
    case "dirty":
      return typeof m.dirty === "boolean" ? { type: "dirty", dirty: m.dirty } : undefined;
    case "save": {
      const doc = asFlowDoc(m.doc);
      if (doc === undefined) {
        return undefined;
      }
      // ハッシュの形（sha256 の 16 進）でなければ、取り込みは無かったものとして扱う（下書きを消さない側）
      return typeof m.imported === "string" && /^[0-9a-f]{64}$/.test(m.imported) ? { type: "save", doc, imported: m.imported } : { type: "save", doc };
    }
    case "draft": {
      if (m.doc === null) {
        return { type: "draft", doc: null };
      }
      const doc = asFlowDoc(m.doc);
      return doc === undefined ? undefined : { type: "draft", doc };
    }
    case "check": {
      const doc = asFlowDoc(m.doc);
      return doc === undefined || typeof m.seq !== "number" || !Number.isFinite(m.seq) ? undefined : { type: "check", seq: m.seq, doc };
    }
    case "reviewSave":
      return typeof m.value === "boolean" ? { type: "reviewSave", value: m.value } : undefined;
    default:
      return undefined;
  }
}

/**
 * 識別子の形（Python の `ticket._ID` と同じ。先頭は ASCII の英数字、続きは英数字・`.`・`_`・`-` と
 * ひらがな・カタカナ・長音記号・CJK 統合漢字・々）
 */
const TICKET_ID = /^[A-Za-z0-9][A-Za-z0-9._\-\u3005\u3041-\u3096\u30a1-\u30fa\u30fc\u4e00-\u9fff]*$/u;

/**
 * ボードの「フロー」ボタンの中身（`{type: "flow", ticket}`）の識別子。形が違えば undefined。
 * 識別子に使えない表記（空・空白・パスの区切り）は受けない。開く先は拡張ホストがボードの答えから引き直す。
 */
export function flowTicketOf(message: { readonly ticket?: unknown }): string | undefined {
  const ticket = message.ticket;
  if (typeof ticket !== "string" || !TICKET_ID.test(ticket)) {
    return undefined;
  }
  return ticket;
}

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-flow-data";

export function embedData(data: FlowData): string {
  return embedJson(data);
}
