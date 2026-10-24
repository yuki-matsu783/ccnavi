/**
 * 案内の手順（`*.yml` の `image:`）が引く画像の表。鍵は `images/<名前>.png` の名前。
 * 画像は data: の URL としてバンドルに入る（Webview はファイルを読まない）。足したら、ここに 1 行足す（テストが突き合わせる）。
 */
import claudeCodePrompt from "./images/claude-code-prompt.png";
import gitlabIssueCreated from "./images/gitlab-issue-created.png";
import gitlabIssuesList from "./images/gitlab-issues-list.png";
import gitlabIssuesMenu from "./images/gitlab-issues-menu.png";
import gitlabLineComment from "./images/gitlab-line-comment.png";
import gitlabMrApproval from "./images/gitlab-mr-approval.png";
import gitlabMarkReady from "./images/gitlab-mark-ready.png";
import gitlabMerged from "./images/gitlab-merged.png";
import gitlabMrMerge from "./images/gitlab-mr-merge.png";
import gitlabNewIssue from "./images/gitlab-new-issue.png";
import gitlabNewMr from "./images/gitlab-new-mr.png";
import gitlabPendingReview from "./images/gitlab-pending-review.png";
import gitlabThreadsResolved from "./images/gitlab-threads-resolved.png";
import gitlabStartThread from "./images/gitlab-start-thread.png";

export const TOUR_IMAGES: Readonly<Record<string, string>> = {
  "claude-code-prompt": claudeCodePrompt,
  "gitlab-issue-created": gitlabIssueCreated,
  "gitlab-issues-list": gitlabIssuesList,
  "gitlab-issues-menu": gitlabIssuesMenu,
  "gitlab-merged": gitlabMerged,
  "gitlab-mr-approval": gitlabMrApproval,
  "gitlab-mark-ready": gitlabMarkReady,
  "gitlab-mr-merge": gitlabMrMerge,
  "gitlab-new-issue": gitlabNewIssue,
  "gitlab-new-mr": gitlabNewMr,
  "gitlab-line-comment": gitlabLineComment,
  "gitlab-pending-review": gitlabPendingReview,
  "gitlab-threads-resolved": gitlabThreadsResolved,
  "gitlab-start-thread": gitlabStartThread,
};
