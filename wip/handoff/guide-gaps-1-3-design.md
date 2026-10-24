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

## 1. GitLab で API に届かないとき、`--mcp-checked` で迂回させない

### 問題
`ccnavi-start.sh` はホストに届かないと終了コード 4 で止まる。GitLab（`else` 側）の「手で行う手順」の 2 は、
`search`・`merged` の採用なしの枝で次のように言う。

- 「ブラウザで MR の一覧から…確かめ、無ければ `--mcp-checked` を付けて再実行します」（814・821 行）
- `title` の枝（825 行）も、確かめたあとに `--mcp-checked` を付けると言う

エージェントにはブラウザも GitLab の MCP も無い。確かめずにフラグだけ付ければ、既存 MR の見落としとマージ済みブランチの再利用を
素通りする。止める目的の関門（重複 MR を作らない）を、案内が外す。
同じ GitLab の枝でも、採用ありの枝（`sh_adopt`）と GitHub 側は「確かめられないときは、ユーザに相談して止まる」を持つので、揃っていない。

### 方針
`--mcp-checked` は「ユーザまたは MCP が実在・重複なしを確かめた」ことの申告。確かめた主体がいないとき、エージェントは付けない。

GitLab の枝の手順 2 を次の形にそろえる（`search`・`merged`・`title`）。

- 確かめる手段をエージェントは持たないので、「ユーザに、ブラウザで<確かめる中身>を見てもらい、結果を受けてから」と、頼む主体をユーザにする
- ユーザが「無い（実在する）」と答えたときだけ `--mcp-checked` を付けて再実行する
- 返事が得られなければ、`--mcp-checked` を付けず、直接実装にも切り替えず、ユーザに報告して止まる
- 1 行にする（`script-output.md`）。次の一手は手順の最後の 1 行にまとめ、`次:` は付けない（手順の見出し内なので）

例（`search`・採用なし）:
`2. ユーザに、ブラウザで <path> の MR の一覧から issue #N に紐づく MR が無いと確かめてもらう。確かめてもらえたときだけ --mcp-checked を付けて再実行し、返事が無ければ付けず、直接実装にも切り替えずに止まる`

`create`・`issue-link` の枝（ブラウザで MR を手で作る・読む）は、結果の値を打ち直しに使うだけで関門を外さないので、文を変えない。ただし
「ブラウザで」の主語はユーザにそろえる。

### 変更点
- `.ccnavi/scripts/ccnavi-start.sh` の 814・821・825 行付近の 3 文
- 手順 1 の「（ユーザに頼む）」はそのまま

### テスト
`tests/sh/test_start_sh.py` に、GitLab の代役で API が失敗する 3 場面（issue 検索・マージ済み確認・題の読み取り）を追加する。
- 出力に「ユーザに」と「止まる」が含まれる
- 出力の「ブラウザで…確かめ、…`--mcp-checked` を付けて再実行します」という旧文が残らない
- 終了コードは 4 のまま

## 2. `git push` の止め文を、`ccnavi-git.sh` がある環境に合わせる

### 問題
`builtin:git-push` の文は固定で「エージェントは git push を実行しません。push はユーザに頼んでください」。
隣の `recursive-delete`・`git-reset-hard` は `wrapper`（`ccnavi-git.sh` の有無）で文を分けている。
`git push` だけ分けていないので、同じ呼び出しで次の 2 文が同時に出て食い違う。

- `builtin:git-push`: ユーザに頼め
- `builtin:guard-raw-git`: `ccnavi-git.sh` を使え

チケット作業の手順では、親のワークツリーの push はエージェントが `ccnavi-git.sh push` で行う（`ticket-flow.md` の 7・12）。
`ccnavi-git.sh push` が通すのは、居るブランチを送る形（`-u`・リモート名・同じブランチ名だけ）で、次は拒む。
子のワークツリー、統合先、`--force`・`--delete`・`--no-verify`・refspec。統合先への直接作業の取り込みの push はユーザが打つ。

### 方針
`wrapper` があるとき（`settings.has_git_wrapper(root)`）の文を、通る道と拒まれる道の両方が分かる形にする。

> 生の git push は実行しません。親のワークツリーのブランチは `{git} push` で送れます。子のワークツリー・統合先・取り込み後の送り先はユーザに頼んでください。

`wrapper` が無いとき（`ccnavi-git.sh` が無い配布）は現状のまま。

`guard-raw-git` が同時に出るのは、生の `git push` が両方の規則に当たるため。2 文が併記されても矛盾しなくなるので、規則の統合や優先づけはしない。

### 変更点
- `src/ccnavi/policy/builtin.py` の `builtin:git-push` の `message` を `wrapper` で分岐（`git` は既に使っている `settings.git_command`）
- 環境に `ccnavi-git.sh` が無い場合の既存テスト（`tests/config/test_config_absent.py`）は文が変わらないので影響しない

### テスト
- `tests/config/` に、`wrapper` ありで `builtin:git-push` の文に `ccnavi-git.sh push` が含まれ、「ユーザに頼んで」だけでないことを見るテストを足す
- `wrapper` なしで従来の文のままであることを見る

## 3. 親のブランチが origin に無いまま `propose`・`start` したときの内部エラー

### 問題
`ccnavi-start.sh` を通さず手で `worktree add -b` した親は、origin に親のブランチが無い。ここで `ccnavi-ticket.sh propose`・`start` を打つと、

1. `ccnavi-sync.sh` が「リモートにありません(まだ送っていない親子のチケット)。今の手元の動きのまま」と言って 0 で通す
2. 続く `ccnavi_wap_sort`（`ccnavi-common-write-and-push.sh` 306 行）が `refs/remotes/origin/<ブランチ>` を基点に `git diff` して失敗し、
   `write-and-push sort: refs/remotes/origin/i0002..HEAD の差分を読めない(fatal: bad revision …)` で終わる

利用者向けの手がかりが無い。1 の「今の手元の動きのまま」が通すと読めるのに、2 で落ちる。

### 方針
一度も push していない親は、チケット作業の入口（`ccnavi-start.sh`）を通っていないので、**入口へ戻す案内で止める**。
ccnavi が origin にブランチを作り直さない（push の関門が「親のブランチがリモートから消えていれば、push で作り直さずに止める」と決めている）ことと整合する。

`ccnavi_wap_sort` を呼ぶ手前（5. の直前、push の関門の後）で次を確かめる。

```sh
git -C "$ccnavi_wap_tree" rev-parse --verify -q "refs/remotes/origin/$ccnavi_wap_branch" >/dev/null
```

無ければ、何も書かずに終了コード 1 で、次を言って止める（`script-output.md` の形）。

> 親のブランチ <ブランチ> がリモートにありません。ccnavi-start.sh を通さずに切った親のワークツリーです。何も反映していません
> 次: ccnavi-start.sh --issue <番号>（MR があれば --mr <番号>）で親のワークツリーと Draft MR を作り直し、提案を移す。直接実装には切り替えず、作り直せなければユーザに報告して止まる

ただし、すでに送った形跡（`ccnavi_parent_trace`）があるのに ref が無い場合は、取得漏れの可能性があるので、従来どおり push の関門と
sync に任せる（この追加の確認は形跡が無い場合だけ）。

### 案内の分岐（ユーザの指示で追加）
origin に親のブランチが無いときの次の一手は、issue と MR の有無で変える。固定の 1 文にしない。

| 状態 | 案内 |
|---|---|
| issue に紐づく MR がすでにある | MR の元ブランチがあるはず。手で切ったブランチではなく、そちらで作業するかをユーザに聞く。ユーザが選んだら `ccnavi-start.sh --mr <番号>`（`--issue` と一緒に渡せる）で続ける |
| issue だけある | 手で切ったブランチを捨てて `ccnavi-start.sh --issue <番号>` を打つ。ブランチ・ワークツリー・Draft MR（`Closes #<番号>`）まで作って進む。手で切ったブランチに積んだコミットがあれば、先にユーザに伝える |
| issue も無い | 現状はユーザに issue を作ってもらう。エージェントが作る変更は下の「別件」 |

エージェントはこの表のどれかを自分で確かめられない（ホストの MR を引けない）ので、案内は `ccnavi-start.sh --issue <番号>` を打つよう言う。
`ccnavi-start.sh` が MR の有無を調べて続ける・一覧を出す・作るを決める。ブランチだけの push をユーザに頼む案は、MR が付かないので採らない。

### 別件（この修正に含めない）
「issue も無いとき、エージェントが issue を作って、ブランチと紐づけた MR まで作って進める」は、今の決まり
（issue はエージェントが作るのは頼まれたときだけ。`worktree.md`・`ticket-work.md`・`.ccnavi/CCNAVI.md`）を変える。
`ccnavi-start.sh` に issue 作成の枝（GitHub・GitLab 両方、書き込み権限の事前確認、題と本文の決め方、重複 issue の防止）を足す別の設計になる。
既存 MR があるときに「そちらで作業するか聞く」を `ccnavi-start.sh` の常時の動作にするか（今は候補が 1 件で open の MR があれば聞かずに続ける）も、同じ設計で決める。

### 注意する点
- `ccnavi_wap_sort` の失敗文（`write-and-push sort: …差分を読めない`）は実装内の表現のまま。利用者向けの文は手前の確認で出す
- 手で作った親でも `ccnavi-start.sh --issue N` は既存のブランチを候補として拾う設計（候補が 1 件で MR 無し→作りかけなら MR を作って続ける）。
  ただし作りかけでないブランチ（コミットが載っている）は終了コード 3 で止まる。この場合の案内は、実装前に `tests/sh/test_start_sh.py` で確かめ、
  ユーザに「そのブランチを使うか、新しく切るか」を聞く形にする

### 変更点
- `.ccnavi/scripts/ccnavi-common-write-and-push.sh`: sort の前に ref の確認と案内（`ccnavi_wap_say` を使う）
- 同じ確認を使う操作（`start`・`finish`・`propose`・`consolidate`）はこの共通関数を通るので、1 か所で足りる

### テスト
`tests/sh/test_write_and_push_propose.py` と `test_write_and_push_ticket.py` に、origin に親のブランチが無い親での `propose`・`start` を足す。
- 終了コード 1
- 出力に `ccnavi-start.sh` と「リモートにありません」が含まれ、`bad revision`・`差分を読めない` は含まれない
- 何も書かず、コミットも増えない

## 進め方と範囲外

- 3 件は独立。1 → 2 → 3 の順で別コミットにする（変更の影響範囲が重ならない）
- `docs/claude/script-output.md` を満たすよう、1・3 の文は `次:` か「止まる」を持たせ、ADR 番号は書かない
- 範囲外: 調査の 4 以降（打ち間違えた id の案内、毎回の事前確認の出力の整理、空の子の `finish`、`ccnavi-review.sh` の `次:` ほか）
- 実装前に確認が要る点: 3 の `ccnavi-start.sh` が手で作ったブランチを採用するか
