// Package diagnose は、判定を実行せずに試す診断を持つ。ユーザがターミナルから打つ `--test`・`--test-expectations
// `、いま有効な宣言を文字で見せる `--explain`、VS Code のボードが読む `--explain --json` が対象である。
//
// 判定を作り直さない。試験は payload を組み、judge.Before をそのまま呼んで、返った応答と記録を見せる。
// 別の経路で判定すると、試験で通ったものが実運用で失敗するためである。モードは常に enable にし、
// 記録の置き場（state）は外す。試しで「1 度だけ渡す文」を消費しないためである。
// ボードも判定と同じ関数（approval・phase・ruleload）で組み、答えを 2 か所で出さない。
//
// 入口（cli）が呼ぶのは Test・TestExpectations・Explain・ExplainJSON の 4 つである。どれも終了コードと、
// 想定外の失敗（Python なら例外が抜けたもの）を error で返す。suggest は TryOne と ExpectationPlaceholder を読む。
//
// Python からの対応は次のとおり。
//   - entry/diagnose・diagnose_try・diagnose_explain・diagnose_board・diagnose_shared をまとめた。
//   - test と test_json → Test（as_json）、test_expectations → TestExpectations、
//     explain・explain_json → Explain・ExplainJSON、try_one → TryOne（答えは Result）、Call → Call、
//     EXPECTATION_PLACEHOLDER → ExpectationPlaceholder。
//   - board・hook_settings・load_expectations・run_expectations・KNOWN_TOOLS・TEST_VERSION・BOARD_VERSION と
//     diagnose_shared の関数は、上の段が使わないので私的にした（_mark_attention → markAttention）。
package diagnose
