---
type: requirements
title: ccnavi 要件書 2.10 診断コマンド
description: ユーザが直接起動する診断コマンド（実効権限の表示・試験・設定の検証など）の要件
tags: [design-doc, cli]
keywords: [要件, REQ-DIA, 診断, コマンド, 直接起動, 試験, 検証, 実効権限]
---

[ccnavi 要件書](../requirements.md) に戻る。

### 2.10 診断コマンド — REQ-DIA

| ID | 型 | 要求 |
|---|---|---|
| REQ-DIA-01 | 事象 | 実効権限の表示を求められたとき、ccnavi は、現在の設定・チケット・環境変数から決まる読み・書き・実行それぞれの許可を、判定を行わずに一覧すること |
| REQ-DIA-02 | 事象 | ツールと対象を与えて試験を求められたとき、ccnavi は、操作を実行せずに判定結果とすべての理由を返すこと |
| REQ-DIA-03 | 常時 | ccnavi が試験で返す判定は、同じ入力に対して実運用で返す判定と一致すること |
| REQ-DIA-04 | 事象 | 設定の検証を求められたとき、ccnavi は、防御を無効化しうる記述を error と warn に分けて報告し、error があれば非ゼロの終了コードで終わること |
| REQ-DIA-06 | 事象 | チケットの状態の機械可読な一覧を求められたとき（`--explain --json`）、ccnavi は、提案・承認済みチケット・マーカー・レビューで止まっているか・書き込みが止まっているか・ワークツリーの有無・承認待ちを、判定と同じ関数で求めた値として JSON で返し、その際にネットワークへ出ないこと |
| REQ-DIA-07 | 常時 | 機械可読な一覧と設定の検証が、チケット制御を使っているかどうかを示すこと（その宣言が `disable` のときの入口の出し方は拡張側。REQ-VSC-04） |
| REQ-DIA-08 | 事象 | 設定の検証を機械可読な形で求められたとき（`--lint --json`）、ccnavi は、ユーザ向けの文面と同じ苦情を同じ深刻度で JSON として返し、終了コードもユーザ向けの文面のときと同じにすること |
| REQ-DIA-09 | 事象 | 診断（試験・期待する判定・検証・実効権限の表示）で 1 つのプロジェクトのルールファイルの差し替え（`--project-rules-file <名前>=<パス>`）を与えられたとき、ccnavi は、その名前のプロジェクトのルールとして差し替え先のファイルを読むこと。レイヤーのフェーズ定義の差し替え（`--project-phases-file <名前>=<パス>`、名前は `self` かプロジェクト）を与えられたときも同じく、そのレイヤーのフェーズ定義として読み、共通レイヤーと合わせて検証すること（同じ識別子で中身が違う定義と、表示名の重なり）。レイヤーのリスクの配点の差し替え（`--project-risk-file <名前>=<パス>`、名前は `self` かプロジェクト）を与えられたときは、そのレイヤーの配点として読み、共通レイヤーと合わせて検証すること（合成後の境目の逆転、同 id の衝突、`script:` の存在確認はそのレイヤーの git プロジェクトルートを基準にする）。hook からの判定では差し替えを無視し、守る対象（REQ-MLT-08）は本来の場所のままとすること |
| REQ-DIA-10 | 事象 | 承認の対象の機械可読な提示を求められたとき（`--agree --preview --json`）、ccnavi は、承認の対象・承認画面の本文・承認の対象にしない提案とその理由・読めない提案を JSON で返し、承認済みチケットを置かないこと。承認の対象は承認が使うのと同じ関数で求めること |

診断コマンドは hook から呼ばれず、ユーザが端末から打つ。REQ-DIA-06、REQ-DIA-02 の JSON 版
（`--test --json` / `--test-expectations --json`）、REQ-DIA-08 は VS Code の拡張（`extensions/vscode/ccnavi-board/`）も読む。
拡張は実行ファイルが出した判定の答えを並べる。

REQ-DIA-03 が無いと、報告するだけのモードや試験の結果が、判定を適用するモードの保証にならない。

### 2.10.1 設定の検証の指摘一覧

`ccnavi --lint`（REQ-DIA-04、REQ-DIA-08）が拾うものの一覧。使い方と出力の見方は [設定の確かめ方](../guide/check.md)。
各行の「深刻度」は報告の区分で、error があれば終了コードは 1、warn と info だけなら 0。

#### ルール

| 深刻度 | 拾うもの |
|---|---|
| error | ルールファイルが読めない、YAML として不正、版番号が違う |
| error | 文面（`deny` だけ）・`match`・`glob` を欠いたルール、`glob` と `regex` の両方があるルール |
| error | `ask` か `allow` に `message` を書いたルール（`ask` の文面はユーザの確認ダイアログにしか出ず、`allow` の文面はどこにも出ない。ルールは適用されたまま） |
| error | 組み立てられない正規表現、読み込み時に弾いている先読み・後読み・後方参照 |
| error | `deny` が空（何も止めないガードは、入っているように見えて入っていない） |
| error | `additionalContextFile` / `additionalContextOnceFile` が絶対パスか `..` で上に出るパス（実行時も読まない） |
| warn | `allow` が空（言及の無い呼び出しが全部、権限モードへ渡る） |
| error | `id` の無いルール（読み込まない。記録も報告もこのルールを名指しできない） |
| warn | `id` が重複するルール |
| warn | 判定が対象を取り出せないツールを `match` に書いたルール |
| warn | 本体（`glob` / `regex` 1 つ）が何にでも当たる `deny`（`.*` や `.` など。`match` のツールを丸ごと止める。`except` があれば除いたもの以外を止める。ツールごと止めると決めた `glob: "*"` ちょうどは言わない） |
| warn | 何にでも当たる、または選択肢が 3 つ以上ある `allow` に `additionalContext`（か `additionalContextFile`）を書いたルール（当たるたびに同じ文が積まれる。`additionalContextOnce` は文脈ごとに 1 度なので指摘しない） |
| warn | `additionalContextFile` / `additionalContextOnceFile` が指すファイルが無い（作るまで何も足さない）、または先頭 4000 文字を超える（先頭だけが届き、切ったことを添える） |

#### 設定とモード

| 深刻度 | 拾うもの |
|---|---|
| error | `.claude/settings.json` の `env` が `CCNAVI_MODE=disable` を宣言している |
| error | `CCNAVI_GUARD_TICKET_APPROVAL` / `CCNAVI_TICKET_CONTROL` が 2 値として読めない値（`enable` として扱って動く） |
| error | `.claude/settings.json` の `env` の `CCNAVI_ENTRY_PATH` が指す先は在るが、実行できない。hook が 126 で起動せず、何も判定していない |
| warn | モードが `disable` / `dry-run`、あるいはモードとして読めない値 |
| warn | 読めない `CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES` の値 |
| warn | 読めない `CCNAVI_STOP_REVIEW` の値（`dry-run` として動く） |
| warn | Stop の AI 判定の登録の食い違い: `CCNAVI_STOP_REVIEW_AGENT=1` があるのに agent 型 Stop hook が無い、hook はあるが印が無い（判定計画が作られず、すべて未検査になる）、hook が複数ある（判定が重複する） |
| warn | 判定役の Bash（`stop-review` の 2 つの入口）を許す権限が `permissions.allow` に無い（判定役は `dontAsk` で動くので、材料を取れず、すべて未検査になる） |
| warn | 守る働きを持つ設定が止めない値になっている（`CCNAVI_GUARD_CORE_FILES` と `CCNAVI_RESTORE_IF_DENY` が `disable` か `dry-run`、`CCNAVI_GUARD_UNWATCHED` と `CCNAVI_GUARD_TICKET_APPROVAL` が `disable`） |
| warn | 上書き設定ファイル `ccnavi.settings.local.json` が読めない。このファイルを読むのは ccnavi 自身のソースツリーだけ（[設計 4.3](../design/startup-config.md)） |
| warn | `CCNAVI_TICKET_CONTROL=disable`（チケットの範囲も HITL ポイントも適用されない） |
| warn | `CCNAVI_LEARNING` が `enable` / `disable` 以外の値（空白つきも）。`enable` として動く |

`CCNAVI_ENTRY_PATH` が実行できるかは POSIX でだけ見る。Windows は実行ビットを持たないため。パスは `env` に書いたとおりに見て、`.exe` は補わない。

#### レイヤー

| 深刻度 | 拾うもの |
|---|---|
| error | 自身のレイヤーかプロジェクトのレイヤーのファイルが不正（そのレイヤーを空として扱っている） |
| info | 裸の `id` と全欄が一致する宣言を、後ろのレイヤーで捨てた（ルール / フェーズ定義 / 配点） |
| warn | ルールの同じ `id` がレイヤーをまたいで中身違いで在る（両方とも適用されている） |
| error | 共通レイヤーの `phases.yml` が読めない（共通の定義を空として扱っている） |
| error | フェーズ定義で、共通レイヤーと同じ `id` で中身が違う、`title` が共通レイヤーの定義と重なる（レイヤーの側のその定義だけを捨てている。同じ `id` なら共通の定義を使う） |
| info | フェーズ定義で、共通レイヤーと全欄が同じ定義がレイヤーにある（レイヤーの側を捨てた） |
| error | フェーズ定義の 1 本の中で、同じ `id` が重複する、表示名が重なる、`scope` か `deliverables` が 20 件を超える（そのレイヤーを空として扱っている） |
| warn | 配点の同じ `id` で中身が違う（両方数え、後ろのレイヤーは `<レイヤー>:<id>`） |
| error | 合成後の `levels` が `medium <= high <= critical` になっていない（そのレイヤーを空として扱っている） |
| error | 配点の `script:` がレイヤーの外を指す（共通レイヤーから `.ccnavi/scripts/`、自身のレイヤーやプロジェクトのレイヤーから `.ccnavi/config/common/scripts/`）、または指す先が git プロジェクトルートに無い |
| error | `id` にコロンを書いた宣言（ルール / フェーズ定義 / 配点） |
| warn | ワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがある（統合されるまで反映されない）。承認済みの領域の下は数えない（そのツリーの版が読まれる） |

無いレイヤーは空で、正常な形である。

#### hook の登録とスクリプト

| 深刻度 | 拾うもの |
|---|---|
| warn | `PostToolUse` / `SubagentStart` / `SubagentStop` に ccnavi が登録されていない |
| warn | 登録はされているが git の作業ツリーではない（実行後チェックが何も検知しない） |
| warn | `.ccnavi/scripts/ccnavi-{ticket,review,git}.sh` が無い |
| warn | `.ccnavi/scripts/ccnavi-common.sh` の互換の版（`CCNAVI_COMPAT`）が実行ファイルと違うか、書かれていない（場所は `(version)`。直し方は [版の JSON](json-shapes.md#版の-json)） |

登録の検査は `.claude/settings.json` しか見ない（ユーザごとの設定は見えないため）。

#### チケットとフェーズ

（チケット制御が有効なときだけ）

| 深刻度 | 拾うもの |
|---|---|
| error | 承認済みチケットの置き場への `Write` / `Edit` をルールが止めていない（承認の意味が消える） |
| error | 同じ識別子が複数の置き場にある、子の識別子が `<親>-<2 桁のフェーズ番号>-<2 桁の連番>` の形でないかフェーズ番号が `phase:` と食い違う、連番が重なる |
| error | 孫を持つ子 |
| warn | 範囲の超過がある子。超過は、親の範囲かフェーズ定義の `scope` を超える項と、regex の項。親は、`allow` の項が計画のどの定義の `scope` とも重ならないとき（計画に `scope: inherit` の定義があれば言わない）。判定がその上限で切り詰めて止めるので、承認と同じく CI の終了コードを失敗にしない |
| error | ワークツリーの元リポジトリと承認済みチケットの `project:` が違う |
| error | 計画を持つ親に、計画が使う定義の写し（`phases:`）が無いか壊れている（判定でも親と子が止まる）、`phases.yml` / `risks.yml` 自身の誤り |
| warn | 承認済みの親の写し（`phases:`）が今の `phases.yml` と違う・`phases.yml` が読めない（進行中の親には効かない）、写しの使われていない定義 |
| error | 承認済みチケットが読めない |
| warn | 未承認の提案がある |
| warn | `predecessors` を満たしていない（`done/` に無いか取り消し済み）のに着手している子 |
| warn | チケットの無いワークツリー、識別子と名前の一致しないワークツリー |
| warn | ワークツリー側に置かれた承認済みチケット（読まれない） |
| warn | 承認済みチケットはあるがワークツリーが無い（範囲が適用されない） |
| warn | リモートの種類に合うトークン（`GITHUB_TOKEN` / `GITLAB_TOKEN`）が無い、`origin` が無い |
| warn | 未完了の操作記録（`logs/state/ops/`。送信の途中で止まった操作。decide は `--resume` で続きを送り、ほかの操作は打ち直す）。記録そのものが読めないものは error |
| warn | 読めない受け入れ台帳（`phases/<親>/accepted.json`）。空として扱うので、受け入れ済みの指摘が未解決に戻る |
| info | `finished_to` の無い `review/` の子（この変更より前に閉じた子）。手で `done/` に動かしても確定できないので、`confirm` か `decide` を使う |
| warn | `CCNAVI_GUARD_TICKET_APPROVAL` と `CCNAVI_GUARD_CORE_FILES` が両方 `disable`（承認の直接実行も設定ファイルの書き換えも止まらない） |

#### 取り込みと正とする側

取り込みの状態（旧 `logs/state/sync/`）は持たないので、親のワークツリーとチケットの置き場、取得済みの `origin/<統合先>` だけを見る。。

| 深刻度 | 拾うもの |
|---|---|
| error | `.claude/settings.local.json` の `env` に承認と判定に影響する値（チケット制御・承認の保護・ccnavi 自身の設定の保護・戻す働き・確かめられないモードの止め・同じ理由の拒否の数え方・モード）がある。例外は無い。`CCNAVI_ENTRY_PATH`・診断ログ・タイムアウト監視の秒は答えを変えないので指摘しない |
| warn | 名前が親の識別子の親のワークツリーが、別のブランチをチェックアウトしている（取り込みと正とする側の検査は、同じ名前のブランチだけを見る） |
| error | 親子のチケットが決まらない（`ccnavi sync check` で言う）: 親のワークツリーが無いか HEAD が別のブランチ、途中の操作がある、など。承認・状態の操作・実行前チェックがその親子のチケットを止める。解き方を添える |
| error | 承認済みチケットが、親のワークツリーの外にしか無い（元ツリーに未コミットで残ったチケットなど。信じないので止まる）。親の承認済みチケットに `mr:` が無いのも error |
| warn | 作業ツリーのレイヤー（共通レイヤー・自身のレイヤー）が、取得済みの `origin/<統合先>` のレイヤー（`git show refs/remotes/origin/<統合先>:<パス>`）と違う（統合先に入るまで、他の機械と Chrome の判定には使われない。`ccnavi sync check` でも同じ warn を言う） |
| info | 取得済みの `origin/<統合先>` が手元に無く、レイヤーのずれを確かめていない（1 行。`sync check` は黙る） |

#### プロジェクト

（索引の 2 つのほかは `projects/` にプロジェクトがあるときだけ）

| 深刻度 | 拾うもの |
|---|---|
| warn | ワークスペースの git の索引に、`projects/` の下の通常のファイル（入れ子のリポジトリ以外）が載っている（ぶつかり）。ワークスペース自身のソースに `projects/` があり、名前は変えられないので、ワークスペースの `projects/` を別の名前に移すよう案内する。プロジェクトを置かないならそのままでも動く |
| warn | ワークスペースの git の索引に、`projects/` の下の入れ子のリポジトリ（gitlink）だけが載っている（載せ忘れ）。`.gitignore` に入れる前に `git add` したものとみて、索引から外して `.gitignore` に `/projects/` を足すよう案内する |
| warn | `projects/` がワークスペースの `.gitignore` に入っていない。上の 2 つのどちらかが出るときは出さない（同じ原因で、`.gitignore` に入れる案内が誤りか、先に索引から外さないと効かないため） |
| error | 予約名（`common` / `self`。大文字小文字は問わない）のプロジェクトがある |
| warn | プロジェクトの中にワークツリーがある（名前を並べる）。ワークツリーはワークスペースの `.claude/worktrees/` に置く。ここのものは承認の走査の対象外で、書いた提案は承認待ちに並ばない。ワークスペースの ccnavi が古い（`projects/<名前>` の写しをルートと取り違える）可能性があるので、`scripts/ccnavi-setup.sh --force` で配り直し、このワークツリーはユーザに伝えて片付ける |

