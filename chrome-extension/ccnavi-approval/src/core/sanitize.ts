/**
 * Markdown の描画（ADR-0093 の 5.5 の 2）。
 *
 * チケットの本文はエージェントや他の人が書ける、攻撃者が制御しうる入力として扱う。
 * Markdown を HTML に変えた後に DOMPurify で消毒し、文字列ではなく DOM の断片で返す
 * （`innerHTML` に文字列を戻さない）。
 *
 * - リンクは `http:`・`https:`・`mailto:` だけ。ほかの綴り（`javascript:`・`data:`・相対）は href を外す
 * - 画像・SVG・MathML・フォーム・style は出さない。画像は外への通信（読んだことの漏れ）になるため
 * - リンクは新しいタブで開き、`noopener noreferrer` を付ける
 */
import createDOMPurify, { type Config, type WindowLike } from "dompurify";
import { Marked } from "marked";

/** 許すリンクの綴り。DOMPurify は属性の値を DOM で読んだ後（実体参照を解いた後）に当てる */
export const ALLOWED_URI = /^(?:https?|mailto):/i;

const FORBID_TAGS = ["img", "picture", "source", "video", "audio", "iframe", "frame", "object", "embed", "form", "input", "button", "textarea", "select", "option", "style", "link", "meta", "base", "svg", "math"];

const CONFIG: Config = {
  USE_PROFILES: { html: true },
  FORBID_TAGS,
  FORBID_ATTR: ["style", "srcset", "action", "formaction", "background", "poster", "ping"],
  ALLOWED_URI_REGEXP: ALLOWED_URI,
  ALLOW_DATA_ATTR: false,
  ALLOW_UNKNOWN_PROTOCOLS: false,
  RETURN_DOM_FRAGMENT: true,
};

export interface Renderer {
  /** Markdown を消毒した DOM の断片にする */
  markdown(text: string): DocumentFragment;
}

export function createRenderer(win: WindowLike): Renderer {
  const purify = createDOMPurify(win);
  purify.addHook("afterSanitizeAttributes", (node) => {
    if (node.nodeName === "A") {
      const href = node.getAttribute("href");
      if (href === null || !ALLOWED_URI.test(href.trim())) {
        node.removeAttribute("href");
      } else {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    }
  });
  const marked = new Marked({ gfm: true, breaks: false, async: false });
  return {
    markdown(text: string): DocumentFragment {
      const html = marked.parse(text, { async: false }) as string;
      return purify.sanitize(html, CONFIG) as unknown as DocumentFragment;
    },
  };
}
