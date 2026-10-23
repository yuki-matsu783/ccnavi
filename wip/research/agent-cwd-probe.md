---
title: サブエージェント起動時の cwd 注入の実測（claude -p）
type: report
description: PreToolUse の updatedInput.cwd でサブエージェントの作業場所を変えられるかを claude -p で実測した結果。段階1のまとめ。
tags: [subagent, hook, agent-cwd]
keywords: [Agent, Task, cwd, updatedInput, SubagentStart, additionalContext, isolation]
---

# サブエージェント起動時の cwd 注入の実測（claude -p）

## 結論

**この環境（claude 2.1.309）では、PreToolUse の `updatedInput.cwd` は効かない。`permissionDecision: allow` を付けても効かない（E1・E1b・E1x・E2・E6・E7）。**
依頼文の前提（「hook が足すと効いた」）と食い違う。段階2は、分かれ道の表に従って止める。

一方で、`updatedInput.prompt` の書き替えは、`permissionDecision` 無しで体に届いた（E8）。

## 環境

| 項目 | 値 |
|---|---|
| claude | 2.1.309 (Claude Code) |
| `--setting-sources` | ある。全実験に `--setting-sources project,local` を付けた |
| ユーザ設定 | `~/.claude/settings.json` は無い。ユーザ設定の hook の混入は無い |
| 起動 | `env -i HOME PATH TERM=dumb CCPROBE_RUN=<ID> …`。認証は追加の変数なしで通った |
| 信頼の設定 | 初回の E0 は作業場所が未信頼で `permissions.allow` が無視された（hook は動いた）。`/root/.claude.json` の `projects["/tmp/ccnavi-probe/ws"].hasTrustDialogAccepted` を true にして打ち直した。E0 のログには未信頼の回が混ざる |
| 外側のセッション | `CCNAVI_MODE=dry-run`（依頼文は disable を想定）。実験は `/tmp/ccnavi-probe/ws` で動くため、実験自体には影響しない |

## 実験ごとの結果

| ID | 結果 | 要点 |
|---|---|---|
| E0 | 成立・hook は動く | SessionStart・UserPromptSubmit・Bash の PreToolUse/PostToolUse・Stop がある |
| E1 | 成立・**効かない** | 注入の hook は `cwd` を返した（inject.jsonl）。体の hook の cwd は `ws` のまま。Bash の pwd は `/tmp/ccnavi-probe/ws`。Read の相対パスは `ws/README-probe.txt` に解決され失敗。Write の相対パスは `ws/e1.txt` に書かれた。起動の PostToolUse の tool_input に `cwd` は無い（`description, prompt, run_in_background, subagent_type`）。Glob は体のツールに無く、体が find で代替した |
| E1b | 成立・効かない | E1 と同じ。揺れていない |
| E1x | 成立・**allow を付けても効かない** | pwd は `ws`。Read は失敗。Write は `ws/e1x.txt` |
| E2 | 成立・効かない | バックグラウンドでも pwd は `ws`。SubagentStop は終了より前にあった |
| E5 | 成立 | 1 回目の isolation 付き起動は DENY。メインは isolation を外して起動し直した（拒否の文面はそのままでよい）。E5b は未実施 |
| E6 | 成立（結果によらない） | 存在しない cwd を注入してもエラーは出ない。そもそも cwd が使われないため、無効な注入先の挙動は分からない |
| E7 | 成立・効かない | Explore の hook の cwd は `ws`。体はプロンプトの文面にある絶対パスを自分で読んで `ls` した。cwd が効いたのではない |
| E8 | 成立 | 既定でバックグラウンド（起動の PostToolUse が SubagentStart より先。SubagentStop の後に 2 回目の UserPromptSubmit が来る）。permissionDecision 無しの prompt の書き足しが体に届き、体は合言葉 KIWI-42 を報告した |
| E9 | **判定不可** | cwd が効かず、体の cwd が子のワークツリーにならなかったので、ctx.py は文を出さなかった（ctx.jsonl 無し）。SubagentStart の additionalContext が体に届くかは未確認 |
| E3・E3c・E4a・E4b・E4c・E5b・E9b・E9c・E10 | 未実施 | 分かれ道の表に従い、E1x でも効かないので、ここで止めた |

## 分かれ道の判断

| 観測 | 判断 |
|---|---|
| E1 で効かない → E1x でも効かない | **報告して止まる**（段階2に進まない） |
| E1b | E1 と同じ。揺れてはいない |
| E2 で効かない | 報告して止まる |

## 分かったこと（段階2の設計に効く）

- この版では、Agent の `cwd` は、hook が足しても Agent の引数として使われない（PostToolUse の tool_input にも出ない）。依頼文の前提は、別の版か別の条件での観測である可能性がある
- prompt の書き替え（E-2 の経路）は permissionDecision 無しで効く
- 体の hook の cwd は、起動時のメインの cwd のまま（前提の表と一致）
- isolation の拒否は、メインが外して起動し直す（B の文面で足りる）
- -p でも Agent の既定はバックグラウンド
- 未確認のまま: SubagentStart の additionalContext が体に届くか、入れ子で cwd がどうなるか、ask と updatedInput の組み合わせ
