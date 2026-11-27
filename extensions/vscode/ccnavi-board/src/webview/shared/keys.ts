/**
 * 画面のキー操作の共通部品。フロー編集画面とテンプレートの編集欄が、元に戻す・やり直すを同じキーで受けるために使う。
 */

/** キーを押した先が欄の中か（欄の中のCtrl+ZやCtrl+Cは欄に任せる） */
export function inField(target: EventTarget | null): boolean {
  const element = target as { tagName?: unknown; isContentEditable?: unknown } | null;
  if (element === null || typeof element.tagName !== "string") {
    return false;
  }
  const tag = element.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || element.isContentEditable === true;
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
