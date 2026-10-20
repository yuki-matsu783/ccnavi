/**
 * ボード画面の案内の見本（`webview/tours/board.yml` の `sample:`）を、画面が描く形にする。
 *
 * ボードは実行ファイルの出力（`ccnavi --explain --json`）と同じ形で見本に書き、実際の読み取り（`parseBoardJson`）と
 * 組み立て（`buildBoard`）を通す。カードの形をここで組むと、カードの欄が増えたときに見本だけが古くなる。
 * 承認の一覧と残った指摘も、実行ファイルの JSON と同じ形で書き、`approvemodel.ts` / `decidemodel.ts` の読み取りを通す。
 * いずれも案内の間に描くだけで、拡張ホストへは何も送らない。
 *
 * `root` と `generatedAt` は、いまのボードのものをそのまま使う（脚注が見本のために変わらないように）。
 */
import { parseApprovePreview } from "./approvemodel.js";
import { buildBoard, COLUMNS, type Board } from "./board.js";
import type { Moved } from "./board-moved.js";
import type { ApprovalOverlay } from "./board-view.js";
import { DECIDE_VERSION, parseDecidePreview } from "./decidemodel.js";
import { BOARD_VERSION, parseBoardJson, type ProposalState } from "./model.js";

/** 手順の `scene.overlay` が指せるオーバーレイ */
export const OVERLAY_NAMES = ["approval", "done", "decide", "reviewed"] as const;
export type OverlayName = (typeof OVERLAY_NAMES)[number];

export interface BoardSample {
  readonly board: Board;
  /** 前の更新から列が変わったカード（動いた表示） */
  readonly moved: readonly Moved[];
  /** `scene.overlay` の名前で引く承認のオーバーレイ */
  readonly overlays: Readonly<Record<OverlayName, ApprovalOverlay>>;
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Record<string, unknown>;
}

function str(value: unknown, at: string): string {
  if (typeof value !== "string") {
    throw new Error(`${at} が文字列ではない`);
  }
  return value;
}

function column(value: unknown, at: string): ProposalState {
  const state = COLUMNS.find((c) => c.state === value)?.state;
  if (state === undefined) {
    throw new Error(`${at} は列の名前（${COLUMNS.map((c) => c.state).join(" / ")}）にする`);
  }
  return state;
}

/** 実行ファイルの JSON の読み取りは版を見るので、見本にも版を付けて通す */
function viaParser<T>(parse: (text: string) => { readonly ok: true; readonly value: T } | { readonly ok: false; readonly error: string }, raw: unknown, at: string, version = 1): T {
  const parsed = parse(JSON.stringify({ version, ...object(raw, at) }));
  if (!parsed.ok) {
    throw new Error(`${at}: ${parsed.error}`);
  }
  return parsed.value;
}

export function parseBoardSample(raw: unknown, root: string, generatedAt: string): BoardSample {
  const sample = object(raw, "sample");
  const json = parseBoardJson(JSON.stringify({ version: BOARD_VERSION, root, generated_at: generatedAt, ...object(sample.board, "sample.board") }));
  if (!json.ok) {
    throw new Error(`sample.board: ${json.error}`);
  }
  const moved: Moved[] = (Array.isArray(sample.moved) ? sample.moved : []).map((item, i) => {
    const at = `sample.moved[${i}]`;
    const m = object(item, at);
    return { id: str(m.id, `${at}.id`), ...(m.from === undefined ? {} : { from: column(m.from, `${at}.from`) }), to: column(m.to, `${at}.to`) };
  });
  const overlays = object(sample.overlays, "sample.overlays");
  const approval = object(overlays.approval, "sample.overlays.approval");
  const done = object(overlays.done, "sample.overlays.done");
  const decide = object(overlays.decide, "sample.overlays.decide");
  const reviewed = object(overlays.reviewed, "sample.overlays.reviewed");
  const { tree, ...decidePreview } = decide;
  return {
    board: buildBoard(json.board),
    moved,
    overlays: {
      approval: { kind: "preview", preview: viaParser(parseApprovePreview, approval, "sample.overlays.approval") },
      done: { kind: "done", count: typeof done.count === "number" ? done.count : 0, prompt: str(done.prompt, "sample.overlays.done.prompt"), carried: done.carried === true },
      decide: { kind: "decidePreview", preview: viaParser(parseDecidePreview, decidePreview, "sample.overlays.decide", DECIDE_VERSION), tree: str(tree, "sample.overlays.decide.tree") },
      reviewed: {
        kind: "prompt",
        title: str(reviewed.title, "sample.overlays.reviewed.title"),
        note: str(reviewed.note, "sample.overlays.reviewed.note"),
        prompt: str(reviewed.prompt, "sample.overlays.reviewed.prompt"),
        what: str(reviewed.what, "sample.overlays.reviewed.what"),
      },
    },
  };
}
