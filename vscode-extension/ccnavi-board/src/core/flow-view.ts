/**
 * フロー編集画面の、拡張ホストと Webview の間の契約。フェーズ管理（`phases-view.ts`）と同じ作り。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない（ADR-0064）。渡すのは「いま何を見せるか」
 * （`FlowData`）だけで、画面が返すのは人が押した操作（`FlowMessage`）だけ。
 *
 * **着手中かどうかを画面は決めない**（ADR-0035・ADR-0085）。錠は実行ファイルの `--explain --json` の
 * `tickets[].flow.locked` をそのまま写す（`flowTargetOf`）。画面はそれを見て欄を止めるだけで、
 * `started_at` などから組み直さない。保存の直前にも拡張ホストが実行ファイルに聞き直す。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、
 * 入れ物は 1 度しか入らない（ADR-0062）。中身が届くのは、画面の編集を捨ててよいときだけ。
 *
 * ここには VS Code の API も DOM も node も入れない。
 */
import type { AppearanceMessage } from "./appearance.js";
import { asFlowDoc, type FlowDoc } from "./flow-doc.js";
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
    `子チケット ${ticket} は着手中なので、フローは書き換えられない（ccnavi が DENY_TICKET_FLOW_LOCKED で止めている）。` +
    "担当のサブエージェントが読んでいる手順が作業の途中で変わるのを防ぐため。" +
    `ロックは ${ticket} が finish で終わるか cancel で取り消されると外れる。手順を直すなら、終わってからか、次の子チケットのフローに書く`
  );
}

/** 置き場の途中かファイルがシンボリックリンク。読まないし書かない */
export function linkedReason(rel: string): string {
  return `フローの置き場（${rel}）かその途中がシンボリックリンクなので、読まないし書かない。リンクの先は承認済みの領域の外かもしれない。リンクを外してから開き直す`;
}

/** 確かめられなかったとき。閉じる側にする */
export function lockFromFailure(error: string): FlowLock {
  return { locked: true, reason: `着手中かを確かめられないので、書かない: ${error}` };
}

/** ボードの JSON から引いた、この子のフロー */
export interface FlowTarget {
  readonly ticket: string;
  readonly title: string;
  readonly parent: string;
  readonly project: string;
  readonly flow: FlowJson;
  readonly lock: FlowLock;
}

export type FlowTargetResult = { readonly ok: true; readonly target: FlowTarget } | { readonly ok: false; readonly error: string };

/**
 * ボードの JSON から子のフローを引く。錠は `flow.locked` の写し。無い子・親・フローの欄が無いものは引けない。
 */
export function flowTargetOf(board: BoardJson, ticket: string): FlowTargetResult {
  const found = board.tickets.find((t) => t.ticket === ticket);
  if (found === undefined) {
    return { ok: false, error: `チケット ${ticket} が実行ファイルの答えに無い` };
  }
  if (found.parent === "") {
    return { ok: false, error: `${ticket} は親チケット。フローを持つのは子チケットだけ` };
  }
  if (found.flow === null) {
    return { ok: false, error: `${ticket} のフローの置き場が実行ファイルの答えに無い（実行ファイルが古い）` };
  }
  return {
    ok: true,
    target: {
      ticket,
      title: found.title,
      parent: found.parent,
      project: found.project,
      flow: found.flow,
      lock: found.flow.locked
        ? { locked: true, reason: lockedReason(ticket) }
        : found.flow.linked
          ? { locked: true, reason: linkedReason(found.flow.rel) }
          : found.flow.tree === ""
            ? lockFromFailure("フローを持つツリーが実行ファイルの答えに無い（実行ファイルが古い）")
            : OPEN_LOCK,
    },
  };
}

/** カードの「フロー」ボタンの言葉。在るか・着手中かで変わる（どちらも実行ファイルの答えの写し） */
export function flowButtonLabel(flow: FlowJson): string {
  if (flow.locked) {
    return "フロー: 閲覧（着手中）";
  }
  return flow.exists ? "フロー: 編集" : "フロー: 作成";
}

// ---- 画面に見せる形

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
  /** 頼んだ往復が起きなかった（人が確認をやめた）。画面は欄を戻す */
  | { readonly type: "cancelled" }
  /** 取り込んだ。画面は編集中のフローをこれに置き換え、未保存にする（書くのは保存を押したとき） */
  | { readonly type: "imported"; readonly doc: FlowDoc; readonly source: string }
  | { readonly type: "tour" }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側は `asFlowMessage` で形を確かめてから使う */
export type FlowMessage =
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "dirty"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  | { readonly type: "import"; readonly dirty: boolean }
  | { readonly type: "save"; readonly doc: FlowDoc }
  | { readonly type: "tourDone" };

/**
 * 画面から届いたものを確かめる。**形が崩れていたら捨てる**（保存の中身が読めないものを書かない）。
 */
export function asFlowMessage(message: unknown): FlowMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; dirty?: unknown; doc?: unknown };
  switch (m.type) {
    case "ready":
    case "openFile":
    case "tourDone":
      return { type: m.type };
    case "reload":
    case "import":
      return { type: m.type, dirty: m.dirty === true };
    case "dirty":
      return typeof m.dirty === "boolean" ? { type: "dirty", dirty: m.dirty } : undefined;
    case "save": {
      const doc = asFlowDoc(m.doc);
      return doc === undefined ? undefined : { type: "save", doc };
    }
    default:
      return undefined;
  }
}

/**
 * ボードの「フロー」ボタンの中身（`{type: "flow", ticket}`）の識別子。形が違えば undefined。
 * 識別子に使えない綴り（空・空白・パスの区切り）は受けない。開く先は拡張ホストがボードの答えから引き直す。
 */
export function flowTicketOf(message: { readonly ticket?: unknown }): string | undefined {
  const ticket = message.ticket;
  if (typeof ticket !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(ticket)) {
    return undefined;
  }
  return ticket;
}

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-flow-data";

export function embedData(data: FlowData): string {
  return embedJson(data);
}
