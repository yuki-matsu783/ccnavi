---
type: design
title: claude -p の代役（台本で動くエージェント）の設計案
description: 受入シナリオの claude -p を、台本どおりにツールを呼ぶ代役に差し替え、hook の登録・何手も続く流れ・実行器の通しを費用なしで毎回同じ結果で確かめる。代役の責務、台本の形、hook の呼び方と応答の解釈、出力、本物との契約、確かめられないこと、未決の点
tags: [testing, scenario]
keywords: [代役, fake_claude, 台本, script, claude -p, stream-json, hook, settings.json, PreToolUse, Stop, SessionStart, 契約テスト, 受入シナリオ, agent]
---

# claude -p の代役（台本で動くエージェント）の設計案

作業中の下書き。合意できたら、実装と一緒に `.agents/docs/acceptance-scenarios.md`（使い方）と各ファイル先頭の説明（中身）へ移し、この文書は消す。

## 何のためか

受入シナリオ（`tests/scenarios/`）は本物の `claude -p` を呼ぶので、費用がかかり、結果が毎回揺れ、既定では回さない。
一方、hook の判定そのものは `tests/guard/` などが hook に渡る JSON を直接実行ファイルに渡して確かめている。
今どちらにも入っていないのは、次の 3 つ。

1. **hook の登録**: `.claude/settings.json` のイベント・matcher・順番が正しく、判定が実際に呼ばれるか
2. **何手も続く流れ**: SessionStart の文脈、拒否のあとに案内された手で通る流れ、Stop の差し戻し、`--resume` でのつなぎ、サブエージェントの開始と終了
3. **実行器の通し**: 準備 → 回す → stream-json を読む → `decisions.jsonl` と突き合わせる → 後片付け

代役は「モデルが何を打つか」を台本に固定し、それ以外（Claude Code が hook を呼んでツールを実行する部分）を真似る。
モデルが案内に従うか（`judge` で読む項目）は対象外で、これまでどおり本物で確かめる。

## 全体の形

```
シナリオ（agent: script）
  └ runner（drive.run_step）── claude の代わりに代役を起こす ──┐
                                                               ▼
                          tests/fake_claude.py（代役）
                            ├ settings.json を読み、hook を選ぶ
                            ├ 台本の 1 手ごとに PreToolUse → ツール実行 → PostToolUse
                            ├ 台本の終わりに Stop（差し戻されたら続きの台本）
                            └ stream-json を標準出力へ（本物と同じ形）
```

- 代役は `tests/fake_claude.py` に 1 本で置く。`fake_gitlab.py` と同じく実行器には依存しない（実行器が代役を呼ぶ向きだけ）
- 実行器は、シナリオの `agent` が `script` のとき、`claude` の代わりに `python -m tests.fake_claude` を同じ引数で起こす。差はコマンドの先頭と、台本の置き場を渡す環境変数 `CCNAVI_FAKE_CLAUDE_SCRIPT` だけ
- 代役は本物と同じ引数を受ける（`-p`・`--model`・`--output-format stream-json`・`--verbose`・`--include-hook-events`・`--max-budget-usd`・`--permission-mode`・`--session-id`・`--resume`・`--no-session-persistence`）。`--model` と `--max-budget-usd` は受けて捨てる。知らない引数が来たら終了コード 2 で止める（実行器が新しい引数を足したのに、代役が追従していないと気づけるように）

## 台本の形

シナリオの step に `script` を足す。`prompt` は残す（本物で回すときに使う。代役は UserPromptSubmit の `prompt` に入れるだけ）。

```yaml
agent: script            # 省略時は claude（本物）
steps:
  - prompt: "README.md を直接編集して。拒否されたら案内に従って"
    script:
      - tool: Edit
        input: {file_path: README.md, old_string: "a", new_string: "b"}
      - tool: Bash
        input: {command: "sh .ccnavi/scripts/ccnavi-git.sh worktree docs-1-readme"}
      - tool: Edit
        input: {file_path: .claude/worktrees/docs-1-readme/README.md, old_string: "a", new_string: "b"}
      - say: "直しました"
    after_stop_block:     # Stop で差し戻されたときに続ける手（任意）
      - tool: Bash
        input: {command: "sh .ccnavi/scripts/ccnavi-git.sh commit -m 'docs: README を直す'"}
```

- 1 手は `tool`＋`input`、`say`（最後の文。stream-json の `result` に入る）、`agent`（サブエージェント。下の「サブエージェント」）のどれか
- 台本は分岐しない。拒否されても次の手へ進む（本物のモデルが案内に従う前提を、台本を書く人が手で並べる）
- パスはワークスペースルートからの相対で書き、代役が絶対パスに直して `tool_input` に入れる（本物の Claude Code は絶対パスを渡す）
- 台本は「モデルが打つと期待する手」の写しなので、ccnavi の案内が変わると台本も直すことになる。それが案内の変更に気づく合図になる
- `expect` はそのまま使う。`judge` は代役では評価せず、報告に「代役では読まない」と出す

## hook の呼び方

- 読むのは `<ワークスペースルート>/.claude/settings.json` の `hooks` だけ。ユーザ設定・`settings.local.json`・プラグインの hook は読まない
- イベントごとに、`matcher` が無いか空なら全部、あれば `tool_name` に正規表現で当てる（Claude Code の決まりに合わせる）
- 命令は `sh -c` で起こし、環境に `CLAUDE_PROJECT_DIR` を足し、payload を標準入力に渡す。`timeout` を守る
- 同じイベントに当たった hook は、本物と同じく並行に起こす
- hook の環境には `CLAUDE_PROJECT_DIR`・`CLAUDE_ENV_FILE` を入れ、`CLAUDE_CODE_ENTRYPOINT` が無ければ `sdk-cli` を入れる（ccnavi は `sdk` で始まるかで非対話を見分ける）
- `asyncRewake` の hook は起こさず、起こさなかったことを stream-json の hook の行に残す

### payload に入れる欄

ccnavi が読む欄（`go/internal/infra/hookio/decode.go` の欄の一覧）を、イベントに合わせて入れる。

| イベント | 代役が出す時点 | 主な欄 |
|---|---|---|
| SessionStart | 起動時。`--resume` なら `source: resume`、それ以外は `startup` | `session_id`・`cwd`・`transcript_path`・`source` |
| UserPromptSubmit | step の初め | `prompt`・`permission_mode` |
| PreToolUse | 各手の前 | `tool_name`・`tool_input`・`tool_use_id`・`permission_mode` |
| PostToolUse / PostToolUseFailure | ツールを実行したあと（失敗なら後者） | `tool_response`（失敗なら `error`） |
| SubagentStart / SubagentStop | `agent` の手の前後 | `agent_id`・`agent_type` |
| Stop | 台本の終わり。差し戻しのあとは `stop_hook_active: true` | `stop_hook_active` |

欄の形が本物と合っていることは、下の「本物との契約」で確かめる。

### 応答の解釈

| hook の応答 | 代役の扱い |
|---|---|
| 終了コード 2 | 止める。標準エラーを理由にする（PreToolUse ならツールを実行せず、エラーの `tool_result` に理由を入れる。Stop なら差し戻し） |
| 終了コード 0 で `hookSpecificOutput.permissionDecision: deny` | PreToolUse を止める。`permissionDecisionReason` を `tool_result` に入れる |
| `permissionDecision: ask` | `-p` では deny と同じく止める（本物で録った。理由が `tool_result` に入り、`system/permission_denied` の行も出る） |
| `decision: block` | Stop は差し戻し（理由は `Stop hook feedback:` で始まる合成の `user` の行で渡る）。PostToolUse は `tool_result` を変えず、理由は hook の行にだけ出る |
| `additionalContext` | モデルに渡す文脈として stream-json に残すだけ（台本は読まない） |
| それ以外の終了コード | 止めない。hook の失敗として stream-json に残す |

Stop の差し戻しは、`after_stop_block` があればそれを実行して Stop をもう一度出し（`stop_hook_active: true`）、無ければそこで終わる。2 回目も差し戻されたら、それ以上続けず終わる（終わり方を `result` に残し、`expect` で比べられるようにする）。

## ツールの実行

台本に書けるのは、実際に実行できるものに絞る。

| ツール | 実行 |
|---|---|
| Bash | `sh -c` を `cwd` で起こす。標準出力・標準エラー・終了コードを `tool_response` にする |
| Read | ファイルを読む |
| Write | ファイルを書く |
| Edit | `old_string` が 1 か所だけ当たるときに置き換える（当たらなければ失敗にする。本物と同じ） |
| Task（`agent` の手） | 下の「サブエージェント」 |

ほかのツール（Glob・Grep・WebFetch など）は台本に書けない。読み込みの時点で止める。必要になったら足す。
Claude Code 自身の権限の仕組み（allow・deny の一覧、確認の表示）は真似ない。hook が止めなければ実行する。`--permission-mode` は payload に入れるだけ。

## サブエージェント

```yaml
      - agent:
          type: general-purpose
          description: "子 C-1 の実装"
          script:
            - tool: Write
              input: {file_path: ..., content: ...}
```

`Task` の PreToolUse → SubagentStart → 中の手（payload に `agent_id`・`agent_type`、stream-json に `parent_tool_use_id`）→ SubagentStop → `Task` の PostToolUse の順で出す。中の手が止められたら、その理由を `Task` の結果に入れる。

## 出力

stream-json は、`tests/scenarios/stream.py` が読む行を本物と同じ形で出す。

- `system` の `init`（`session_id`）
- `--include-hook-events` のときの hook の行（本物の `hook_response` の形。`tests/scenarios/fixtures/subagent-edit-denied.jsonl` が本物の見本）
- `assistant` の `tool_use`、`user` の `tool_result`（止められたら `is_error: true`）
- `result`（`result` は最後の `say`、`total_cost_usd` は 0）

セッションの記録（`transcript_path` が指すファイル）も、ccnavi が読む分だけ書く。置き場は代役の状態の置き場（`--no-session-persistence` でなければ `--resume` のために残す）。

## 本物との契約

代役が本物とずれると、代役では緑なのに本物で壊れる。GitLab の契約テスト（`.agents/docs/gitlab-contract.md`）と同じ組み方で防ぐ。

- 録る側: `tests/manual/claude/record_hooks.py`。hook の標準入力を書き出すだけの settings を置いた使い捨ての木で、本物の `claude -p` に決まった手（Read・Write・Edit・Bash・止められる Edit・Task・Stop の差し戻し・`--resume`）を打たせ、イベントごとの payload の形と stream-json の形を `tests/contract/claude/recorded.json` に録る。手で回す（費用がかかる）
- 比べる側: `tests/contract/` に足す。代役で同じ手を回し、`tests/contract/shape.py` の比較器で形を比べる。値（ID・時刻・パス）は比べない
- 比べるのは ccnavi が読む欄だけ（GitLab の `reads` と同じ考え方）。読まない欄は比べない
- Claude Code の版を上げたら録り直す。録った版を `recorded.json` に残す

## 確かめられないこと

- モデルが案内を読んで正しく動くか、拒否を迂回しないか（`judge`）。本物の受入シナリオと探索に残す
- 並行に起こした hook が終わる順番（本物とは揃わない）
- Claude Code 自身の権限の仕組みと、確認の表示
- 契約で録っていない手の形（録った手に限って本物と合う）

## 回す場所と台本の置き場

- 代役のシナリオは、使い捨てのワークスペース（`tests/e2e/` のように一時ディレクトリに組み立てる）で回す。普段のワークスペースルートに触れないので、人間の確認を取らず、全量テストに入れる。本物の `claude -p` で回すときの決まり（普段のワークスペースルートで、確認を取ってから）は変えない
- 台本は、本物用の `prompt` と同じシナリオのファイルに `script` として並べる。1 本のシナリオを、本物でも代役でも回せる

## 進め方

本物で先に録り、録った形を正として代役を作る。代役を仕様の読みだけで先に作らない。

| 段 | 中身 | これで回せるシナリオ |
|---|---|---|
| 1 | 本物で録る。`tests/manual/claude/record_hooks.py` で、本物の `claude -p`（Haiku）に決まった手を打たせ、hook の payload と stream-json の形を `tests/contract/claude/recorded.json` に録る。`permissionDecision: ask` の `-p` での扱いもここで確かめる | — |
| 2 | 録った形を元に、代役の芯（SessionStart・UserPromptSubmit・PreToolUse・PostToolUse・Stop、Bash・Read・Write・Edit、stream-json）と、比べる側の契約テスト、実行器の `agent: script` を作る | UC-01（済み。台本を書いた）。UC-02・03・04・05・07 は台本を足せば回せる |
| 3 | Stop の差し戻し、`--resume`、セッションの記録、PostToolUseFailure、サブエージェント。足りない手があれば段 1 に戻って録り足す | UC-08・09・18・19、fake-gitlab を相手にした UC-20 |
| 4 | 使い捨てのワークスペースで回し、全量テストに入れる | 上の全部を毎回回す |

## 本物で録った結果（段 1）

`tests/manual/claude/record_hooks.py` で Claude Code 2.1.325 を録った。形は `tests/contract/claude/recorded.json`、振る舞いはその `observations`。上の設計と違っていた点と、代役を作るときに合わせる点を挙げる。

- **hook は並行に起きる**: 同じイベントに当たった 2 本の hook の実行時間が重なった。代役も並行に起こす
- **サブエージェントの道具の名前は `Agent`**: `Task` ではない。サブエージェントの中では、終わるときに `SubagentHandback` というツールの PreToolUse・PostToolUse も出る。中の hook の payload には `agent_id` が入る
- **サブエージェントはバックグラウンドで動いた**: 終わると `system/task_notification` が出て、親の側で UserPromptSubmit がもう一度起きた。代役は、`agent` の手のあとに UserPromptSubmit を 1 回足す
- **`ask` と PostToolUse の `block`**: 上の「応答の解釈」の表に書いた
- **`--resume`**: SessionStart の `source` は `resume`
- **hook の環境**: `CLAUDE_PROJECT_DIR` は payload の `cwd` と同じで、hook の作業ディレクトリもそこ。ほかに `CLAUDE_CODE_ENTRYPOINT`・`CLAUDE_ENV_FILE` が入る
- stream-json には、`stream.py` が読む行のほかに `system/hook_started`・`system/task_*`・`rate_limit_event` などの行も出る。代役は、`stream.py` と ccnavi が読む行だけを出す

## 段 2 で作ったもの

| 置き場 | 中身 |
|---|---|
| `tests/fake_claude.py` | 代役の本体 |
| `tests/contract/claudeside.py` | 本物と代役で共有する場面（木・hook・打つ手）と、形・振る舞いへのまとめ |
| `tests/contract/test_claude_contract.py` | 代役の形と振る舞いを `recorded.json` と比べる（約 4 秒） |
| `tests/scenarios/schema.py`・`drive.py` | `agent: script` と step の `script`・`after_stop_block`。`agent: script` では `judge` を評価しない |

`agent: script` のシナリオも、今は本物と同じく `runner run … --confirmed` で回す（普段のワークスペースルートで、人間の確認を取ってから）。確認なしで回せるのは段 4 で使い捨てのワークスペースを作ってから。
