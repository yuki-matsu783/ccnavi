/**
 * ボードの 1 つのリポジトリの欄（src/board/Repo.tsx。React）を jsdom の文書に描き、`section.repo` を返す。
 *
 * 描く先は `doc.body` に足した入れ物（div）で、描き終えてから返す（flushSync）。React は押したときの動きを入れ物で
 * 受けるので、返した要素は入れ物から動かさない（`doc.body.append` し直さない）。
 *
 * 同じ文書に続けて呼ぶと、前に描いた root を外し、文書の中を空にしてから描く（前の欄が文書に残らない）。
 * 返した要素は、次に呼ぶまでが有効。
 *
 * 複数のリポジトリの欄を同じ文書に並べて確かめるときは `renderBoards`（1 回の描画に全部を入れる）。
 * `renderBoard` を続けて呼ぶと前の欄は文書から外れる。
 */
import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { Renderer } from "../../src/core/sanitize.js";
import type { RepoBoard } from "../../src/core/snapshot.js";
import type { Actions, IssueList } from "../../src/board/controller.js";
import { MarkdownContext } from "../../src/board/parts.js";
import { Repo } from "../../src/board/Repo.js";

export interface Extras {
  readonly attention?: Readonly<Record<string, string>>;
  readonly issues?: IssueList;
}

/** 文書ごとに、いま描いている root */
const live = new WeakMap<Document, Root>();

export function renderBoard(doc: Document, md: Renderer, board: RepoBoard, actions?: Actions, extras: Extras = {}): HTMLElement {
  return renderBoards(doc, md, [board], actions, extras)[0];
}

/** 複数のリポジトリの欄を 1 回の描画で並べ、`section.repo` を渡した順に返す。 */
export function renderBoards(doc: Document, md: Renderer, boards: readonly RepoBoard[], actions?: Actions, extras: Extras = {}): HTMLElement[] {
  const previous = live.get(doc);
  if (previous) {
    (globalThis as { window?: unknown }).window = doc.defaultView;
    flushSync(() => previous.unmount());
  }
  doc.body.replaceChildren();
  const container = doc.createElement("div");
  doc.body.append(container);
  const root = createRoot(container);
  live.set(doc, root);
  // react-dom は描く間と描いた後の微小タスクで大域の window（いまの event）を読む。Node には無いので、この文書の window を
  // 置いたままにする。前提: 試験のファイルごとに別のプロセス（scripts/test.js が `node --test` で回す）なので、ほかのファイルへは
  // 影響しない。同じプロセスで別の文書を使う試験と混ぜるときは、呼び手が戻す。
  // （Pyodide は Node かを process で見分けるので、window があっても変わらない）
  (globalThis as { window?: unknown }).window = doc.defaultView;
  flushSync(() => root.render(createElement(MarkdownContext, { value: md }, ...boards.map((board) => createElement(Repo, { key: `${board.repo.host}/${board.repo.owner}/${board.repo.repo}`, board, actions, attention: extras.attention, issues: extras.issues })))));
  const sections = [...container.children];
  if (sections.length !== boards.length || !sections.every((section): section is HTMLElement => section instanceof doc.defaultView!.HTMLElement)) throw new Error("section.repo を描けなかった");
  return sections;
}
