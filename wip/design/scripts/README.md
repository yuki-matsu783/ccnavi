# 写す sh（i0064-04 置き場の固定）

`.ccnavi/scripts/` はエージェントが書けない場所なので、完成品をここに置き、人が写す。
設計は `wip/design/i0064-fixed-places.md` §3、チケットは `.ccnavi/approved/doing/i0064-04.md`。

**この手順は人が端末で打つ。** エージェントは `.ccnavi/scripts/` に書けず、git も `ccnavi-git.sh` を
通すので、ここに書いた `cp` や生の `git` をエージェントがそのまま実行しない。
ここに書いた `git` はどれも `ccnavi-git.sh` が通す形にしてある（`git` を
`sh .ccnavi/scripts/ccnavi-git.sh` に置き換えても通る）。`hash-object` は通らないので、
blob は `ls-tree` と `ls-files -s` で照らす。

## いつ・どこで打つか

**i0064-04 を親 i0064 に取り込んだあと、親のワークツリー（`.claude/worktrees/i0064`、
ブランチ `i0064`）で打つ。** 子のワークツリー（`.claude/worktrees/i0064-04`）でも、
ワークスペースルート（`main`）でもない。

- 子のワークツリーで写すと、写したものは子のブランチに載り、親の取り込みを待つ間に
  親の `.ccnavi/scripts/` と食い違う
- ワークスペースルートで写すと `main` に直に入る。しかも `main` にある `wip/design/scripts/` は
  別の古い写し（たとえば古い `ccnavi-git.sh`）なので、それを写すことになる

統合先へは親のブランチごと入る。

| ここのファイル | 写す先 | 変えたところ（他は土台と同じ） |
|---|---|---|
| `ccnavi-common.sh` | `.ccnavi/scripts/ccnavi-common.sh` | `ccnavi_project` の `ccnavi_pj_places=projects` |
| `ccnavi-fetch.sh` | `.ccnavi/scripts/ccnavi-fetch.sh` | `approved=.ccnavi/approved`・`projects=projects` |
| `ccnavi-git.sh` | `.ccnavi/scripts/ccnavi-git.sh` | 子の push の検査で置き場を直に書き、絶対パスの分岐（`case` 3 つ）を消す |
| `ccnavi-push-approved.sh` | `.ccnavi/scripts/ccnavi-push-approved.sh` | 置き場 3 つを直に書き、綴りの正規化（`%/` 3 行と `case` 3 つ）を消す。冒頭の説明から `$CCNAVI_…` を消す |
| `ccnavi-review.sh` | `.ccnavi/scripts/ccnavi-review.sh` | `state="$root/logs/state"` |
| `ccnavi-approve.sh` | `.ccnavi/scripts/ccnavi-approve.sh` | 冒頭の説明だけ（`.ccnavi/approved/doing/`） |

直に書いた綴りの横には `# 固定（ADR-0092）` のコメントが付く。差分に出るのは上の表の箇所と、
この ADR 番号のコメントだけ。

6 本とも既にあるファイルの差し替え。新しいファイルは無い。

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
```

何も出ないこと。何か出れば、下の `ls-tree`（コミットした中身を見る）と作業ツリーの中身が
食い違うので、照らしても意味が無い。**出れば先へ進まない。**

### 3. blob を表と照らす

```sh
git ls-tree HEAD .ccnavi/scripts/ccnavi-common.sh .ccnavi/scripts/ccnavi-fetch.sh \
  .ccnavi/scripts/ccnavi-git.sh .ccnavi/scripts/ccnavi-push-approved.sh \
  .ccnavi/scripts/ccnavi-review.sh .ccnavi/scripts/ccnavi-approve.sh \
  wip/design/scripts/ccnavi-common.sh wip/design/scripts/ccnavi-fetch.sh \
  wip/design/scripts/ccnavi-git.sh wip/design/scripts/ccnavi-push-approved.sh \
  wip/design/scripts/ccnavi-review.sh wip/design/scripts/ccnavi-approve.sh
```

12 行の 3 列目（blob）を下の表と照らす。**1 つでも違えば写さない。**

- 土台（`.ccnavi/scripts/`）が違う: その間に誰かが `.ccnavi/scripts/` を変えている。写すと、
  その変更を黙って消す。写す版を作り直してもらう
- 写す版（`wip/design/scripts/`）が違う: 別の写し（`main` にある古い写しなど）を見ているか、
  i0064-04 の取り込みがまだか、取り込んだあとに写す版が変わっている。居る場所と取り込みを確かめる

| ファイル | 土台（`.ccnavi/scripts/`） | 写す版（`wip/design/scripts/`） |
|---|---|---|
| `ccnavi-common.sh` | `d21cb5e8133db908987a132a8d221e80583683c1` | `f619f70c4347ebdda071b828912a5fc82cdbb3ca` |
| `ccnavi-fetch.sh` | `f7f3b9944382aa1b3f9ac1f3a1fe11e903158060` | `306d540630d245f9532ae8ac2b5d7504858bf1f8` |
| `ccnavi-git.sh` | `9bc2a7982976c82587ca0ebbef1b75135ff1084a` | `53616f35ad39febcf90f88e708d10b786ebb5598` |
| `ccnavi-push-approved.sh` | `097b9fc03ace078c95665cd5d8387f2ba5e67266` | `ac435ffe1cacde83cecc15ce00c06db5cbf3b8d0` |
| `ccnavi-review.sh` | `91635fc85e96240bb58ab8c717975f3b16b2ccb8` | `276641f0659f72fe40a63aea9f28595d19a9ce32` |
| `ccnavi-approve.sh` | `c6eb5af8b2ba5fda4ef2088a92370bad7e7d9d28` | `1d1487da646693672a3812c565d6ff8de0676d81` |

土台は親のブランチの 9f33635（`main` を取り込んだ直後）の `.ccnavi/scripts/`。
写す版との差分は `diff .ccnavi/scripts/<名前> wip/design/scripts/<名前>` で見られ、上の表の箇所と
ADR 番号のコメントだけが出る。

## 写す

**6 本を 1 つのコミットで写す。** どれか 1 本だけ写すと、sh の間で置き場の決まり方が食い違う。
たとえば `ccnavi-git.sh` だけ写すと、子の push の検査は既定の置き場だけを見るのに、
`ccnavi-push-approved.sh` は env が指す置き場を運ぶ。

親のワークツリーで打つ。`&&` で繋いであるので、途中で失敗すればそこで止まる。

```sh
cp wip/design/scripts/ccnavi-common.sh wip/design/scripts/ccnavi-fetch.sh \
   wip/design/scripts/ccnavi-git.sh wip/design/scripts/ccnavi-push-approved.sh \
   wip/design/scripts/ccnavi-review.sh wip/design/scripts/ccnavi-approve.sh .ccnavi/scripts/ &&
  git add .ccnavi/scripts/ccnavi-common.sh .ccnavi/scripts/ccnavi-fetch.sh \
    .ccnavi/scripts/ccnavi-git.sh .ccnavi/scripts/ccnavi-push-approved.sh \
    .ccnavi/scripts/ccnavi-review.sh .ccnavi/scripts/ccnavi-approve.sh &&
  git ls-files -s .ccnavi/scripts/ccnavi-common.sh .ccnavi/scripts/ccnavi-fetch.sh \
    .ccnavi/scripts/ccnavi-git.sh .ccnavi/scripts/ccnavi-push-approved.sh \
    .ccnavi/scripts/ccnavi-review.sh .ccnavi/scripts/ccnavi-approve.sh &&
  git diff --cached --summary
```

コミットする前に出力を見る。

- `ls-files -s` の 2 列目（blob）が、上の表の「写す版」と同じ
- `ls-files -s` の 1 列目（モード）が、下の「実行ビット」のとおり
- `diff --cached --summary` に `mode change` が出ない（`cp` は写す先のモードを残す）

どれか違えば、下の「元に戻す」で戻す。合っていればコミットする。

```sh
git commit -m "feat: 置き場の env を読まない sh を写す（i0064-04）"
```

### 実行ビット

6 本とも既にあるファイルなので、実行ビットの付け直しは要らない。`cp` は写す先が既にあれば
そのモードを残すので、`ccnavi-approve.sh`・`ccnavi-fetch.sh`・`ccnavi-push-approved.sh` は 100755、
残りは 100644 のまま残る。

## 写したあとに確かめる

親のワークツリーで打つ。

```sh
uv run python -m unittest discover -s tests -t .
grep -rnE 'CCNAVI_(PROJECTS|PROJECT_HOME|TICKETS_PROPOSAL|TICKETS_APPROVED|LOG|STATE)\b' .ccnavi/scripts/
sh .ccnavi/scripts/ccnavi-git.sh status
sh .ccnavi/scripts/ccnavi-review.sh --help
```

- 全件が通る。置き場の env を読まないことを見る A9 の 3 本（`tests.sh.test_gitwrap` の
  `PlacesAreNotReadTest`、`tests.core.test_review_origin` の `test_the_state_variable_is_not_read`、
  `tests.sh.test_push_approved_sh` の `test_does_not_read_the_place_variables`）は、写す前は落ち、ここで通る
  - 注: git が 2.44 より古い機械では、`tests.sh.test_gitwrap.MergeFileTest` の 1 件が写す前から落ちる
    （その git は `merge-file --object-id` を知らない）。写したことによる失敗ではない。
    それ以外が通っていればよい
- `grep` は何も返さない
- `ccnavi-git.sh status`（ワークツリーの中で）と `ccnavi-review.sh --help` が今どおり動く

写す前に写す版だけを確かめるなら、`CCNAVI_SH_DIR` で sh の出どころを差し替える
（写す版に無い sh も読むテスト、`tests.sh.test_clean_sh`・`tests.sh.test_bin_lookup`・
`tests.e2e.test_e2e_sh` はこれでは回さない）。ここでも古い git では上の MergeFileTest の 1 件が落ちる。

```sh
CCNAVI_SH_DIR=wip/design/scripts uv run python -m unittest \
  tests.sh.test_gitwrap tests.core.test_review_origin tests.sh.test_review_decide \
  tests.sh.test_push_approved_sh tests.sh.test_fetch_sh tests.guard.test_wrapguard
```

## 元に戻す

コミットする前（6 本だけを HEAD に戻す。`.ccnavi/scripts/` の他のファイルには触らない）:

```sh
git restore --staged --worktree .ccnavi/scripts/ccnavi-common.sh .ccnavi/scripts/ccnavi-fetch.sh \
  .ccnavi/scripts/ccnavi-git.sh .ccnavi/scripts/ccnavi-push-approved.sh \
  .ccnavi/scripts/ccnavi-review.sh .ccnavi/scripts/ccnavi-approve.sh
```

コミットしたあとは、写したコミットを `git revert` する。戻すときも 6 本を一緒に戻す。
（`revert` だけは `ccnavi-git.sh` が通さない。人が打つ。）
