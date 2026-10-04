# 写す sh（i0064-10 置き場の env と ADR の番号を外す）

`.ccnavi/scripts/` はエージェントが書けない場所なので、完成品をここに置き、人が写す。
設計は `wip/design/i0064-fixed-places.md` §3、チケットは `.ccnavi/approved/doing/i0064-10.md`。

**この手順は人が端末で打つ。** エージェントは `.ccnavi/scripts/` に書けず、git も `ccnavi-git.sh` を
通すので、ここに書いた `cp` や生の `git` をエージェントがそのまま実行しない。
ここに書いた `git` はどれも `ccnavi-git.sh` が通す形にしてある（`git` を
`sh .ccnavi/scripts/ccnavi-git.sh` に置き換えても通る）。`hash-object` は通らないので、
blob は `ls-tree` と `ls-files -s` で照らす。

## なぜ写し直すか

- **置き場の env を外す。** 親に main を取り込み直したとき、保護済みの sh は main の版をそのまま採った。
  main の取り込み（`ccnavi-sync.sh`）と承認（Chrome 拡張の承認の ADR-0093）の sh が置き場の env を読んでいるので、ここでまとめて外す
- **ADR の番号を外す。** コメントの `（ADR-0092）` を外し、`# 固定` とだけ書く。番号が動いても sh を写し直さないため。
  番号は文書の中だけで振る

## いつ・どこで打つか

**i0064-10 を親 i0064 に取り込んだあと、親のワークツリー（`.claude/worktrees/i0064`、
ブランチ `i0064`）で打つ。** 子のワークツリー（`.claude/worktrees/i0064-10`）でも、
ワークスペースルート（`main`）でもない。

- 子のワークツリーで写すと、写したものは子のブランチに載り、親の取り込みを待つ間に
  親の `.ccnavi/scripts/` と食い違う
- ワークスペースルートで写すと `main` に直に入る。しかも `main` にある `wip/design/scripts/` は
  別の古い写しなので、それを写すことになる

統合先へは親のブランチごと入る。

| ここのファイル | 写す先 | env を既定の綴りにした行 | 番号を外したコメント |
|---|---|---|---|
| `ccnavi-agree.sh` | `.ccnavi/scripts/ccnavi-agree.sh` | 冒頭の説明の `$CCNAVI_TICKETS_APPROVED（既定 .ccnavi/approved）` を `.ccnavi/approved（固定）` に（説明だけ） | — |
| `ccnavi-common.sh` | `.ccnavi/scripts/ccnavi-common.sh` | `ccnavi_project` の `ccnavi_pj_places=projects`。控えの説明を `logs/state`（固定）に。`ccnavi_state` の本体を `"$1/logs/state"` の 1 行に（`case` を消す）。`ccnavi_parent_tree` の `ccnavi_pt_approved=.ccnavi/approved`・`ccnavi_pt_proposals=wip/proposals` | — |
| `ccnavi-fetch.sh` | `.ccnavi/scripts/ccnavi-fetch.sh` | — | `approved=`・`projects=` の横の `（ADR-0092）` |
| `ccnavi-git.sh` | `.ccnavi/scripts/ccnavi-git.sh` | `store_hit` の `sh_approved=.ccnavi/approved`・`sh_review=wip/proposals/review`。`push-child-worktree` の検査の `push_projects=projects` と、`push_copies`・`push_proposals` を決める `case` 2 つを既定の綴りの 2 行に。親子のチケットの控えの `pr_approved=.ccnavi/approved`・`pr_proposals=wip/proposals` | — |
| `ccnavi-push-approved.sh` | `.ccnavi/scripts/ccnavi-push-approved.sh` | 冒頭の説明から `$CCNAVI_…` を消す。`approved=`・`proposals=`・`projects=` を既定の綴りで直に書く | — |
| `ccnavi-review.sh` | `.ccnavi/scripts/ccnavi-review.sh` | — | `state=` の横の `（ADR-0092）` |
| `ccnavi-sync.sh` | `.ccnavi/scripts/ccnavi-sync.sh` | 実行ファイルが答えないときの予備を `approved=.ccnavi/approved`・`home=.ccnavi`・`projects=projects` に。使われていない予備の `proposals`（代入 3 行）を消す。冒頭の説明を「既定の綴り（固定）」に | — |

直に書いた綴りの横には `# 固定` のコメントが付く。差分に出るのは上の表の箇所だけ。
通らなくなった分岐（綴りが絶対パスのときの `case`、末尾の `/` を落とす行）は、差分を小さく保つために残してある。

7 本とも既にあるファイルの差し替え。新しいファイルは無い。

## 写す前に: 居る場所と、土台と写す版を確かめる

### 1. ブランチが `i0064` であること

親のワークツリー（`.claude/worktrees/i0064`）に `cd` してから打つ。

```sh
git rev-parse --abbrev-ref HEAD
```

`i0064` と出ること。**違えば先へ進まない。**

### 2. 写す先と写す版に、コミットしていない変更が無いこと

```sh
git status --short -- .ccnavi/scripts/ wip/design/scripts/
git diff --cached --name-only
```

どちらも何も出ないこと。

- 1 つ目が何か出せば、下の `ls-tree`（コミットした中身を見る）と作業ツリーの中身が
  食い違うので、照らしても意味が無い
- 2 つ目が何か出せば、ほかにステージ済みのものがある。写す手順はパスを限ってコミットするが、
  混ざっていないことを先に確かめておく

**どちらかが出れば先へ進まない。**

### 3. blob を表と照らす

```sh
git ls-tree HEAD .ccnavi/scripts/ccnavi-agree.sh .ccnavi/scripts/ccnavi-common.sh \
  .ccnavi/scripts/ccnavi-fetch.sh .ccnavi/scripts/ccnavi-git.sh \
  .ccnavi/scripts/ccnavi-push-approved.sh .ccnavi/scripts/ccnavi-review.sh \
  .ccnavi/scripts/ccnavi-sync.sh \
  wip/design/scripts/ccnavi-agree.sh wip/design/scripts/ccnavi-common.sh \
  wip/design/scripts/ccnavi-fetch.sh wip/design/scripts/ccnavi-git.sh \
  wip/design/scripts/ccnavi-push-approved.sh wip/design/scripts/ccnavi-review.sh \
  wip/design/scripts/ccnavi-sync.sh
```

14 行の 3 列目（blob）を下の表と照らす。**1 つでも違えば写さない。**

- 土台（`.ccnavi/scripts/`）が違う: その間に誰かが `.ccnavi/scripts/` を変えている（main の取り込みなど）。写すと、
  その変更を黙って消す。写す版を今の `.ccnavi/scripts/` から作り直してもらう
- 写す版（`wip/design/scripts/`）が違う: 別の写し（`main` にある古い写しなど）を見ているか、
  i0064-10 の取り込みがまだか、取り込んだあとに写す版が変わっている。居る場所と取り込みを確かめる

照らさずに古い写す版（main の取り込みの前に作ったもの）を写すと、main の診断ログと取り込み（sync・C1）の
sh の中身を消す。

| ファイル | 土台（`.ccnavi/scripts/`） | 写す版（`wip/design/scripts/`） |
|---|---|---|
| `ccnavi-agree.sh` | `57171ce432aae00b33dad35b4ecb10986308ea55` | `afd2eea9b40fc0057e894d7a989c8ca47be95a08` |
| `ccnavi-common.sh` | `ae90782b63c2bd142644449561501fff87b52989` | `f7718b8f539764e4dfef6ff50fe970fa5148976d` |
| `ccnavi-fetch.sh` | `8f25961b96a45476109d6135b8c60be5abb4112e` | `6eb146faff82beaf5921bfa24b56b5cf7360fb42` |
| `ccnavi-git.sh` | `c04e8ed4d34f02a37f20849ef29e9086b4e25d87` | `93cae07084b0cec44c4818ba739c67777a4f4178` |
| `ccnavi-push-approved.sh` | `e1a9fa226f2b838c343bab8abe1eb3b0f8387036` | `69aae07177bfd5682e28021c3dc91ce8e4900753` |
| `ccnavi-review.sh` | `9982ac592ed9ebda43d4db48e4480a1076260c9d` | `0dfabbe6ba7149f789bb5a8f2866d979dff2ae31` |
| `ccnavi-sync.sh` | `c059b4bf0f75b9edd6ba0578d302fa79450d3ec2` | `30d7c9227a2e575836d323aa17188ca6812e24d2` |

土台は親のブランチの b761f957（`main` を取り込み直した直後）の `.ccnavi/scripts/`。
写す版との差分は `diff .ccnavi/scripts/<名前> wip/design/scripts/<名前>` で見られ、上の表の箇所だけが出る。

## 写す

**7 本を 1 つのコミットで写す。** どれか 1 本だけ写すと、sh の間で置き場の決まり方が食い違う。
たとえば `ccnavi-git.sh` だけ写すと、子の push の検査は既定の置き場だけを見るのに、
`ccnavi-push-approved.sh` は env が指す置き場を運ぶ。

親のワークツリーで打つ。`&&` で繋いであるので、途中で失敗すればそこで止まる。

```sh
cp wip/design/scripts/ccnavi-agree.sh wip/design/scripts/ccnavi-common.sh \
   wip/design/scripts/ccnavi-fetch.sh wip/design/scripts/ccnavi-git.sh \
   wip/design/scripts/ccnavi-push-approved.sh wip/design/scripts/ccnavi-review.sh \
   wip/design/scripts/ccnavi-sync.sh .ccnavi/scripts/ &&
  git add .ccnavi/scripts/ccnavi-agree.sh .ccnavi/scripts/ccnavi-common.sh \
    .ccnavi/scripts/ccnavi-fetch.sh .ccnavi/scripts/ccnavi-git.sh \
    .ccnavi/scripts/ccnavi-push-approved.sh .ccnavi/scripts/ccnavi-review.sh \
    .ccnavi/scripts/ccnavi-sync.sh &&
  git ls-files -s .ccnavi/scripts/ccnavi-agree.sh .ccnavi/scripts/ccnavi-common.sh \
    .ccnavi/scripts/ccnavi-fetch.sh .ccnavi/scripts/ccnavi-git.sh \
    .ccnavi/scripts/ccnavi-push-approved.sh .ccnavi/scripts/ccnavi-review.sh \
    .ccnavi/scripts/ccnavi-sync.sh &&
  git diff --cached --name-status
```

コミットする前に出力を見る。

- `ls-files -s` の 2 列目（blob）が、上の表の「写す版」と同じ
- `ls-files -s` の 1 列目（モード）が、下の「実行ビット」のとおり
- `diff --cached --name-status` に、写す 7 本の `M` の 7 行だけが出る。ほかのパスが出れば、
  別のステージ済みの変更が混ざっている

どれか違えば、下の「元に戻す」で戻す。合っていればコミットする。コミットはパスを 7 本に限る
（ほかにステージ済みのものがあっても、それはコミットに入らない）。

```sh
git commit -m "fix: sh から置き場の env の読み取りと ADR の番号を外す（i0064-10）" -- \
  .ccnavi/scripts/ccnavi-agree.sh .ccnavi/scripts/ccnavi-common.sh \
  .ccnavi/scripts/ccnavi-fetch.sh .ccnavi/scripts/ccnavi-git.sh \
  .ccnavi/scripts/ccnavi-push-approved.sh .ccnavi/scripts/ccnavi-review.sh \
  .ccnavi/scripts/ccnavi-sync.sh
```

### 実行ビット

7 本とも既にあるファイルなので、実行ビットの付け直しは要らない。`cp` は写す先が既にあれば
そのモードを残すので、`ccnavi-agree.sh`・`ccnavi-fetch.sh`・`ccnavi-push-approved.sh` は 100755、
残りは 100644 のまま残る（ここの写す版はどれも 100644 だが、写す先のモードが残る）。

## 写したあとに確かめる

親のワークツリーで打つ。

```sh
uv run python -m unittest discover -s tests -t .
grep -rnE 'CCNAVI_(PROJECTS|PROJECT_HOME|TICKETS_PROPOSAL|TICKETS_APPROVED|LOG|STATE)\b' .ccnavi/scripts/
grep -rnE 'ADR-0084|ADR-0092|ADR-0098' .ccnavi/scripts/
sh .ccnavi/scripts/ccnavi-git.sh status
sh .ccnavi/scripts/ccnavi-review.sh --help
sh .ccnavi/scripts/ccnavi-sync.sh --help
```

- 全件が通る。置き場の env を読まないことを見る A9（`tests.sh.test_gitwrap` の
  `PlacesAreNotReadTest` と `test_the_store_does_not_move_with_the_environment`、
  `tests.core.test_review_origin` の `test_the_state_variable_is_not_read`、
  `tests.sh.test_push_approved_sh` の `test_does_not_read_the_place_variables`、
  `tests.sh.test_sync_sh` と `tests.sh.test_c1_sh` の `PlacesAreNotReadTest`）は、写す前は落ち、ここで通る
  - 注: git が 2.44 より古い機械では、`tests.sh.test_gitwrap.MergeFileTest` の 1 件が写す前から落ちる
    （その git は `merge-file --object-id` を知らない）。写したことによる失敗ではない
  - 注: Windows では main から来た `tests.sh.test_diaglog_sh` の 3 件が写す前から落ちる。写したことによる失敗ではない
  - 注: i0064-06（docs）の前なら `tests.core.test_adr_numbers.test_numbers_are_unique` の 1 件が落ちる
  - それ以外が通っていればよい
- `grep` は 2 つとも何も返さない
- `ccnavi-git.sh status`（ワークツリーの中で）と `ccnavi-review.sh --help`・`ccnavi-sync.sh --help` が今どおり動き、
  `logs/diag/` に診断ログが書かれる

写す前に写す版だけを確かめるなら、`CCNAVI_SH_DIR` で sh の出どころを差し替える
（写す版に無い sh も読むテスト、`tests.sh.test_clean_sh`・`tests.sh.test_bin_lookup`・
`tests.e2e.test_e2e_sh` はこれでは回さない）。ここでも古い git では上の MergeFileTest の 1 件が落ちる。
Git Bash では `MSYS2_ARG_CONV_EXCL='*'` を前に付ける（パスの書き換えを止める）。

```sh
CCNAVI_SH_DIR=wip/design/scripts uv run python -m unittest \
  tests.sh.test_gitwrap tests.core.test_review_origin tests.sh.test_review_decide \
  tests.sh.test_push_approved_sh tests.sh.test_fetch_sh tests.sh.test_sync_sh tests.sh.test_c1_sh \
  tests.guard.test_wrapguard tests.core.test_sync_paths
```

## 元に戻す

コミットする前（7 本だけを HEAD に戻す。`.ccnavi/scripts/` の他のファイルには触らない）:

```sh
git restore --staged --worktree .ccnavi/scripts/ccnavi-agree.sh .ccnavi/scripts/ccnavi-common.sh \
  .ccnavi/scripts/ccnavi-fetch.sh .ccnavi/scripts/ccnavi-git.sh \
  .ccnavi/scripts/ccnavi-push-approved.sh .ccnavi/scripts/ccnavi-review.sh \
  .ccnavi/scripts/ccnavi-sync.sh
```

コミットしたあとは、写したコミットを `git revert` する。戻すときも 7 本を一緒に戻す。
（`revert` だけは `ccnavi-git.sh` が通さない。人が打つ。）
