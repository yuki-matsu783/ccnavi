/**
 * プロジェクト管理画面が拡張ホストへ返す操作。clone も書き込みも画面はしない（ADR-0035）。
 * 送れるのは契約（`core/projects-view.ts` の `ProjectsMessage`）に書いてあるものだけ。
 */
import { poster } from "../vscode.js";
import type { ProjectsMessage } from "../../core/projects-view.js";

export const post = poster<ProjectsMessage>();
