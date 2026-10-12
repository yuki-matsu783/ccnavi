/**
 * チケットの識別子の形（Python の `ticket_ids._ID` と同じ。先頭は ASCII の英数字、続きは英数字・`.`・`_`・`-` と
 * ひらがな・カタカナ・長音記号・CJK 統合漢字・々）。画面から届いた識別子を受けるかの見分けに使う
 * （フロー編集・計画編集）。識別子に使えない表記（空・空白・パスの区切り）は受けない。
 *
 * VS Code の API も DOM も node も読まない（画面のバンドルに入っても軽い）。
 */
const TICKET_ID = /^[A-Za-z0-9][A-Za-z0-9._\-\u3005\u3041-\u3096\u30a1-\u30fa\u30fc\u4e00-\u9fff]*$/u;

export function isTicketId(value: unknown): value is string {
  return typeof value === "string" && TICKET_ID.test(value);
}
