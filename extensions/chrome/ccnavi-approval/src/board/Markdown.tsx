/**
 * Markdown の本文（`div.markdown`）。marked と DOMPurify（`src/core/sanitize.ts`）で消毒した DocumentFragment を、
 * ref で要素に差し込む。HTML の文字列には戻さない（`dangerouslySetInnerHTML` は使わない。sanitize.ts の決まり）。
 * 中身は React の子にしないので、React は描き直しで中を触らない。本文か描き手が変わったときだけ差し替える。
 */
import { useCallback, useContext, type JSX } from "react";

import { MarkdownContext } from "./parts.js";

export function Markdown({ text }: { text: string }): JSX.Element {
  const md = useContext(MarkdownContext);
  const fill = useCallback(
    (node: HTMLDivElement | null) => {
      if (node === null) return;
      if (md === null) throw new Error("Markdown の描画機能（MarkdownContext）が見つかりません");
      node.replaceChildren(md.markdown(text));
    },
    [md, text],
  );
  return <div className="markdown" ref={fill} />;
}
