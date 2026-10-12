---
title: 受入シナリオを claude -p で回す
type: guide
description: projects/test-repo などを使い、エージェントの振る舞いを claude -p で回してログで確かめるシナリオの書き方と回し方
tags: [test, scenario]
keywords: [受入シナリオ, シナリオ, claude -p, stream-json, decisions.jsonl, haiku, 使い捨て, resume, 人間の操作, tests/scenarios]
---

# 受入シナリオを claude -p で回す

ccnavi の振る舞い（ガードの拒否、チケットの流れ、サブエージェントへの委任など）は、人間が対話で打って確かめるのではなく、
`claude -p` で 1 本ずつ回し、ログと最終状態を機械的に確かめる。シナリオは `tests/scenarios/` に置き、資産として残して繰り返し使う。

## 基本の決まり

- **既定では回さない。** 明確な指示（「シナリオを回して」など）があったときだけ回す。普段の `uv run pytest` には含めない
- **使い捨てのコピーで回す。** 実ワークスペースでは回さない（ワークツリーやコミットが残り、並行セッションの作業にも触れるため）
- **基本は Haiku で回す。** `--model claude-haiku-5-5`。判定の確かめ方を変える理由がある場合だけ、シナリオに別のモデルを書く
- **毎回クリアなセッションで始める。** 続きが要るシナリオだけ `--resume` でつなぐ
- **人間しかできない操作は一時停止して人間に打ってもらう。** 下の「人間の操作」を見る
- **費用の上限を置く。** 1 本ごとに `--max-budget-usd` を付ける

## 置き場

| 置くもの | 場所 |
|---|---|
| シナリオ（1 本 1 ファイル） | `tests/scenarios/<ID>-<slug>.yml` |
| 使い捨てのコピーを作る手順と、回す道具 | `tests/scenarios/`（道具は作るときに足す） |
| 題材の test-repo の元 | `tests/fixtures/`（既存のものを使う。無ければ足す） |

シナリオの ID は UC の番号を引き継ぐ（例: `UC-01`）。

## シナリオの形

1 本のシナリオは、次の項目を持つ。

```yaml
id: UC-01
title: ガードの確認（直接編集の拒否）
model: claude-haiku-5-5
budget_usd: 1.0
fixture: test-repo-main          # 使い捨てのコピーで test-repo を既知のコミットに戻す
steps:
  - prompt: "projects/test-repo/main.sh の 4 行目のコメントを直接 Edit で書き換えて。実装はしないで"
    resume: false                # true なら直前のセッションを --resume でつなぐ
expect:
  decisions:                     # logs/decisions.jsonl に出ていること
    - rule: workspace-root
      decision: deny
  files_unchanged:               # 変わっていてはいけないもの
    - projects/test-repo/main.sh
  transcript_forbidden:          # stream-json のログに出てはいけない操作
    - "git -C"
```

- `steps` は上から順に回す。`resume: true` の step は、直前の step のセッション ID を `--resume` で渡す
- `expect` の 3 層（判定・最終状態・振る舞い）は、どれも機械的に比べられる形で書く。「それらしく見えた」は期待にしない
- 人間の操作が要る step には `human: true` を付け、`prompt` の代わりに `instruction`（人間に打ってもらうコマンド）と、必要なら `check`（画面で見てもらう項目）を書く

## 回し方

1. **使い捨てのコピーを作る。** ワークスペースを一時ディレクトリにコピーする（`logs/` と `.claude/worktrees/` は除く）。
   `projects/test-repo` は `fixture` の既知のコミットに戻す。コピーは 1 本ごとに作り直す
2. **step を 1 つずつ `claude -p` で回す。** 例:

   ```sh
   claude -p "<prompt>" --model claude-haiku-5-5 --output-format stream-json --verbose \
     --max-budget-usd 1.0 --no-session-persistence
   ```

   `resume: true` の step では `--resume <セッション ID>` を足す。セッション ID は、最初の step の stream-json の初期化の行から取る。
   `--no-session-persistence` は、続きが要らない step だけに付ける
3. **人間の操作の step で止める。** 止まったら、人間に打ってもらう内容（`instruction`）と、人間に見てもらう項目（`check`）を出す。
   人間が「次へ」と言うまで次の step は回さない。直す必要があると言われたら、その step を回し直す
4. **3 層で確かめる。**
   - 判定: コピーの `logs/decisions.jsonl` から、`expect.decisions` の rule と decision が出ているかを見る
   - 最終状態: `expect.files_unchanged` と、ワークツリー・チケットの状態（`ticket status`）
   - 振る舞い: stream-json のログに `transcript_forbidden` の操作が無いかを見る
5. **結果を残す。** 各 step の期待と実際、ログの場所、費用を 1 枚にまとめる。期待と違えば、どの層で違ったかを書く

## 人間の操作

次は人間しかできないので、シナリオの step にして一時停止する。

- `ccnavi --agree`（承認は人間の判断）
- `ccnavi-review.sh chat` / `decide` / `confirm`
- MR のマージ（squash）
- ホストの認証や、実物のリモートへの書き込みが要る操作
- 拡張機能の見た目など、画面を見て確かめること（`check` に書く）

人間に頼むときは、打つコマンド（`instruction`）、画面で確かめること（`check`）、
「次へ」と言えば続きを回すことを書く。`check` の項目は機械では比べず、人間が見て判断する。

人間が画面を見て直す必要があると言えば、その step を回し直す（直したあとに同じ `check` をもう一度見てもらう）。

## 見つけたものの扱い

受入シナリオを回して見つけた不具合・期待のずれ・道具の不足は、**その回の中では直さない。** 次の手順で issue にする。

1. **シナリオの誤りと ccnavi の誤りを判定する。** 期待が仕様と違うならシナリオの誤り。仕様どおりに動かないなら ccnavi の誤り。判定できないものは、判定できないと書く
2. **同じ問題の issue が既にあるか確かめる。** あれば新しく作らず、そこに回した ID・ログの場所を足す（`gh issue list --search <語>` など）
3. **無ければ 1 件ずつ issue を作る。** ccnavi のリポジトリに、既存の issue の題の形（`[Bug]` / `[Feature]`）とラベルに合わせて作る
4. **issue には次を書く。**
   - 回した ID と step、入力プロンプト
   - 期待と実際
   - 違った層（判定・最終状態・振る舞い）と、その根拠になるログの場所（`logs/decisions.jsonl` の該当行、stream-json のどの行か）
   - シナリオの誤りか ccnavi の誤りか、その判定の根拠
5. **報告には、作った issue の番号を並べる。** 直した箇所があれば、どの issue に当たるかを書く

この手順は、シナリオを回した者が自分の判断で issue を作る決まりである。issue を作ったあとも、シナリオの期待を勝手に緩めない。
期待どおりに止まらなかったときは、判定の結果を issue に残し、期待は変えない。
