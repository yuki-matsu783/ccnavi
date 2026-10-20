---
type: adr
title: ccnavi 自身の保護と認証情報の保護を組み込みに寄せ、共通の rules.yml から外す
description: 共通の rules.yml と組み込みに二重にあった deny を組み込みに一本化する。実行前と実行後の保護範囲を同じ関数から作り、共通のルールファイルが読めない間の修復を下書きとユーザの取り込みに変える
tags: [rules, selfguard, tickets, builtin]
keywords: [組み込み, 共通ルール, 認証情報, guard-hooks, guard-settings, guard-scripts, guard-ccnavi-config, 承認済みの置き場, 実行後, handoff, 読めない間]
---

# ADR-0158: ccnavi 自身の保護と認証情報の保護を組み込みに寄せ、共通の rules.yml から外す

状態: 採用

## 状況

同じ保護が、組み込み（`policy/builtin.py`・`policy/selfguard_shell.py`・`tickets/ticket_guard.py`）と共通の
`.ccnavi/config/common/rules.yml` の 2 か所にあった。

- シェルから書く形の保護は組み込みにあり、名前で書くツール（Write / Edit / NotebookEdit）の hooks・settings・scripts・
  共通の設定の保護は rules.yml の `guard-*` にあった。保護の場所が揃っていなかった
- 認証情報は、組み込みの `builtin:credentials`（NotebookEdit と見本の例外が食い違う）と、rules.yml の
  `credentials` / `credentials-files` が重なっていた。再帰的な強制削除も、組み込みと rules.yml が同じ式を持っていた
- rules.yml 側の保護は、rules.yml を書き換えられると一緒に消える。ルールを壊して組み込みの既定に戻す経路が、
  Write / Edit の「修復を通す」例外と組み合わさっていた
- 実行後の保護範囲は rules.yml の `deny` / `ask` から導くので、組み込みで持つ保護は実行後に効かず、
  実行前と実行後で範囲がずれた

## 決定

### A. rules.yml から消すもの

deny の `recursive-delete`、`credentials`、`credentials-files`、`guard-hooks`、`guard-settings`、`guard-scripts`、
`guard-ccnavi-config`、`guard-approved-tickets`。残すのは `workspace-root`、`session-scratchpad`、`no-powershell`、
`find-writes`、`heredoc`。ask と allow は変えない。

### B. 土台（常に効く。`builtin.py`）

- `builtin:recursive-delete`: 今のまま
- `builtin:credentials`: `Bash|Read|Write|Edit|NotebookEdit`。`.env*`・`.dev.vars`・`.netrc`・`.npmrc`・`.pypirc`・
  `.aws/`・`.ssh/`・`*.pem` / `*.key`（後ろに別の拡張子が続く形も）・`secrets/`・`credentials/`・`credentials.json`・
  `id_rsa`・`id_ed25519`。見本の `.env.example` も止まる。Grep / Glob / PowerShell は対象外（今と同じ）。
  土台は設定で切れないので、誤検知が出ても外せない

### C. 自己防衛の保護（`CCNAVI_GUARD_CORE_FILES` の実効値が `disable` でなければ足す）

`selfguard_shell.add_named_rules` が 4 本を足す。`Write|Edit|NotebookEdit` で、書き込みの例外は
`handoff_write_exempt`（直下の `wip/handoff/` の普通の写し。ハードリンクでつながった写しは外さない）。

| id | 当てるもの |
|---|---|
| `builtin:guard-hooks` | glob `*/.claude/hooks/*` |
| `builtin:guard-settings` | regex `[\\/]\.claude[\\/]settings(\.local)?\.json$` |
| `builtin:guard-scripts` | glob `*/<ccnavi ディレクトリ>/scripts/*` |
| `builtin:guard-ccnavi-config` | regex `[\\/]<ccnavi ディレクトリ>[\\/]config[\\/]common[\\/](rules\|phases\|risks)\.yml$`（ルートを固定せず、パスの末尾で当てる） |

呼ぶ場所は 3 つで、二重に足さない。実行前の通常時は `add_rules` の後（先頭に挿すので、専用の文面が
`guard-project-home` / `guard-common-layer` より先に並ぶ）。実行前で共通のルールファイルが読めず組み込みの既定で
動いている間は、`add_rules` は呼ばずこの関数だけ。実行後は `events.watched_for` が各ツリーのルールの組に足す
（読めない間も同じ）。`add_rules` の中身は変えず、実行後の範囲に `guard-project-home` 全体は足さない。

### D. 共通の rules.yml が読めない間の修復

読めない間も `builtin:guard-ccnavi-config` は外さない。エージェントは共通のルールファイルを Write / Edit で直接直さない。
`wip/handoff/.ccnavi/config/common/rules.yml` に下書きを置き（例外で書ける）、ユーザが端末の `ccnavi user handoff`
で取り込むか、手で直す。今の中身が読めない rules.yml の取り込みは `handoff._defense_change` が「確かめられないので
端末に限る」と扱い、ボードからの取り込みは断られる。`ccnavi user handoff` は、共通のルールファイルが壊れていても
動く（テストで確かめた）。`builtin:guard-config-via-bash` と `builtin:guard-ccnavi-config` の文面が、この手順を案内する。
`selfguard._left_as_repair` は、dry-run で名前で書くツールが通った場合の既存の例外として残し、実行後のチェック
（`post.check`）には引き継がない。

### E. チケット制御の保護（承認済みの置き場）

承認済みの置き場は、チケット制御の保護として扱う。チケット制御が有効な間は自己防衛の値に依らない。

- 実行前: 既存の `ticket_guard.gate_rules`（`builtin:ticket-approved` / `-shell`）を使う。`builtin:guard-approved-tickets`
  は作らない。旧 `guard-approved-tickets` の案内（承認の経路、`status`、`finish`、`propose`）を、`builtin:ticket-approved`
  の文面に統合した
- 実行後: `watched_for` が、名前で書くツール用の 1 本（`builtin:ticket-approved`。`ticket_guard.approved_write_rule`）
  を保護範囲に足す。守るのは PostToolUse / PostToolUseFailure での未コミットの変更の検知と復元、Stop での残った変更の報告。
  **コミット済みの変更の Stop での報告は対象外。** `post._committed_findings` がチケットの置き場のコミット済みの変更を
  一律に外す今の設計は変えない。不正な変更をコミットするには、その前の書き込みが要る。その書き込みは実行前のゲートで止まり、
  実行後の検知にも掛かる。そこを抜けた場合は人間のレビュー（MR）で確かめる
- ccnavi のスクリプトによる正規の書き込みは、今の仕組み（`post.check` の `places`、ADR-0075。コマンド名ではなく変更の
  中身の形で見分ける）で外れる。handoff の写しの例外は付けない

### F. 文面の差し込み

`_insert` は `rules.parse` に root を渡さないので、文面の `{root}` / `{script:...}` は置き換わらない。C~E の文面は
`settings.script_command(root, ...)` で組む。

### G. 当たったルールの並び

C の 4 本は専用の文面が `guard-project-home` / `guard-common-layer` より先に並ぶ。approved は今の並び
（`guard-project-home` が先、`builtin:ticket-approved` が後）のまま。

### H. 実行後の各イベント

`restore` は `restore_if_deny` を全体モードと掛け合わせた実効値（`modes.effective_setting`）で判断する。全体モードが
dry-run なら復元しない。Stop は報告だけで復元しない。prompt / SessionStart は同じ保護範囲で基準を取り、前からある変更と
ユーザが受け入れた変更を差し戻さない。

### I. failsafe（`hook/failsafe.py`）

`_MINIMAL_DENY` を `builtin:credentials` に揃えた（`credentials/`、`.pem` / `.key` に別の拡張子が続く形を足した）。
failsafe 独自の通常より広い保護（`.gnupg`、`id_ecdsa`、`.p12` / `.pfx`、`.git`、操作記録の置き場）は残す。

### J. その他

`handoff.DEFENSE_ID_PREFIX` は判定を変えず、コメントだけ直した（レイヤーの rules.yml に利用者が書く防御のルールの印。
ccnavi 自身の保護は組み込みにある）。

## 理由

- シェル側と名前で書く側の保護の場所が揃っていなかった。全部を組み込みに置くと、保護の根拠を 1 か所で読める
- rules.yml を書き換えて防御を弱める経路がなくなる。保護がルールファイルの中にある限り、書けた時点で緩められる
- 実行前と実行後の保護範囲を同じ関数（`add_named_rules`、`approved_write_rule`）から作る。範囲がずれると、実行前に通った
  書き込みが実行後に報告される（あるいはその逆）
- 承認済みの置き場はチケット制御の保護として扱う。自己防衛の設定で切れる側に置くと、チケット制御が有効なのに承認済みの
  置き場が無防備になる
- イレギュラーな場面（共通のルールファイルが読めない間）は、人間に直してもらう。判定の経路に例外を足すと、その例外が
  攻める入口になる。例外的な場面で判定の経路が複雑になるときは止めて人間に依頼する、という方針も
  設計書 8.2.1 に書いた。

## 覆す判断

- `add_rules` の「hook の登録はワークスペースのルールに任せる」: 名前で書くツールの 4 本は組み込みが持つ
- ADR-0012 と `builtin.py` の「読めない間は Write/Edit での修復を通す」: 下書きとユーザの取り込みに変える
- ADR-0144 の「防御の記述」の判定（`guard-` で始まる id）は残る。ただし共通レイヤーには `guard-*` を書かなくなったので、
  この判定が働くのは「今ある deny」の条件だけになる

## 得たもの・失ったもの

- 得たもの: 保護が 1 か所にそろう。実行前と実行後の範囲が同じ関数から作られる。rules.yml の書き換えで防御が弱まる経路がなくなる
- 失ったもの: 見本の `.env.example` も、対象の 5 つのツールで止まる
- 失ったもの: 土台の認証情報の範囲が広がり、設定で外せない
- 失ったもの: 共通の rules.yml が壊れたとき、エージェントは自分で直せない。下書きを置き、ユーザが取り込むか手で直す
- 失ったもの: 自己防衛を `disable` にすると、hooks / settings / scripts / 共通の設定への名前で書くツールの保護は、
  実行前にも実行後にも足されない（hooks / settings のワークスペースルート側は `workspace-root` で実行前に止まる）
- 失ったもの: 承認済みの置き場は、チケット制御が有効な間は自己防衛の値に依らず、実行前は拒否し、実行後は未コミットの変更を
  検知・復元する。コミット済みの変更の Stop での報告は対象外で、MR のレビューで確かめる。チケット制御を `disable` にすると、
  実行後の保護は外れる（実行前は、自己防衛が有効なら `guard-project-home` で止まる）

## 採らなかった案

- 読めない間だけ、修復の対象を外す例外を足す: 判定の経路が増え、その例外が攻める入口になる
- 実行後の範囲に `guard-project-home` 全体を足す: ccnavi ディレクトリの下の正規の書き込みが報告に並ぶ
- 実行後のチェックに `_left_as_repair` を引き継ぐ: 読めない間の修復を実行後だけ通すことになり、実行前と範囲がずれる
- コミット済みの承認済みチケットの変更を Stop で報告する: 正規の書き込み（`ticket start` など）のコミットまで並ぶ。
  設計（`_committed_findings`）を変えず、MR のレビューに任せる
