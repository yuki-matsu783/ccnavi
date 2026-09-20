/**
 * フェーズ管理画面が拡張ホストへ返す操作。種類の意味は判定しない（ADR-0035）。
 * 送れるのは契約（`core/phases-view.ts` の `PhasesMessage`）に書いてあるものだけ。
 */
import { poster } from "../vscode.js";
import type { PhasesMessage } from "../../core/phases-view.js";

export const post = poster<PhasesMessage>();
