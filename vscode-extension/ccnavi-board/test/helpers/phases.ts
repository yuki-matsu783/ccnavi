/**
 * フェーズ管理画面（React）を happy-dom で動かす。`test/helpers/risk.ts` と同じ役割。
 *
 * 画面は束ねた 1 本（`out/webview/phases.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * ここでも同じ 1 本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * この入口の綴り（`test/helpers/<画面の名前>.ts`）が約束で、`scripts/test-groups.js` はそれを辿る。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import { readPhases } from "../../src/core/phases-doc.js";
import { renderPhasesPage, type RenderOptions } from "../../src/core/phases-render.js";
import type { PhasesData, PhasesPage } from "../../src/core/phases-view.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage, type LoadOptions } from "./dom.js";
import { loadPageJsdom, type JsdomPage } from "./jsdom.js";

export const NONCE = "TEST-NONCE-123";

/**
 * 見本の種類（5 種類。README「フェーズの種類と計画」の例）。画面のテストの多くがこれを開く。
 * 拡張がファイルを作るときの雛形だったが、共通層に雛形を置くと層の同じ id と中身が食い違うので、
 * 画面から作る道ごと無くし、テストの見本としてだけ残す。
 *
 * **待ち方は dag で、流れを `after` で書く**（調査 → 設計と受入テスト作成 → 実装とテスト）。
 * feedback の種類は `after` を持てない（`phasetypes.py`）ので、レビュー後の対応として別に置く。
 */
export const SAMPLE_PHASES_TEXT = `# フェーズの種類（設計 9.7）。人が持つ設定で、エージェントは書き換えない。
#
# 親チケットの \`plan:\` に、ここで定義した種類の名前を順に並べる。それが全体計画で、
# \`ccnavi --approve\` が通ることが合意になる。レビューを受けたあとは \`feedback:\` に
# \`kind: feedback\` の種類を並べて改版を出す（対応が無くても \`[]\` で出す）。
#
# \`id\`（キー）と \`title\` はどちらも一意。重なれば --lint が error で止める。
# このファイルが無ければ、フェーズは番号だけの挙動に戻る。
#
# \`order: dag\` なので、各項は \`after\` に挙げた種類（の祖先）だけを待ち、辺で繋がっていない
# 種類は並行して進む。辺の書き漏れは並行として通るので、画面の図で確かめる。
#
# 下は雛形。scope の綴りはこのプロジェクトの置き場に合わせて直す。
version: 1
order: dag

phases:
  research:
    kind: work
    title: 調査
    review: none
    scope: ["wip/research/*"]
    deliverables: ["wip/research/summary.md"]
    when: 既存の振る舞いや依存が分からないとき。分かっているなら飛ばす

  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*", "docs/*"]
    deliverables: ["wip/design/*.md"]
    after: [research]
    when: 触る場所が 3 か所を超えるか、外から見える振る舞いが変わるとき

  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]
    after: [design]
    when: 振る舞いが変わるとき。設計のあと、実装より先に書く

  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope: ["src/*", "tests/*"]
    requires: [acceptance]
    after: [acceptance]

  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr
    scope: inherit
`;

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function phasesHtml(data: PhasesData, options: Partial<RenderOptions> = {}): string {
  return renderPhasesPage(data, { nonce: NONCE, script: screenScript("phases"), style: screenStyle("phases"), ...options });
}

/** 見本の中身（`SAMPLE_PHASES_TEXT` の 5 種類）。差し替えたいところだけ渡す */
export function page(overrides: Partial<PhasesPage> = {}): PhasesPage {
  return {
    root: "/ws",
    phasesPath: ".ccnavi/config/phases.yml",
    exists: true,
    model: readPhases(SAMPLE_PHASES_TEXT).model,
    lock: { locked: false, reason: "", doing: [] },
    ...overrides,
  };
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: PhasesData, initialState?: unknown, options: LoadOptions = {}): Promise<DomPage> {
  const dom = await loadPage(phasesHtml(data), initialState, options);
  await dom.settle();
  return dom;
}

/** 見本の種類を開く。差し替えたいところだけ渡す */
export async function openPhases(overrides: Partial<PhasesPage> = {}, initialState?: unknown): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) }, initialState);
}

/**
 * 図を出した状態で開く。**大きさを測れるようにして読ませる**（`measure`）。
 * これをしないと React Flow は点を隠したまま線を 1 本も描かず、テストは空の絵で通る。
 */
export async function openGraph(overrides: Partial<PhasesPage> = {}, initialState: unknown = {}): Promise<DomPage> {
  const dom = await openPage({ kind: "page", page: page(overrides) }, { ...(initialState as object), view: "graph" }, { measure: true });
  await dom.settle();
  return dom;
}

/** 種類 1 行の中の要素。`li.phase[data-key=…]` の下だけを見る */
export function rowSelector(key: string): string {
  return `.phase[data-key="${key}"]`;
}

/**
 * 図を出した状態で、**jsdom で**開く。ドラッグだけがここを通る
 * （happy-dom では d3-drag の待ちが終わらず固まる。`test/helpers/jsdom.ts` の頭）。
 */
export async function openGraphJsdom(overrides: Partial<PhasesPage> = {}, initialState: unknown = {}): Promise<JsdomPage> {
  return loadPageJsdom(phasesHtml({ kind: "page", page: page(overrides) }), { ...(initialState as object), view: "graph" });
}
