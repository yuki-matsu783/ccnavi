// Package fields はチケットの frontmatter の欄を、読み直さずに行単位で書き換え・読み取る。ユーザが書いたコメントや
// `|` のブロックを残し、親のブランチの diff に余計な変更を出さないため。実行後チェックが「この変更は ccnavi の
// サブコマンドが書いたぶんか」を内容で答える正規化（ScriptShape）も、同じ行単位の切り出しで持つ。
//
// Python からの対応: ticket_fields をそのまま移した。set_fields → SetFields、set_mr → SetMR（MR_* → MRResult）、
// plain_int_field → PlainIntField、mr_is_empty → MREmpty、script_fields_set → ScriptFieldsSet、
// script_shape → ScriptShape。_yaml_scalar は infra の yaml.Quoted を使う。
package fields
