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
 * - 中身を隠す書き方は通さない（段階 3 のレビューの決定 A。承認者に見えないまま承認させない）:
 *   `hidden`・`class`・`style`・`id`・`color` などの属性と、`font`・`details`・`summary` などの要素は落とす。
 *   HTML コメントは消さずに「〈HTML コメント: …〉」の文字で見せる
 */
import createDOMPurify, { type Config, type WindowLike } from "dompurify";
import { Marked } from "marked";

/** 許すリンクの綴り。DOMPurify は属性の値を DOM で読んだ後（実体参照を解いた後）に当てる */
export const ALLOWED_URI = /^(?:https?|mailto):/i;

const FORBID_TAGS = [
  "img", "picture", "source", "video", "audio", "iframe", "frame", "object", "embed", "form", "input", "button", "textarea", "select",
  "option", "style", "link", "meta", "base", "svg", "math",
  // 中身を隠す・畳む・見た目を変える要素（決定 A）
  "font", "details", "summary", "dialog", "template", "noscript", "marquee", "center", "blink",
];

const CONFIG: Config = {
  USE_PROFILES: { html: true },
  FORBID_TAGS,
  FORBID_ATTR: [
    "style", "srcset", "action", "formaction", "background", "poster", "ping",
    // 隠す・色で消す・見た目を変える属性（決定 A）。class と id は拡張の CSS に当たりうる
    "hidden", "class", "id", "name", "color", "bgcolor", "face", "size", "width", "height", "align", "dir", "open", "inert", "popover",
  ],
  ALLOWED_URI_REGEXP: ALLOWED_URI,
  ALLOW_DATA_ATTR: false,
  ALLOW_UNKNOWN_PROTOCOLS: false,
  RETURN_DOM_FRAGMENT: true,
};

function escape(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * HTML コメントを、消さずに目に見える文字にする（決定 A）。閉じていないコメントも末尾まで見せる。
 * コードの中のコメントは marked が既に実体参照にしているので当たらない。
 */
export function visibleComments(html: string): string {
  return html.replace(/<!--([\s\S]*?)(?:-->|$)/g, (_, body: string) => `<span>〈HTML コメント: ${escape(body.trim())}〉</span>`);
}

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
      const html = visibleComments(marked.parse(text, { async: false }) as string);
      return purify.sanitize(html, CONFIG) as unknown as DocumentFragment;
    },
  };
}
