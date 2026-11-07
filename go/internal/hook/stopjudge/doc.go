// Package stopjudge は Stop の AI 判定の判定役（agent 型 hook）の呼び出しを見分け、ccnavi のほかの hook から除く。
//
// 判定役は独立したコンテキストで `ccnavi stop-review material|record` を打つ。作業者の呼び出しと同じに扱うと、
// 失敗の連続・範囲・フェーズの知らせ・実行中の印・差し戻しが動いてしまう。そこで判定役の PreToolUse は
// 許す形の `stop-review` の Bash と返り値の道具だけを通してほかは止め、ほかのイベントは上の段が何もしない。
// 判定役は transcript を読むので、そこに書かれた命令でほかのコマンドを通されないよう、許す形は先頭語から完全一致に限る。
// 作業者が `stop-review` を打てば止め（dry-run では止めずに）、汚染の印（stoptaint-）を付ける。
//
// 見分けるのは payload の agent_id の接頭辞（実機の Claude Code 2.1 では `hook-agent-<uuid>`）。登録していない
// ワークスペースでは見分けない。ユーザ自身の agent 型 hook も同じ接頭辞になるので、登録したワークスペースではそれも判定役になる。
//
// Python からの対応: hook/stopjudge。is_judge → IsJudge、before → Before、記録の reason の
// "stop-review-judge" → ReasonJudge。印の名前は records/stopreview の TaintPrefix を使う。印を読む tainted は
// 呼び手が無い（stopreview が読む）ので移していない。
package stopjudge
