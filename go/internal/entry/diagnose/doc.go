// Package diagnose は、判定を実行せずに試す診断を置く。ユーザがターミナルから打つ `--test`・`--test-expectations
// `、いま有効な宣言を文字で見せる `--explain`、VS Code のボードが読む `--explain --json` を扱う。
//
// 判定を作り直さない。試験は payload を組み、judge.Before をそのまま呼んで、返った応答と記録を見せる。
// 別の経路で判定すると、試験で通ったものが実運用で失敗することがある。モードは常に enable にし、
// 記録の置き場（state）は外す。外さないと、試しで「1 度だけ渡す文」を消費してしまう。
// ボードも判定と同じ関数（approval・phase・ruleload）で組み、答えを 2 か所で出さない。
//
// 入口（cli）が呼ぶのは Test・TestExpectations・Explain・ExplainJSON の 4 つ。どれも終了コードと、
// 想定外の失敗の error を返す。suggest は TryOne と ExpectationPlaceholder を読む。
package diagnose
