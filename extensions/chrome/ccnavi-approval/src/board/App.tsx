/**
 * ボードの本体。controller の状態を `useSyncExternalStore` で読み、リポジトリの欄（`Repo`）を並べる。
 *
 * 描く先は board.html の `main#boards`。ページの決まった枠（`#status`・`#banner`・`#result`・`body[data-state]`）は
 * board.html に置いたまま（見出しの `#refresh`・`#options` も）で、`#banner` の中身は portal で、ほかは描いた直後に
 * 文字と属性だけを合わせる（実機の試験と見た目が頼る ID・属性を変えないため）。
 *
 * 書く操作の最中（controller の `busy` と、書いた後の読み直しが始まるまで）は、`BusyContext` で `button.action` を
 * すべて押せなくする。見出しの 2 つのボタンは `action` を持たないので巻き込まない。
 * 欄は 1 つずつ `RepoBoundary` で囲み、描く途中で投げた欄だけをエラー表示にする（ほかの欄は残す）。
 */
import { useLayoutEffect, useSyncExternalStore, type JSX } from "react";
import { createPortal } from "react-dom";

import type { Renderer } from "../core/sanitize.js";
import type { BoardController } from "./controller.js";
import { BusyContext, MarkdownContext } from "./parts.js";
import { Repo } from "./Repo.js";
import { RepoBoundary } from "./RepoBoundary.js";

/** board.html に置いてある枠 */
export interface Frame {
  readonly body: HTMLElement;
  readonly status: HTMLElement;
  readonly banner: HTMLElement;
  readonly result: HTMLElement;
}

export interface AppProps {
  readonly controller: Pick<BoardController, "snapshot" | "subscribe" | "actions">;
  readonly md: Renderer;
  readonly frame: Frame;
}

function setText(node: HTMLElement, text: string): void {
  if (node.textContent !== text) node.textContent = text;
}

export function App({ controller, md, frame }: AppProps): JSX.Element {
  const s = useSyncExternalStore(controller.subscribe, controller.snapshot);
  const busy = s.busy || s.state === "writing";

  useLayoutEffect(() => {
    setText(frame.status, s.status.text);
    if (s.status.bootMs !== undefined) frame.status.dataset.bootMs = String(s.status.bootMs);
    if (s.status.importMs !== undefined) frame.status.dataset.importMs = String(s.status.importMs);
    if (s.status.violations !== undefined) frame.status.dataset.violations = String(s.status.violations);
  }, [frame, s.status]);

  useLayoutEffect(() => {
    const result = frame.result;
    if (s.result.kind === undefined) delete result.dataset.kind;
    else result.dataset.kind = s.result.kind;
    if (result.className !== s.result.className) result.className = s.result.className;
    setText(result, s.result.text);
  }, [frame, s.result]);

  useLayoutEffect(() => {
    if (frame.body.dataset.state !== s.state) frame.body.dataset.state = s.state;
  }, [frame, s.state]);

  return (
    <MarkdownContext value={md}>
      <BusyContext value={busy}>
        {[...s.sections].map(([key, section]) => (
          <RepoBoundary key={key} name={`${section.board.repo.owner}/${section.board.repo.repo}`} repoKey={key} section={section}>
            <Repo board={section.board} actions={controller.actions} attention={section.attention} issues={section.issues} decide={s.decide} />
          </RepoBoundary>
        ))}
      </BusyContext>
      {createPortal(
        s.banner.map((line, i) => (
          <p key={i} className={line.className} data-level={line.level}>
            {line.text}
          </p>
        )),
        frame.banner,
      )}
    </MarkdownContext>
  );
}
