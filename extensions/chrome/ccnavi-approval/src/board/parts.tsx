/**
 * ボードの小さな部品。注意の一言・押すボタン・リンクと、描く間ずっと要る値（Markdown の描き手・書く操作の最中か）。
 *
 * 素の文字列はすべて React の子（textContent）で入れる。リンクは http(s)・mailto の表記のときだけ付ける。
 */
import { createContext, useContext, type JSX, type ReactNode } from "react";

import type { Renderer } from "../core/sanitize.js";
import { ALLOWED_URI } from "../core/sanitize.js";

/** Markdown を消毒した断片にする描き手（`createRenderer` の答え） */
export const MarkdownContext = createContext<Renderer | null>(null);

/**
 * 書く操作の最中（controller の `busy` と、書いた後の読み直しが始まるまで）。真の間は `button.action` をすべて押せなくする。
 * ボタンは `ActionButton` だけで作り、ここを読み漏らさない（二重に書かせないため）
 */
export const BusyContext = createContext(false);

export function Notice({ kind, text }: { kind: "error" | "warn"; text: string }): JSX.Element {
  return (
    <p className={`notice ${kind}`} role={kind === "error" ? "alert" : "status"}>
      {text}
    </p>
  );
}

/** 書く・読む操作のボタン（`button.action[data-action]`）。書く操作の最中は押せない */
export function ActionButton({ action, onClick, type = "button", children }: { action: string; onClick?: () => void; type?: "button" | "submit"; children: ReactNode }): JSX.Element {
  const busy = useContext(BusyContext);
  return (
    <button className="action" type={type} data-action={action} disabled={busy} onClick={onClick === undefined ? undefined : () => onClick()}>
      {children}
    </button>
  );
}

/** リンクを http(s)・mailto の表記のときだけ付ける。ほかは文字だけ */
export function Link({ href, text }: { href: string; text: string }): JSX.Element {
  if (!ALLOWED_URI.test(href.trim())) return <span>{text}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {text}
    </a>
  );
}
