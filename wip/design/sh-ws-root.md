# 設計: 保護済み sh がプロジェクトの中で動くようにする

親チケット `sh-ws-root`、フェーズ 1（設計）の成果物。設計 §25.8（保護済みスクリプトと案内）の
実装にあたる部分を書く。`ccnavi.md` へ写す作業は**このチケットではしない**（9 節）。

## 0. 何を解くか

設計 §25.8 は「sh はワークスペースにしかない」と書いている。ところが 3 本の sh は、自分が
使う根を `git rev-parse --show-toplevel` か `--git-common-dir` から導いている。

| スクリプト | 今の導出 | モード A | モード B |
|---|---|---|---|
| `ccnavi-git.sh:465` | `--show-toplevel` | ワークスペース | プロジェクト、または作業ツリー |
| `ccnavi-git.sh:381` | `--git-common-dir` の親 | ワークスペース | プロジェクト |
| `ccnavi-ticket.sh:52-58` | `--git-common-dir` の親 | ワークスペース | プロジェクト |
| `ccnavi-review.sh:74-82` | `--git-common-dir` の親 | ワークスペース | プロジェクト |

モード A では git のトップとワークスペースルートが一致するので、今まで誰も困らなかった。
モード B では一致しない。`cwd` がプロジェクトの中にあれば、git はプロジェクトを答える。
それが正しい git の答えであって、sh が欲しかったのは git の答えではなく**道具の置き場**だった、
というのが今回の誤りの本体。

**骨: git に聞くのをやめる。** ワークスペースルートはファイルシステムを上へ歩いて探す。
git のトップは git の用途（`status`、`diff`、`push`）にだけ使う。この 2 つを別の変数にする。

## 1. `ccnavi-common.sh`

3 本が `.` で読む共通部分。新設。置き場は `.claude/scripts/ccnavi-common.sh`。

```sh
# 呼ぶ側の綴り（3 本の先頭、set -eu の直後）
. "$(dirname "$0")/ccnavi-common.sh"
```

`$0` は呼ばれたときの綴りそのままなので、`sh .claude/scripts/ccnavi-git.sh` でも
`sh ../../scripts/ccnavi-git.sh` でも、`dirname` は同じディレクトリを指す。**共通部分の
読み込みにだけ `$0` を使い、ワークスペースルートの決定には使わない。** 理由は 2 節。

提供するもの。

| 名前 | 何 |
|---|---|
| `ccnavi_workspace` | ワークスペースルートの絶対パスを標準出力に出す。見つからなければ非ゼロ |
| `ccnavi_project` | 引数のディレクトリが属するプロジェクトの名前を出す。ワークスペース自身なら空 |
| `ccnavi_mask_url` | 引数の URL の資格情報を伏せて出す |
| `ccnavi_abs` | 相対パスを絶対に直す。`realpath` と `readlink -f` は使わない（3 環境で綴りが揃わない） |

失敗したときの綴りは呼ぶ側に任せる（`reject` と `fail` の文面がスクリプトごとに違うため）。
共通部分は標準出力と終了コードだけを返し、標準エラーには何も書かない。

## 2. ワークスペースルートの探し方

```
CCNAVI_WORKSPACE があれば、それを絶対に直して使う。
  そこに .claude/scripts/ が無ければ失敗（指し先の誤りを黙って受けない）
無ければ、cwd から親へ 1 段ずつ登り、.claude/scripts/ を持つ最初のディレクトリを返す。
  ファイルシステムの根まで登って見つからなければ失敗。
```

**印を `.claude/scripts/` にする理由。** `.git` は駄目（プロジェクトも持つ）。`.claude/` だけも
駄目（Claude Code が作る場合があり、プロジェクト側にできた `.claude/` に当たる）。
`.claude/ccnavi/` は配布先によっては置き場が動く（`CCNAVI_APPROVED` 等）。
`.claude/scripts/` は「この sh 自身が居る場所」で、居なければ sh が呼べていない。

**`$0` から導かない理由。** `dirname "$0"/..` の 1 段上がワークスペースルートだ、というのは
配布のレイアウトに寄りかかる。作業ツリーの中にも `.claude/scripts/` が checkout されうる
（ワークスペースの git が追跡しているため）ので、`$0` が作業ツリーの中の写しを指していると
1 段上は作業ツリーになる。

**決定（2026-09-12、利用者）: 最初に当たったものを根とする。** 作業ツリーの中に
`.claude/scripts/` が checkout されている場合、上へ歩くと作業ツリーが先に当たる。これを
**意図した挙動**とする。作業ツリーの中で `sh ../../scripts/...` と打った人はワークスペースの
写しを呼んでおり、`sh .claude/scripts/...` と打った人はその作業ツリーの写しを呼んでいる。
どちらも「自分が呼んだ sh の隣の根」で一貫し、記録も承認済みの写しも呼ばれた側の根に付く。

代償: 作業ツリーの中で `.claude/scripts/` を編集した状態（このチケット自身がそうなる）だと、
その作業ツリーの中から打った sh は作業ツリーを根と見る。記録が作業ツリーの `logs/` に出て、
承認済みの写しも作業ツリーの `.claude/ccnavi/tickets/` を見る。**写す前の検証では、
ワークスペースルートから、またはワークスペースの写しを名指しで呼ぶ**。7 節の受入テストは
隔離したワークスペースを別に組み立てるので、この影響を受けない。

**`cd` を使わない。** `cd` を挟むと `set -e` の下で戻り忘れが事故になる。パス文字列を
削っていく形で登る。

## 3. プロジェクトの名前の決め方

`ccnavi_project <ディレクトリ>` は、ワークスペースルートからの相対を見て決める。

```
<ws>/projects/<名前>/...          -> <名前>
<ws>/.claude/worktrees/<id>/...   -> その作業ツリーの切り元をたどる（下記）
それ以外                           -> 空（ワークスペース自身）
```

置き場の名前は `CCNAVI_PROJECTS`（既定 `projects`）を見る。

作業ツリーの切り元は `.git` ファイルから取る。実測（2026-09-12、git 2.39.2.windows.1、
Git Bash と PowerShell の両方）で確定した綴り。

```
gitdir: C:/Users/.../ws/projects/p1/.git/worktrees/w1
```

- 絶対パス。`git worktree add` に相対を渡しても絶対で書かれる
- 区切りは `/` のみ。バックスラッシュは出ない
- ドライブレターは大文字
- 行末は `\n` 1 個。`gitdir:` の後ろは半角空白 1 個

したがって、`gitdir:` の値から `/.git/worktrees/<id>` を末尾から削れば切り元の git プロジェクト
ルートが出る。その basename が `<ws>/projects/` の直下にあれば名前、無ければ空。

**取れなかったときは空にする。** `.git` が読めない、`gitdir:` が無い、切り元が消えている
（孤児）のいずれでも空を返し、記録は `logs/` に落ちる。止めない。記録の置き場のために
作業を止めるのは釣り合わない。

**代償**: `gitdir:` の解析が sh と実行ファイル（`tree.py`）の 2 か所に載る。綴りが変わったら
2 か所直す。実測で綴りが安定していること、実行ファイル側は判定に使い sh 側は記録の置き場に
しか使わない（誤っても判定が緩まない）ことから、共通化はしない。

## 4. 各スクリプトの変更

### 4.1 `ccnavi-git.sh`

**記録の置き場**（`:465-468`）。`root` を 2 つに分ける。

```sh
ws=$(ccnavi_workspace) || reject "ワークスペースルートが見つかりません。..."
gitroot=$(git rev-parse --show-toplevel 2>/dev/null || :)   # git の用途にだけ使う
[ -z "$gitroot" ] && reject "git リポジトリの中で実行してください。"
proj=$(ccnavi_project "$(pwd)")
if [ -n "$proj" ]; then logdir="$ws/logs/$proj"; else logdir="$ws/logs"; fi
```

返す記録のパスは、これまで `$root` からの相対 1 本だった。**`$ws` からの相対と絶対の両方を
出す。** モード B ではエージェントの `cwd` がプロジェクトなので、相対だけでは届かない。

**push ガード**（`:381-388`）。`push_root` を `$ws` に差し替える。作業ツリーは
`$ws/.claude/worktrees/*` にあるので、これで一致する。承認済みの写しの置き場も
`$ws/${CCNAVI_APPROVED:-.claude/ccnavi/tickets}` にする。検査の中身は変えない。

**`worktree add` の行き先**（`:197-208`）。`add` のときだけ、行き先を検査する。

```
add の引数を順に読む。
  値を取るオプション（-b -B --reason）は、そのオプションと値の 2 語を飛ばす。
  値を取らない既知のオプション（--detach --force -f --checkout --no-checkout
    --lock --guess-remote --no-guess-remote --track --no-track --quiet -q）は 1 語飛ばす。
  知らないオプション（- で始まる語）が来たら reject する。
  最初の非オプションの語を行き先とする。
行き先を cwd 基準で絶対に直し、$ws/.claude/worktrees/<1 段> でなければ reject。
```

**決定（利用者）: 知らないオプションは止める。** 通すと行き先を取り違え、検査そのものが
意味を失う。git の新しいオプションが使えなくなるのは代償として受け入れる。今の
ホワイトリストの方針（知らないものは通さない）と揃う。使いたいオプションが出たら、
一覧に足す変更を出す。

文面は、正しい綴りを `cwd` に合わせて出す。`cwd` がプロジェクトなら
`../../.claude/worktrees/<名前>`、ワークスペースルートなら `.claude/worktrees/<名前>`。
絶対パスでも通る。

検査の範囲は `add` だけ。`remove` と `prune` は既存のツリーを指すので行き先の検査は要らない。

### 4.2 `ccnavi-ticket.sh`

`root`（`:52-58`）を `ccnavi_workspace` に差し替える。実行ファイルの置き場（`:66`）と
`--root` に渡す値がワークスペースになる。`wip/<プロジェクト>/tickets/` と `wip/tickets/` の
振り分けは実行ファイルが `--root` から決めるので、sh 側の変更はこれだけ。

### 4.3 `ccnavi-review.sh`

`root`（`:74-82`）を同じく差し替える。`state`（`:426`）がワークスペースの
`.claude/ccnavi/state/` になり、プロジェクトに `.claude/` を作らなくなる。

`origin` は **`cwd` の git から読む**（`:102`）。これは変えない。レビューはプロジェクトの
リモートに結ぶので、プロジェクトの中で打つのが正しい。設計 §25.8 の運用（プロジェクトの
作業はプロジェクトに入ってから始める）がここで効く。

**資格情報の伏せ字**。`ccnavi_mask_url` を共通部分に置き、`origin` を出力しうる全箇所に通す。

```sh
ccnavi_mask_url() {
	printf '%s' "$1" | sed -E 's#^([A-Za-z][A-Za-z0-9+.-]*://)[^/]*@#\1<伏せた>@#'
}
```

今の綴り（`:434`）との差は 2 つ。`[^/@]+@` を `[^/]*@` にして**最後の `@` まで**消す
（解析側が `${authority##*@}` と最後まで見ているので、伏せ字も合わせる）。
`[a-z]+` を `[A-Za-z][A-Za-z0-9+.-]*` にして大文字の scheme と `git+ssh` の形を拾う。

`origin` を読めなかったときの `fail`（`:114`、`:132`、`:142`）は、**`origin` を読む直前に
伏せた綴りを 1 度作り、`fail` にはそれだけ渡す**。生の `$origin` を文面に入れる綴りを残さない。

```sh
origin=$(git remote get-url origin 2>/dev/null || :)
[ -z "$origin" ] && fail "origin が無い。..."
origin_shown=$(ccnavi_mask_url "$origin")
# 以降、文面に使うのは $origin_shown だけ
```

なお `:113` の `sed -E 's#^(https?://|git@|ssh://git@)##'` は
`ssh://oauth2:<token>@host` の形に当たらないため、`rest` が `origin` と等しくなって
`:114` の `fail` に落ちる。ここが今いちばん漏れやすい経路で、伏せ字を先に作れば塞がる。

**ブランチ名のスラッシュ**（`:575-576`）。ファイル名を組み立てる前に `/` を `-` に置換する。
置換後に同名が衝突しうるが、その 2 本は `$$` を含むので実際には当たらない。

### 4.4 `.claude/hooks/test-py.sh`

`:67-68` の `[ -d "$target" ] || continue` を `[ -d "$target/tests" ] || continue` にする。
テストを持つツリーの扱いは変わらない。

### 4.5 `.claude/ccnavi/rules.yml`

`message` の 7 か所（`:36-37`、`:53`、`:62-63`、`:70`、`:86`、`:112` 付近）の
`sh .claude/scripts/...` を `sh {root}/.claude/scripts/...` にする。`{root}` は
ルールを読むときにワークスペースルートの絶対パスへ置き換わる（`rules.load`）。

### 4.6 `scripts/ccnavi-setup.sh`

`:80` の `DEPLOY_SCRIPTS` と `:947` の点検の一覧に `ccnavi-common.sh` を足す。足さないと、
配った先で 3 本が読めないファイルを `.` しようとして起動時に落ちる。

**決定（利用者）: これも人が写す側に回す。** 親チケットの `allow` に `scripts/*` が無いため。
チケットを出し直すより往復が少ない。

## 5. 自己防衛

変更は要らない（確認済み）。`guard-scripts` は `*/.claude/scripts/*` のグロブ、selfguard の
`_PLACES` は `\.claude[\\/]((ccnavi|hooks|scripts)[\\/]|...)` なので、`ccnavi-common.sh` は
置いた時点で守られ、控えと復元の対象にも入る。

## 6. 人が写す手順

保護済みファイルはチケットの承認でも書けない（`judge.py:246-247`「チケットはルールが
何も言わなかったときだけ見る。ルールのほうが強い」）。ガードは緩めず、人が写す。

実装フェーズの成果物として `wip/design/scripts/` に置くもの。

```
wip/design/scripts/ccnavi-common.sh      新設
wip/design/scripts/ccnavi-git.sh         全文
wip/design/scripts/ccnavi-ticket.sh      全文
wip/design/scripts/ccnavi-review.sh      全文
wip/design/scripts/test-py.sh            全文
wip/design/scripts/rules.yml             全文
wip/design/scripts/ccnavi-setup.sh       全文
wip/design/scripts/COPY.md               写す手順と、写す前後で確かめること
```

差分ではなく全文にする。人が手で当てる工程を無くすため。`COPY.md` には `cp` の並びと、
写す前に `diff` で見る綴り、写したあとに回す受入テストの打ち方を書く。

## 7. 確かめ方

### 7.1 既存

in-process の unittest 406 件が通ること。sh の変更なので直接は掛からないが、`rules.yml` の
`message` 変更は `tests/test_root_placeholder.py` と見本の検査に掛かる。

### 7.2 新設（`CCNAVI_E2E=1` のときだけ走る）

`tests/test_e2e_sh.py`。環境変数が無ければ `skipUnless` で飛ばす。隔離した一時ディレクトリに
本物のワークスペースを組み立て、**組み立てた実行ファイルと写した sh を実際に叩く**。
2 節の代償を避けるため、このワークスペースはこのリポジトリとは別に組み立てる。

```
ws/                         git init、1 コミット、.claude/scripts/ に 4 本、dist/ に実行ファイル
ws/projects/p1              git init、1 コミット
ws/projects/p2              git init、1 コミット
ws/.claude/worktrees/wp1    p1 から切る
ws/.claude/worktrees/w0     ws から切る
```

| # | 何 | 期待 |
|---|---|---|
| 1 | `p1` の中で `ccnavi-git.sh status` | 記録が `ws/logs/p1/`。`p1/logs/` ができない |
| 2 | `wp1` の中で同じ | 記録が `ws/logs/p1/`（切り元の名前） |
| 3 | `w0` の中で同じ | 記録が `ws/logs/` |
| 4 | `ws` で同じ（モード A 相当） | 記録が `ws/logs/`。`projects/` を消した状態でも同じ |
| 5 | `p1` の中で `ccnavi-ticket.sh` を引数無し | 実行ファイルが見つかる（使い方が出る） |
| 6 | `p1` の中で `ccnavi-review.sh origin` | `p1/.claude/` ができない |
| 7 | 子の写しを置いた `wp1` で `push` | 拒否される（親の名前が文面に出る） |
| 8 | `p1` の中で `worktree add .claude/worktrees/x` | 拒否。文面に `../../.claude/worktrees/x` が出る |
| 9 | `p1` の中で `worktree add ../../.claude/worktrees/x -b x` | 通る。`ws/.claude/worktrees/x` ができる |
| 10 | `worktree add --unknown-opt ../../.claude/worktrees/x` | 拒否される |
| 11 | `origin` を `ssh://oauth2:glpat-A@B@h/g/p.git` にして `ccnavi-review.sh origin` | 出力にも stderr にもトークンが出ない |
| 12 | `origin` を読めない綴りにして同じ | 同上（`fail` の文面） |
| 13 | `CCNAVI_WORKSPACE` を別の場所に向けて `ccnavi-git.sh status` | その場所の `logs/` に出る。`.claude/scripts/` が無ければ失敗 |
| 14 | `ws` の外で `ccnavi-git.sh status` | 失敗する。文面に `CCNAVI_WORKSPACE` が出る |
| 15 | `tests/` を持たないツリーで `test-py.sh` | 素通りする（ターンが止まらない） |
| 16 | `feature/x` ブランチで `ccnavi-review.sh` の記録を作る | 存在するパスに落ちる |

11 と 12 は、**出力の全文にトークンの断片（`glpat-A`、`B`）が含まれないこと**を突き合わせる。
伏せ字が「消えているつもりで残っている」形を捕まえるため、伏せた結果を目視の綴りで比べるのでは
なく、元のトークンの部分文字列で探す。

### 7.3 モード A の非退行

4 と、`projects/` を作らないワークスペースでの 1〜3 相当。置き場も文面も §25 の前と同じで
あることを確かめる。

## 8. 失敗したときの見え方

ルート探索が失敗したときの文面は 3 本で揃える。

```
ccnavi-git: ワークスペースルートが見つかりません（.claude/scripts/ を持つ親を cwd から
上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペース
ルートの絶対パスを渡してください。
```

「何を探したか」を書く。書かないと、受け取ったエージェントが `cd` を繰り返して試す。

## 9. 今回入れないもの

- `ccnavi.md` §25 と `requirements.md` の REQ-MLT 表への追記。進行中の `config-union` が §25 を
  構造ごと改版するため。`HANDOVER.md` に未了として書く
- Python 側（識別子の接頭辞、プロジェクト名の空白で自己防衛が抜ける穴、プロジェクトから切った
  作業ツリーの控え、lint の早期 return、孤児の作業ツリーの lint、`message` の `{root}` の lint）
- `worktree add` の行き先の**書き換え**（止めるだけにする）
- push の前にプロジェクトの一致を確かめること（`ticket start` と書き込みの判定に既にある）
- ガードの設計を変えて「承認済みチケットなら保護済みスクリプトも書ける」形にすること

## 10. 決めたこと（レビューの記録）

2026-09-12、利用者の判断。

1. ルートの印は `.claude/scripts/`。上へ歩いて**最初に当たったもの**を根とする。作業ツリーの
   中に写しがあれば、その作業ツリーが根になる
2. `worktree add` の引数解析で、**知らないオプションは止める**
3. `scripts/ccnavi-setup.sh` の変更も**人が写す**側に回す。チケットは出し直さない
