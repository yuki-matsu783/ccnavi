// Package risk は、実績で測るリスク（risks.yml の配点）を読み、子を閉じるときに差分を数えて点を付ける。
//
// 数えるのは宣言（チケットの範囲や required: false）ではなく、子の仕事の始まりから先頭までの差分である。点が
// HIGH 以上（Escalates）なら、定義が review: false でも子が required: false でも、そのフェーズは人間のレビューが要る
// 扱いになる。測れなかった項目（スクリプトの失敗・読めない出力）は重いほうとして扱う。0 と数えると、スクリプトが
// 動かなくなっただけで点が消えてしまうからである。配点は、共通レイヤーと親の project: が指すレイヤーの和である。
// ファイルが無ければ組み込みの配点を使う。共通レイヤーが壊れていれば、組み込みに戻る。
//
// Python からの対応: tickets/config/risk。LEVEL_* → Low・Medium・High・Critical、ESCALATE_FROM → Escalates、
// KIND_* → Kind*、SCRIPT_HOMES → ScriptHomes、BUILTIN → Builtin、Factor.value → Limit（数える項目）と Text
// （glob・script・judge）、max の None → hasMax、Definition.level_of・factor・judges → levelOf・Factor・Judges、
// effective_levels → EffectiveLevels（Thresholds）、load・load_layer・parse・merge・script_problems・
// layer_definition・load_definition → Load・LoadLayer・Parse・Merge・ScriptProblems・LayerDefinition・
// LoadDefinition、definition_path → layer.File、load と mark_layer(common) の組 → CommonDefinition、
// measure・evaluate・judge_prompt → Measure・Evaluate・JudgePrompt（judgements は記録の JSON のまま）、
// Score.Lines・as_dict → Score.Lines・Object、Diff の lines・files・deleted_files・summary → lines・files・
// deletedFiles・Summary。
package risk
