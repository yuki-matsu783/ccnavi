/**
 * 文の中のバッククォートで囲んだ部分を等幅（`<code>`）にする。lint など実行ファイル由来の文は
 * バッククォートつきで届くので、画面側で直す。文字は React が escape するので HTML としては解釈しない。
 */
import type { JSX } from "react";

export function Code({ text }: { readonly text: string }): JSX.Element {
  const parts = text.split(/`([^`\n]+)`/);
  return (
    <>
      {parts.map((part, index) => (index % 2 === 1 ? <code key={index}>{part}</code> : part))}
    </>
  );
}
