/**
 * 画面のキー操作の共通部品。フロー編集画面・テンプレートの編集欄・フェーズ管理画面が、元に戻す・やり直すを同じキーで受けるために使う。
 */

/** 文字を打たない入力欄の種類。ここにフォーカスがあっても、Ctrl+Zなどは画面が受ける（欄に取り消す文字が無い） */
const NO_TEXT_INPUTS = new Set(["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"]);

/**
 * キーを押した先が文字を打つ欄の中か（欄の中のCtrl+ZやCtrl+Cは欄の文字の取り消し・コピーに任せる）。
 * 選択欄（select）とチェックボックスなど、文字を打たない欄は含めない
 */
export function inField(target: EventTarget | null): boolean {
  const element = target as { tagName?: unknown; type?: unknown; isContentEditable?: unknown } | null;
  if (element === null || typeof element.tagName !== "string") {
    return false;
  }
  const tag = element.tagName.toLowerCase();
  if (tag === "input") {
    return !NO_TEXT_INPUTS.has(String(element.type ?? "text").toLowerCase());
  }
  return tag === "textarea" || element.isContentEditable === true;
}

/** 元に戻す（Ctrl+Z）・やり直す（Ctrl+Shift+Z・Ctrl+Y）のキーか。macOSはCtrlの代わりにCmd。欄の中とAlt付きは受けない */
export function historyKey(event: KeyboardEvent): "undo" | "redo" | undefined {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || inField(event.target)) {
    return undefined;
  }
  const key = event.key.toLowerCase();
  if (key === "z") {
    return event.shiftKey ? "redo" : "undo";
  }
  return key === "y" ? "redo" : undefined;
}
