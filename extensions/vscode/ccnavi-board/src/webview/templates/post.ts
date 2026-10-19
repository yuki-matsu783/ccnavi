/**
 * テンプレート管理画面が拡張ホストへ返す操作。送るのは契約（`core/templates-view.ts` の `TemplatesMessage`）に
 * 書いてあるものだけ。
 */
import { poster } from "../vscode.js";
import type { TemplatesMessage } from "../../core/templates-view.js";

export const post = poster<TemplatesMessage>();
