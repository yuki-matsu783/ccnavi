---
type: design
title: チケット作業の案内の修正 1〜3 の設計
description: 実機の調査で見つけた案内の不備のうち、GitLab で --mcp-checked へ誘導する文、git push の止め文の食い違い、親ブランチが origin に無いときの内部エラーの 3 件の直し方
tags: [design-doc, tickets, sh-scripts]
keywords: [mcp-checked, ccnavi-start.sh, git-push, builtin, ccnavi-git.sh, write-and-push, origin, 親のブランチ, 案内, 次:]
---

# チケット作業の案内の修正 1〜3 の設計

チケット作業を間違った操作も含めて動かして見つけた不備のうち、効果が大きく直す量が少ない 3 件を直す。
基準は `docs/claude/script-output.md`（次の一手を `次:` で出す。詰まったら直接実装せず、報告して止まる）。

## 0. 入口の全パターン（issue・MR・ブランチ）

実装（`ccnavi-start.sh` 1192〜1520 行と、`tests/sh/test_start_sh.py` の固定）から、状態ごとの処理と案内を整理した。
「変更」の列が、この修正で変えるもの。「別件」は下の「別件」の節。

### A. `ccnavi-start.sh`（ホストに届く場合）

| # | 入力 | issue | 候補のブランチと MR | 処理 | 出力・終了コード | 変更 |
|---|---|---|---|---|---|---|
| A1 | `--issue N` | 実在 | 候補 0 件（名前に N を含むブランチも、紐づく MR も無い） | 新規作成。ブランチ・ワークツリー・初期コミット・push・Draft MR（`Closes #N`） | OK と「必須」の `issue:` `mr:`。0 | なし |
| A2 | `--issue N` | 無い／読む権限なし | — | 何も作らず中断 | 「番号を作らず、ユーザに確かめてください。issue が無ければ、ユーザに作ってもらい」。2 | 別件（エージェントが issue を作る） |
| A3 | `--issue N` | PR の番号 | — | 中断 | 「`--mr N` を使う」。2 | なし |
| A4 | `--issue N` | 実在 | 候補 1 件・open の MR あり（`adopt_check` を通る） | 聞かずにそのブランチで続ける（ワークツリー・早送り） | 0 | 別件（聞くか） |
| A5 | `--issue N` | 実在 | 候補 1 件・MR が open でない／フォーク／マージ先が統合先でない／保護ブランチ／識別子が使えない／別ブランチと識別子が衝突／同名のマージ済み MR あり | 中断 | 理由ごとの `adopt-*`。1 | なし |
| A6 | `--issue N` | 実在 | 候補 1 件・MR 無し・統合先と同じ先頭（取り込み済みの消し忘れ） | 一覧を出して中断 | 「このブランチを使うか・新しく切るか」を聞く。3 | なし |
| A7 | `--issue N` | 実在 | 候補 1 件・MR 無し・初期コミット 1 つだけ（作りかけ） | push と Draft MR を足して続ける | 0 | なし |
| A8 | `--issue N` | 実在 | 候補 1 件・MR 無し・作業コミットがある | 一覧を出して中断 | 「ユーザに MR（`Closes #N`）を作ってもらい `--mr` で」。3 | なし |
| A9 | `--issue N` | 実在 | 候補 1 件・実体なし（提案だけ） | 中断 | ユーザに確かめる。3 | なし |
| A10 | `--issue N` | 実在 | 候補 2 件以上 | 一覧を出して中断 | 「続けるか・新しく切るか・やめるか」を聞く。3 | なし |
| A11 | `--mr M` | MR が閉じる issue を確かめる | MR の元ブランチが候補 | A4・A5 と同じ。加えて閉じる issue を確かめる（0 件で中断、2 件以上で選択、`--issue` と違えば中断） | 0／1／3 | なし |
| A12 | `--mr M` | — | MR が無い | 中断 | 「番号を確かめてください」。1 | なし |
| A13 | `--branch B` | — | B と MR の元ブランチが違う／MR が無い | 中断 | `branch-mismatch`／`branch-no-mr`。1 | なし |

ブランチの置き場（手元だけ／origin だけ／両方）は、候補になった後の `ensure_worktree`・`fast_forward` が吸収する（手元に無ければ origin から取る。origin を取れなければ中断）。

### B. `ccnavi-start.sh`（ホストに届かない場合。終了コード 4）

| # | 状況 | GitHub | GitLab | 変更 |
|---|---|---|---|---|
| B1 | 候補の探索・マージ済み確認・題の読み取り・MR の作成・閉じる issue の確認 | MCP の手順を案内する。確かめたら `--mcp-checked` を付けて打ち直す | 「ブラウザで確かめ…」と案内する | 案1（ユーザに確かめてもらい、返事を待つ。確かめられなければ止まる） |
| B2 | 既存の MR を採用する形 | `--issue`・`--mr`・`--branch` の 3 つと `--mcp-checked` | 同じ | 案1（GitLab は主語をユーザにする） |

### C. 親のブランチが origin に無いまま `propose`・`start` したとき（案3）

`ccnavi-start.sh` を通さずに手で切った親のブランチ B。ccnavi が見るのは手元の事実だけ（B が origin に無い。送った形跡が無い）。MR の有無はホストの事柄で、エージェントは調べられないので、案内で `ccnavi-start.sh` に判断させる。

| # | B の状態 | 今 | 案内（案3 の後） |
|---|---|---|---|
| C1 | origin にある | 通常どおり | なし |
| C2 | origin に無い・送った形跡なし | `bad revision` の内部エラーで終わる | 何も書かずに止め、`次の手:` で 3 択を出す（下） |
| C3 | origin に無い・送った形跡あり（`started_at` など） | push の関門と `ccnavi-sync.sh` が従来どおり止める | 変えない |

C2 の 3 択（決めるのはユーザ。エージェントは直接実装に切り替えない）:

1. issue に紐づく MR が**すでにある**: その MR の元ブランチで作業するかをユーザに聞く。作業するなら `ccnavi-start.sh --mr <番号>`（`--issue` と一緒に渡せる）。手で切った B は使わない
2. MR は**無く、issue はある**: B を捨てて `ccnavi-start.sh --issue <番号>` を打つ。ブランチ・ワークツリー・Draft MR を作って進む。提案は新しい親のワークツリーへ移す。B にコミットがあれば先にユーザに伝える
3. B を使い続けたい: ユーザが `git push -u origin <B>` と MR（説明欄に `Closes #N`）の作成をしてから、`ccnavi-start.sh --mr <番号>` で採用する
   （issue が無いときは、ユーザに issue を作ってもらってから 2 へ。エージェントが作る変更は別件）

2 を選んだときに `ccnavi-start.sh --issue <番号>` が何をするかは、B の名前と中身で変わる（コードとテストで確認済み）。案内はこれを前提にし、該当する出力はそのまま `ccnavi-start.sh` が出す。

| B の名前・中身 | MR | `ccnavi-start.sh --issue N` の結果 |
|---|---|---|
| 名前に N が無い | 無し | 新しいブランチを切る。B は手元に残る（二重になるので、案内で「B のワークツリーを片付ける」と添える） |
| 名前に N が有る・統合先と同じ先頭 | 無し | A6（一覧を出して中断。使うか新しく切るかを聞く） |
| 名前に N が有る・初期コミット 1 つ | 無し | A7（push と Draft MR を足して続ける） |
| 名前に N が有る・作業コミット有 | 無し | A8（ユーザに MR を作ってもらう） |
| 何でも | 別のブランチの open の MR が有る | その MR のブランチで続ける（A4。B は無視） |

## 1. GitLab で API に届かないとき、確かめていない `--mcp-checked` を促さない

### 問題
`ccnavi-start.sh` はホストに届かないと終了コード 4 で止まる。GitLab（`else` 側）の「手で行う手順」の 2 は、
`search`・`merged` の採用なしの枝（814・821 行）と `title` の枝（825 行）で、「ブラウザで確かめ、無ければ `--mcp-checked` を付けて再実行」と言う。
エージェントにはブラウザも GitLab の MCP も無い。確かめずにフラグだけ付ければ、既存 MR の見落としとマージ済みブランチの再利用を素通りする。
GitHub 側は、エージェントが MCP で確かめる主体になれるので、同じ文でも筋が通る。GitLab だけが確かめる主体を持たない。

### 方針と限界
`--mcp-checked` は「誰かが実在・重複なしを確かめた」ことの申告で、スクリプトは検証できない（`ccnavi-start.sh` 168 行）。
文面で防げるのは、確かめる主体をユーザにして、確かめられないときの行き先を「止まる」にするところまで。偽って付ける道は文面では塞がらない。

GitLab の枝の手順 2 を次の形にする（`script-output.md` の「ユーザに〜を頼んで返事を待つ」の形）。

- `search`（採用なし）: 「ユーザに、ブラウザで <path> の MR の一覧から issue #N に紐づく MR が無いと確かめてもらい、返事を待つ。無いと分かったときだけ `<打ち直すコマンド> --mcp-checked` を打つ。確かめられなければ付けず、直接実装にも切り替えず、ユーザに報告して止まる」
- `merged`（採用なし）: 同じ形で、「ブランチ <B> のマージ済みの MR が無い」を確かめてもらう
- `title`: 「ユーザに、ブラウザで issue #N の題を読んで伝えてもらい、返事を待つ。実在と題が分かったときだけ `<打ち直すコマンド> --slug <語> --title '<題>' --mcp-checked` を打つ。…」
- 打ち直すコマンドは全文を出す（GitHub 側の 750・765 行と同じ。そのまま打てる形）

同じ GitLab の枝の `create`・`issue-link`・`mcp-args` も、「ブラウザで」「MCP などで確かめ」の主語がエージェントのままだと、`<作った番号>` や `--issue` の値を作り話で埋めて打てる。主語を「ユーザに…してもらい、その値を受けて」にそろえる範囲に入れる（`mcp-args` の「MCP などで」は GitLab では「ユーザに」）。

`次:` は付けない。手順 1・2 の分岐があり、次の一手が 1 つに決まらないため（`script-output.md` の「決まらないときは `次:` にしない」）。

### 変更点
- `.ccnavi/scripts/ccnavi-start.sh`: GitLab 枝の 814・821・825 行と `create`・`issue-link`・`mcp-args` の文、`--help`（91 行）の「エージェントが別の手段（MCP など）で確かめたあとに付ける」を「ユーザまたは MCP が確かめたあと」に
- `.ccnavi/docs/worktree-start.md`（58・72 行）を同じ言い方に

### テスト
`tests/sh/test_start_sh.py` に、GitLab の代役で API が失敗する場面（issue 検索・マージ済み確認・題の読み取り・MR の作成・閉じる issue の確認）を追加する。
- 出力に「ユーザに」と「止まる」と打ち直すコマンドの全文が含まれる
- 旧文（「ブラウザで…確かめ、…`--mcp-checked` を付けて再実行します」）が残らない
- 終了コードは 4 のまま。GitHub 側の出力は変わらない

## 2. `git push` の止め文を、`ccnavi-git.sh` がある環境に合わせる

### 問題
`builtin:git-push` の文は固定で「エージェントは git push を実行しません。push はユーザに頼んでください」。
隣の `recursive-delete`・`git-reset-hard` は `wrapper`（`ccnavi-git.sh` の有無）で文を分けている。
生の `git push` では `guard-raw-git`（「`ccnavi-git.sh` を使え」）も同時に出る（どちらも base で、`judge.py` で併記される）ので、食い違う。
プロジェクトが `git-push` の規則を足した環境では、そちらだけが出るので、この修正は効かない（範囲外）。

### 事実（`ccnavi-git.sh` の push）
- 親のワークツリー（`.claude/worktrees/<親>`）のブランチは `push [-u] [origin] [今のブランチ]` で送れる。送る前に push の関門（`push-parent-missing`・`push-parent-unverified`）を通り、拒まれたら「ユーザに伝えて止まる」と案内される
- 子のワークツリーのブランチは送らない（`push-child-worktree`。親が取り込んでから親のワークツリーで送る。子は結果を報告して終わる）
- 統合先、`--force`・`--delete`・`--no-verify`・refspec は拒む。統合先への直接の取り込みの送りはユーザが端末で打つ（`次:` 1389 行）

### 方針
`wrapper` があるとき（`settings.has_git_wrapper(root)`）の文を、断定を避け、通る道と拒まれる道が分かる形にする。

> git push は直接実行しません。親のワークツリーのブランチは `{git} push` で送ります。子のワークツリーのブランチは送らず、親が取り込んでから親のワークツリーで送ります。統合先への送りはユーザが打ちます。通る形は `{git}` の `--help` を見てください

`wrapper` が無いとき（`ccnavi-git.sh` が無い配布）は現状のまま。併記される `guard-raw-git` の文とは矛盾しなくなるので、規則の統合や優先づけはしない。

### 変更点
- `src/ccnavi/policy/builtin.py` の `builtin:git-push` の `message` を `wrapper` で分岐

### テスト
- `tests/config/test_config_absent.py` の `BuiltinGitWordingTest` の `IDS` に `builtin:git-push` を足す。wrapper ありは「ラッパーのコマンドを含む」「素の `git ` を案内に使わない」を自動で検査する
- wrapper なしは、従来の「ユーザに頼んでください」が残ることを個別に見る

## 3. 親のブランチが origin に無いまま `propose`・`start` したときの内部エラー

### 問題
`ccnavi-start.sh` を通さず手で `worktree add -b` した親は、origin に親のブランチが無い。ここで `ccnavi-ticket.sh propose`・`start` を打つと、

1. 1 回目の sort（268 行）は基点なしで通る
2. `ccnavi-sync.sh` が「リモートにありません(まだ送っていない親子のチケット)。今の手元の動きのまま」と言って 0 で通す
3. 2 回目の sort（`ccnavi-common-write-and-push.sh` 306 行）が `refs/remotes/origin/<ブランチ>` を基点に `git diff` して失敗し、
   `write-and-push sort: refs/remotes/origin/i0002..HEAD の差分を読めない(fatal: bad revision …)` で終わる

### 事実（調査で確かめた）
- push の関門は、送った形跡が無い親の初回 push を通す（`ccnavi-common-state.sh` 257〜262・293〜299 行）。止めるのは、形跡があるのに origin に無いときだけ
- `ccnavi-start.sh` は手で切ったブランチを拾わない。名前に issue 番号が無ければ候補にならず、別のブランチを新しく切って二重になる。
  候補になっても、統合先と同じ先頭なら「古いブランチらしい」として終了コード 3 で止まる（`test_stale_branch_without_mr_or_initial_commit_asks_instead_of_resuming`）。
  採用して続けるのは、統合先より 1 コミットだけ進んで題が初期コミット（`chore: #N の作業を開始する`）のブランチだけ
- したがって「`ccnavi-start.sh` で作り直す」を固定の案内にしない

### 方針
共通関数（`ccnavi-common-write-and-push.sh`）は事実だけを言って 1 を返し、`次:` は呼び出し元（`ccnavi-ticket.sh`）で足す
（`script-output.md`: 共通関数に `次:` を出さない。`ccnavi_wap_prepare` は `ccnavi-common-review.sh`・`ccnavi-user-decide.sh` からも呼ばれる）。

2 回目の sort の手前で、次を確かめる。

1. `ccnavi_parent_info "$ccnavi_wap_tree" "$ccnavi_wap_family_id" || :` を明示的に呼ぶ（`ccnavi_parent_trace` は `ccnavi_pi_*` に依存する。関門は一致しない親で早期に戻るため、値が古いままになりうる）
2. `ccnavi_parent_trace`（形跡）が無く、`show-ref --verify --quiet refs/remotes/origin/<B>` も無いときだけ、何も書かずに 1 で止める。形跡があるのに ref が無いときは従来どおり push の関門と sync に任せる。`ccnavi_pi_approved = unreadable` の扱いは関門に合わせる
3. この確認は hook マーカー（b）のコミットより後に置く（1 回目の sort の後に `ccnavi_wap_commit` が走りうるため）。止めるのは「sort の失敗の手前」で、「コミットが増えない」は保証しない。テストは、マーカーが無い場合に何も書かないことを見る

事実の文（共通関数が言う）:
> 親のブランチ <B> はリモートにありません。一度も送っていない親子のチケットです。何も反映していません

案内（`ccnavi-ticket.sh` が足す。`script-output.md` の `次の手:` に番号つきで並べる。決められるのはユーザ）:

```
ccnavi-ticket: 親のブランチ <B> はリモートにありません。ccnavi-start.sh を通さずに切った親です。何も反映していません
次の手: ユーザに状況を伝えて、どれにするか聞いて返事を待つ。直接実装には切り替えない
  1. issue に紐づく MR がすでにある: その MR のブランチで作業するかを聞く。作業するなら ccnavi-start.sh --mr <MR の番号>（--issue <番号> と一緒に渡せる）
  2. MR は無く、issue はある: <B> を使わず ccnavi-start.sh --issue <番号> で新しく切る（ブランチ・ワークツリー・Draft MR を作る）。提案は新しい親のワークツリーへ移す。<B> にコミットがあれば先に伝える
  3. <B> を使い続ける: ユーザが git push -u origin <B> と MR（説明欄に Closes #<番号>）の作成をしてから、ccnavi-start.sh --mr <MR の番号> で採用する
案内: issue が無いときは、ユーザに issue を作ってもらってから 2 へ進む
```

`<番号>` は提案の `issue:`・`mr:` が決まっていればその値を埋める（`ccnavi_parent_info` が読む）。決まっていなければ `<…>` のまま残す。
wrapper の有無で 3 の `git push` の書き方は変えない（ユーザが打つ操作）。

あわせて `ccnavi-sync.sh` の「リモートにありません(まだ送っていない親子のチケット)。今の手元の動きのまま」の行（699 行）に、ここで止まる場合があることを足す（「ccnavi-start.sh を通さずに切った親は、propose・start の前にリモートへ送る必要がある」）。

### 別件（この修正に含めない）
「issue も無いとき、エージェントが issue を作って、ブランチと紐づけた MR まで作って進める」は、今の決まり
（issue を作るのは頼まれたときだけ。`worktree.md`・`ticket-work.md`・`.ccnavi/CCNAVI.md`）を変える。
`ccnavi-start.sh` に issue 作成の枝（GitHub・GitLab 両方、書き込み権限の事前確認、題と本文の決め方、重複 issue の防止）を足す別の設計になる。
既存 MR があるときに「そちらで作業するか聞く」を `ccnavi-start.sh` の常時の動作にするか（今は候補が 1 件で open の MR があれば聞かずに続ける）も、同じ設計で決める。

### 変更点
- `.ccnavi/scripts/ccnavi-common-write-and-push.sh`: 2 回目の sort の前に確認と事実の文
- `.ccnavi/scripts/ccnavi-ticket.sh`: 失敗の理由を見て `次:` を足す（共通関数が失敗の種類を変数で返す）
- `.ccnavi/scripts/ccnavi-sync.sh`: 699 行の文

### テスト
`tests/sh/test_write_and_push_propose.py` と `test_write_and_push_ticket.py` に、origin に親のブランチが無い親での `propose`・`start` を足す。
- 終了コード 1
- 出力に `ccnavi-start.sh --issue` と「リモートにありません」が含まれ、`bad revision`・`差分を読めない` は含まれない
- 何も書かない。形跡がある親（`started_at` あり）で ref が無い場合は従来の止め方のまま
- `ccnavi-review.sh`・`ccnavi-user-decide.sh` 経由の同じ状態でも、`propose` に言及する文が出ない

## 進め方と範囲外

- 3 件は独立。1 → 2 → 3 の順で別コミットにする（変更の影響範囲が重ならない）
- 1・3 の文は `script-output.md` を満たす。ADR 番号は書かない。`ccnavi-common*.sh` に `次:` を出さない
- 範囲外: 調査の 4 以降（打ち間違えた id の案内、毎回の事前確認の出力の整理、空の子の `finish`、`ccnavi-review.sh` の `次:` ほか）と、上の「別件」
