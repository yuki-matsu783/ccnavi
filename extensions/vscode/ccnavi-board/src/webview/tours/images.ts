/**
 * 案内の手順（`*.yml` の `image:`）が引く画像の表。鍵は `images/<名前>.png` の名前。
 * 画像は data: の URL としてバンドルに入る（Webview はファイルを読まない）。足したら、ここに 1 行足す（テストが突き合わせる）。
 */
import claudeCodePrompt from "./images/claude-code-prompt.png";
import gitlabLineComment from "./images/gitlab-line-comment.png";
import gitlabStartThread from "./images/gitlab-start-thread.png";

export const TOUR_IMAGES: Readonly<Record<string, string>> = {
  "claude-code-prompt": claudeCodePrompt,
  "gitlab-line-comment": gitlabLineComment,
  "gitlab-start-thread": gitlabStartThread,
};
