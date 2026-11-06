// Package rules は、ルール（deny・ask・allow）の型、ルールファイルの読み込み、照合を持つ。判定の中心であり、
// 同じルールファイルに対して Python の実行ファイルと同じ答えを返す。式は pyre で読む。
//
// ルールは自分の文面を持つ。止めるルールは代わりの手段を、聞くルールは何を見て判断すればよいかを書く。
// glob も regex も、どの機械でも大文字小文字を区別せずに当てる。区別が要る regex は `(?-i:...)` で囲む。
// `{root}` はワークスペースルートの実パスに、`{!root}` は「ルートの外」を先読みなしに書いた式に置き換える。
// 組み立てられないルールは名指しの苦情（Problem）にして外す。1 件の不備でガード全体を止めないためである。
// ただし `{!root}` を組み立てられない deny・ask は、保護を消さないために match のすべてに当てて止める。
//
// ツールの呼び出しのうち、ルールを当てる文字列（SubjectOf）もここが持つ。判定とルールの検査が同じ表を読む。
//
// Python からの対応: rules と subjects をまとめた。load → Load（読めなければ error）、parse → Parse、
// parse(…, builtin=True) → Builtin（組み込みは Spec で書く）、compile_condition → CompileCondition、
// Rule.matches・hits・body_hits・spoken_message・key・written_body → Matches・Hits・BodyHits・SpokenMessage・
// Key・WrittenBody、record_name → RecordName、tool_matches → ToolMatches、fill_text・fill_root → FillText・
// FillRoot、root_glob・root_pattern・real_root・not_root_pattern → RootGlob・RootPattern・RealRoot・NotRootPattern、
// has_relative_scripts → HasRelativeScripts、readable_every → ReadableEvery、SECTIONS → Sections、
// DENY・ASK・ALLOW → Deny・Ask・Allow（Decision）、SEVERITY_* → Error・Warn・Info（Severity）、
// message_id・message_values・message_variants → Rule.Text（TextRef）、subjects.subject_of → SubjectOf、
// SUBJECT_FIELDS → SubjectFields。
package rules
