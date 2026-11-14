---
title: claude -p の代役と、本物との契約テスト
type: guide
description: 受入シナリオを本物の claude -p の代わりに台本どおりに動く代役（tests/fake_claude.py）で回す仕組み。台本の書き方、代役が真似る hook の呼び方と応答の扱い、本物から録った形（recorded.json）と比べる契約テスト、録り直し、確かめられないこと
tags: [testing, scenario]
keywords: [代役, fake_claude, 台本, script, after_stop_block, --agent, claude -p, stream-json, hook, 契約テスト, claudeside, recorded.json, record_hooks, observations, SubagentHandback, CLAUDE_CODE_ENTRYPOINT]
---

# claude -p の代役と、本物との契約テスト

受入シナリオ（[acceptance-scenarios.md](acceptance-scenarios.md)）は本物の `claude -p` を呼ぶので、費用がかかり、結果が毎回揺れる。
代役（`tests/fake_claude.py`）は、モデルが打つ手を台本に固定し、Claude Code が hook を呼んでツールを実行する部分だけを真似る。
これで、hook の登録（`.claude/settings.json` のイベント・matcher）、何手も続く流れ（拒否・Stop の差し戻し・`--resume`・サブエージェント）、実行器の通しを、費用なしで毎回同じ結果で確かめられる。
モデルが案内に従うか（`judge`）は確かめられない。それは本物で回す。

## ファイル

| 置き場 | 中身 |
|---|---|
| `tests/fake_claude.py` | 代役の本体。実行器が `claude` と同じ引数で起こす |
| `tests/contract/claudeside.py` | 本物と代役で共有する場面（使い捨ての木、hook の入力を書き出すだけの hook、打つ手）と、形・振る舞いへのまとめ |
| `tests/contract/claude/recorded.json` | 本物の `claude -p` から録った形と振る舞い。**本物だけ** |
| `tests/contract/test_claude_contract.py` | 代役を同じ場面で回し、`recorded.json` と比べる（テストのグループ `contract`。約 4 秒） |
| `tests/manual/claude/record_hooks.py` | 本物から録る道具。手で回す（費用がかかる） |

## 代役で回す

シナリオの step に、本物用の `prompt` と並べて `script`（打つ手の列）を書き、実行器に `--agent script` を付ける。1 本のシナリオを本物でも代役でも回せる。

```yaml
steps:
  - prompt: "README.md を直接編集して。拒否されたら案内に従って"
    script:                      # 代役で回すときの台本。本物で回すときは使わない
      - tool: Edit
        input: {file_path: README.md, old_string: "a", new_string: "b"}
      - say: "拒否されました"
    after_stop_block:            # Stop で差し戻されたときに続ける手（任意）
      - tool: Bash
        input: {command: "sh .ccnavi/scripts/ccnavi-git.sh status"}
```

```sh
uv run python -m tests.scenarios.runner run tests/scenarios/UC-01-direct-edit-denied.yml --agent script --confirmed
```

- 1 手は `tool`（`Read`・`Write`・`Edit`・`Bash`）と `input`、`say`（最後の文。`result` に入る）、`agent`（サブエージェント。`type`・`description`・`script`）のどれか
- `file_path` は cwd（ワークスペースルート）からの相対で書く。代役が絶対パスに直して hook に渡す
- 台本は分岐しない。止められても次の手へ進む。案内に従う手は、台本を書く人が並べる。ccnavi の案内が変わると台本も直すことになり、それが案内の変更に気づく合図になる
- `--agent script` のときは、人間の操作でない step はすべて `script` が要る（読み込みで止める）。`judge` は評価しない
- 台本は出力の置き場に `step-N.script.json` として残る
- 代役でも、今は本物と同じく、普段のワークスペースルートで人間の確認を取ってから回す（[acceptance-scenarios.md](acceptance-scenarios.md) の「回し方」）。使い捨てのワークスペースで確認なしに回す仕組みは、まだ無い

## 代役が真似ること

### hook の呼び方

- 読むのは `<cwd>/.claude/settings.json` の `hooks` だけ。ユーザ設定・`settings.local.json`・プラグインの hook は読まない
- `matcher` が無いか空なら全部、あれば `tool_name` に正規表現で当てる
- 同じイベントに当たった hook は並行に起こす。`asyncRewake` の hook と `command` 以外の hook は起こさない
- 命令は `sh -c` で起こし、payload を標準入力に渡す。環境に `CLAUDE_PROJECT_DIR`（= cwd）と `CLAUDE_ENV_FILE` を入れ、`CLAUDE_CODE_ENTRYPOINT` が無ければ `sdk-cli` を入れる（ccnavi はこれが `sdk` で始まるかで非対話を見分ける）
- 呼ぶ順は本物と同じ: SessionStart（`--resume` なら `source: resume`）→ UserPromptSubmit → 手ごとに PreToolUse → PostToolUse か PostToolUseFailure → 終わりに Stop。サブエージェントは `Agent` の PreToolUse → SubagentStart → 中の手（`agent_id`・`agent_type` 付き）→ `SubagentHandback` → SubagentStop → `Agent` の PostToolUse。サブエージェントを使った回は、Stop の前に UserPromptSubmit をもう一度起こす（本物はサブエージェントの終わりを知らせで受ける）

### 応答の扱い

| hook の応答 | 代役の扱い |
|---|---|
| 終了コード 2 | 止める。標準エラーを `[command hook]: …` として理由にする |
| `permissionDecision: deny` | PreToolUse を止める。ツールは実行せず、`tool_result` をエラーにして理由を入れる |
| `permissionDecision: ask` | `-p` では deny と同じく止める |
| `decision: block` | Stop は差し戻す（理由は `Stop hook feedback:` で始まる合成の `user` の行）。`after_stop_block` を打ち、`stop_hook_active: true` で Stop をもう一度出す。2 回目も差し戻されたら、`result` の `stop_reason` を `stop_hook_blocked_twice` にして終わる。PostToolUse の `block` は `tool_result` を変えない |
| `additionalContext`・それ以外の終了コード | 止めない。hook の行に残すだけ |

ツールは実際に実行する（Read・Write・Edit・Bash）。Edit は `old_string` が 1 か所だけ当たるとき（`replace_all` なら全部）に置き換え、当たらなければ失敗にする。Bash は 0 以外で終われば失敗にする。
Claude Code 自身の権限の仕組み（allow・deny の一覧、確認の表示）とモデル・費用は真似ない。hook が止めなければ実行する。

## 本物との契約

代役が本物とずれると、代役では通るのに本物で壊れる。GitLab の契約（[gitlab-contract.md](gitlab-contract.md)）と同じく、本物から録った形と比べて防ぐ。
場面は `claudeside.py` が持ち、本物には自然文の `PROMPT`、代役には同じ手の台本 `SCRIPT` を渡す。

`test_claude_contract.py` が比べるもの:

- **形**: 代役が出す欄は本物にもあり、型は本物の型に含まれる（hook の payload はイベントとツールごと、stream-json は `type/subtype` ごと）。本物にだけある欄は比べない。代役は ccnavi と実行器が読む欄だけを出す
- **ccnavi が読む欄**: hook の payload で ccnavi が読む欄（`go/internal/infra/hookio/decode.go` の欄の一覧。テストの `READS`）と、`tool_response` の `type`（Write）・`agentId`（Agent）は、本物にあれば代役にもある
- **振る舞い**: `observations`（呼ばれたイベントの並び、止めたときの理由がどこに出るか、Stop の差し戻し、サブエージェント、`--resume` の `source`、hook が並行に起きるか、hook の環境）が本物と同じ

### 録り直す

Claude Code の版を上げたとき、代役に手を足すとき（`claudeside.py` の `PROMPT` と `SCRIPT` に同じ手を足す）に録り直す。本物の `claude -p` を呼ぶので、明確な指示があったときだけ回す。

```sh
uv run python tests/manual/claude/record_hooks.py --max-budget-usd 2 --dry-run   # 置かずに標準出力へ
uv run python tests/manual/claude/record_hooks.py --max-budget-usd 2
```

- 使い捨ての木で `claude -p --model haiku --setting-sources project` を 2 回（2 回目は `--resume`）回す。上限は 1 回ごとに `--max-budget-usd`。費用の合計を最後に出す
- 録るのは形（型とキー）と振る舞いだけで、値は持たない。キーにモデルの名前や呼び出しの ID が入る辞書は、キーを `<model>`・`<id>` にまとめる。モデルの名前・機械のパス・メールの形・呼び出しの ID が残っていれば置かない
- 値を含む生の記録は `scratchpad/claude-record/`（追跡されない）に残る。代役を直すときに読む
- 録り直したら `python -m tests tests/contract` を回し、落ちたら本物に合わせて代役を直す

## 確かめられないこと

- モデルが案内を読んで正しく動くか、拒否を迂回しないか（`judge`）
- 並行に起こした hook が終わる順番
- Claude Code 自身の権限の仕組みと確認の表示
- 場面で録っていない手（Glob・Grep・WebFetch などのツール、PostToolUse の `additionalContext` 以外の出力の形）。台本に書けるツールは Read・Write・Edit・Bash と `agent` だけ
