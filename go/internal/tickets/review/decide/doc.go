// Package decide は、レビューで残った指摘の行き先を決める。対象は `ccnavi --reviewed N --accept-unresolved` の対話・
// `--preview --json`・`--yes`・`--choose-out`・`--check`、そして `ops` の投稿の本文の作り直しである。
//
// ユーザは指摘ごとに、3 つのどれかを選ぶ。このまま（Keep）、直す（Fix）、issue に回す（Issue）。このまま残すものは
// 手元に置く。投稿する下書き（決めた内容のコメントと issue）は、state の置き場に書き、sh が投稿する。下書きのファイル名・
// 目印・`--json` の形（版 Version）・ダイジェストの形は、sh とボード（拡張の decidemodel.ts）との契約である。変えない。
// 前提の検査は gate の 1 か所にある。本体と `--check` が同じものを通る。
//
// Python からの対応: tickets/review/decide をこのパッケージにした。CHOICE_* → Choice の定数、CHOICES →
// Choices、DECIDE_VERSION → Version、Decision → Decision（unresolved・extra → Unresolved・Extra）、
// decision_digest → Digest、parse_digest → ParseDigest（答えは Shown）、changed_since_shown →
// ChangedSinceShown、decision_json → JSON、decide_gate → 私的な gate、check → Check、decide_preview → Preview、
// decide_yes → Yes、choose → Choose、reviewed → Reviewed、apply_decision → 私的な apply、plain・span・defuse・
// fence → 私的な plain・Span・私的な defuse・fence、thread_block → 私的な threadBlock、transcribe → Transcribe、issue_texts →
// IssueTexts、decide_comment_text → CommentText、post_text → PostText（記録は *oplog.Record）、resume_plan →
// ResumePlan、ISSUE_BODY_LIMIT → issueBodyLimit、COPY_HEADING → copyHeading。_thread_line は
// request.ThreadLabel を直に使い、_decision_of の軽い入れ物は Decision をそのまま組む。
package decide
