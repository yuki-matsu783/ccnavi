---
title: 導入と配布
type: guide
description: ccnavi を dry-run で入れ、記録を読んでルールを直し、enable に切り替えるまでの手順。導入スクリプトの打ち方と、実行ファイルとルールの配り方、VS Code からワークツリーを見る設定
tags: [user-guide, install, dry-run]
keywords: [導入, dry-run, enable, ccnavi-setup.sh, 配布, 実行ファイル, launcher, --force, --check, --no-vscode, --no-fetch, --no-idle, .gitignore, ワークツリー, VS Code, 記録, jq]
---

# 導入と配布

どんなコマンドが打たれ、ルールが何にヒットするかは、動かしてみるまで分からない。`dry-run` で導入し、記録を読みながらルールを直し、記録が落ち着いてから `enable` に切り替える。
このページは、その手順と、導入スクリプトで配布先に設定と道具を置く方法を書く。入口は[手引きの目次](index.md)にある。

## どこで打つか

`sh scripts/ccnavi-setup.sh` のように `scripts/` から始まる sh（`ccnavi-setup.sh`・`ccnavi-install.sh`・`ccnavi-doctor.sh`・`ccnavi-package.sh`・`ccnavi-stop-review.sh`）は、**ccnavi の根**で打つ。ccnavi の根は、配布物の zip を展開したフォルダか、clone した ccnavi のリポジトリである。入れた先（配布先）の `.ccnavi/scripts/` には、これらの sh は入らない。`.ccnavi/scripts/` の sh（`ccnavi-git.sh` など）は、配布先のワークスペースルートで打つ（[配布するスクリプト](scripts.md)）。

以降の例の `<ワークスペースルート>` は入れる先のフォルダで、`ccnavi --lint` などの `ccnavi` は配布先で打つ。

## 入口: Claude Code に「ccnavi を入れて」と頼む

配る側が ccnavi のリポジトリで `sh scripts/ccnavi-package.sh --build` で zip（と `.sha256`）を作り、共有フォルダに置く。使う側は zip を
受け取ってチェックサムを確かめ、自分で展開し、展開したフォルダで `claude` を起動して「ccnavi を入れて」と頼む
（`ccnavi-install` スキル）。スキルは `scripts/ccnavi-doctor.sh` で OS・git・jq・curl・GitLab の認証・既存の導入を調べ、
足りないものは入れ方を案内してから、`scripts/ccnavi-install.sh <入れる先> --check` を打ち、続けて本番を打つ。glab は必須ではなく、
無くても git の資格情報と `GITLAB_TOKEN` で足りる。

ソースから使うときは `--build` を付けて実行ファイルを組み立てる（`--build --check` で副作用を見てから）。
更新は `--force` で行い、バックアップは入れる先の外に作られる。手順は展開した zip の `INSTALL.md` にある。
下の 1 以降は、入ったあとの進め方を説明する。

## 1. dry-run で立てる

実行ファイルを組み立て、8 つのイベントに登録する（[設定](settings.md)。導入スクリプトが書く）。
判定だけを見るなら `PreToolUse` と `PostToolUse` の 2 つでも動く（残りを登録しないと何が欠けるかは[設定](settings.md)の表）。
`CCNAVI_MODE` は `dry-run` にする。

```json
"env": {
  "CCNAVI_MODE": "dry-run"
}
```

ルールファイルには、取り返しの付かない操作とガード自身の設定だけを `deny` に書いて始める
（`rm -rf`、`git push`、`git reset --hard`、認証情報の置き場、`.claude/` の下）。組み込みの既定
（[ルールファイルが読めないとき](records.md)）がちょうどその範囲なので、コピーして直すのが早い。

`allow` は空のままでよい。`dry-run` は一般の保護には手を出さないので作業は止まらず、ルールの抜けている箇所だけが記録に溜まる（チケット制御が有効なら、チケットの関門と補助の保護は `dry-run` でも止める）。
モードごとの違いは[設定](settings.md)の「動作モード」にある。

導入したら、設定そのものを確かめる。

```sh
ccnavi --lint
```

`dry-run` の間は、モードが `dry-run` であることを知らせる `warn` が出続ける。Claude Code のセッションの中で打つと 1 件。`env` を渡さないターミナルで `CCNAVI_MODE=dry-run` だけを付けて打つと、戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY`・`CCNAVI_GUARD_CORE_FILES`）の `warn` も出て、3 件になる。終了コードは 0 のまま。

`env` の変更はセッションを開き直すまで反映されない。

## 2. 普段どおり開発する

ccnavi は判定し、呼び出しには手を出さず「`enable` なら何をしていたか」を返す。
その文面はエージェントが読むので、代わりに取る手段が書けているかもここで分かる。

## 3. 記録を読む

通した回も含めて 1 行 1 件で残っている（[記録](records.md)）。読むのは 4 つ。

```sh
# 判定の内訳
jq -r '.decision' logs/decisions.jsonl | sort | uniq -c | sort -rn

# 止めた回。誤検知はここに出る
jq -r 'select(.decision=="deny")|[((.rules//[])|join(",")),(.subject|gsub("[ \t\n]+";" "))]|join("\t")' \
  logs/decisions.jsonl | sort | uniq -c | sort -rn

# どこにもヒットしなかった回。enable ではこれが全部、権限モードに渡る
jq -r 'select(.code=="UNDECLARED")|(.subject|gsub("[ \t\n]+";" "))' \
  logs/decisions.jsonl | sort | uniq -c | sort -rn | head -30

# 通した回を、ヒットしたルール別に。広すぎる allow はここに出る
jq -r 'select(.event=="PreToolUse" and .decision=="allow")|[((.rules//[])|join(",")),(.subject|gsub("[ \t\n]+";" "))]|join("\t")' \
  logs/decisions.jsonl | sort | uniq -c | sort -rn | head -30
```

`subject` は 1 行にまとめてから数える（複数行のコマンドを `uniq -c` が行ごとに別々に数えないように）。
ローテートした分（`logs/decisions.<日時>.jsonl`）も合わせて数えるなら、`logs/decisions.jsonl` の代わりに `logs/decisions*.jsonl` を渡す。

`decision` が `skip` の行は判定まで進まなかった回で、`reason` に理由が入る。
`paths` が付いた行は実行後チェックが拾った変更で、引数に現れない書き込みはここで分かる。
欄の意味は[記録と振り返り](records.md)にある。

記録には絶対パスとコマンド全文が入る。`logs/` はコミットしない。

## 4. ルールを直す

| 記録に出るもの | 直す先 |
|---|---|
| 止めるつもりのなかったものが `deny` に出ている | ルールのパターンを絞る。語の切れ目が要るなら `regex` へ |
| 同じ呼び出しが `UNDECLARED` で並ぶ | `allow` に足す。1 行足すたびにユーザが見なくなる範囲が広がる |
| `allow` で通っているが止めたいものがある | `deny` か `ask` に足す。強いタイプが先にヒットする |

直したら、そのつど 3 つを回す。

```sh
ccnavi --lint                              # 防御を無効化しうる記述が無いか
ccnavi --test Bash "cd /repo && git push"  # 1 件が何にヒットするか
ccnavi --test-expectations .ccnavi/config/common/rule-expectations.yml  # 期待する判定をまとめて回す
```

期待する判定の書き方は[設定の確かめ方](check.md)にある。

ルールを 1 件足したら期待する判定も 1 行足す（[設定の確かめ方](check.md)）。止めたくないものも必ず一緒に置く。
`/ccnavi-config` スキルがこの流れをまとめて回す。記録を数えて `deny` / `ask` の下書きを起こすのは `ccnavi --suggest`
（[設定の確かめ方](check.md)）。ルールの書き方は[ルールの書き方](rules.md)にある。

## 5. enable へ切り替える

目安は記録の側にある。

- 普段の作業で `UNDECLARED` がほとんど出ない（残ったままだと、作業の大半が判定を受けずに権限モードへ渡る）
- `deny` に出ているものが、全部「意図して止めたもの」になっている
- `--lint` の `error` が 0 件

`CCNAVI_MODE` を `enable` にして、セッションを開き直す。記録は同じファイルに続き、
`deny` と `ask` の `enforced` が `false` から `true` に変わる。

`disable` は `.claude/settings.json` に書いても有効にならない（[設定](settings.md)の「動作モード」）。
一時的に外すなら、セッションを起動する側の環境から渡す。

## 導入スクリプトで設定と道具を置く

設定は環境変数で、`.claude/settings.json` の `env` ブロックに書く。hook には実行ファイルだけを登録すればよい。ccnavi の根で打つ。

```sh
sh scripts/ccnavi-setup.sh <ワークスペースルート>
```

このスクリプトが hook と `env` を書く。書く形は[設定](settings.md)の例にある。何度打っても同じ形になり、既にある値と、ccnavi と関係のない hook はそのまま残る。

| 付けるもの | 働き |
|---|---|
| `--check` | 何も書かず、揃っていないところだけを見る |
| `--mode <値>` | `CCNAVI_MODE` を指定する。`disable` は断る |
| `--force` | 既にある値や配布物を入れ替える。`--mode` か `--ticket-control` を名指しして付けたときだけ、その値を置き換える。名指ししていない値はそのまま |
| `--no-vscode` | `.vscode/settings.json` に触らない（[VS Code からワークツリーを見る](#vs-code-からワークツリーを見る)） |
| `--no-fetch` | セッション開始時の取り込み（`ccnavi-fetch.sh`）と、`projects/` の clone 済みリポジトリの一覧を登録しない。1 台だけで使いリモートに合わせる必要が無いとき |
| `--no-idle` | 放置したセッションの引き継ぎ促しを登録しない（[設定](settings.md)の「大きな会話を放置したとき」） |
| `--deploy <ccnavi の根>` / `--no-deploy` | 配布元を名指しする / 配布物を置かず設定だけ書く |
| `--bin user` | 実行ファイルをホームディレクトリの下に置く（下の節） |

既にある値は置き換えず、違えば並べて見せる。hook は、そのイベントに ccnavi が登録されていなければ足す。別の表記で登録されているように見えるイベントは、足さずに名前を挙げる（足すと判定が 2 回走るため）。

戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES`）は `CCNAVI_MODE` と同じ値で書く。`dry-run` で入れたままだとこの 2 つも `dry-run` のまま残るので、`enable` へ切り替えるときは `--mode enable` で打ち直す。切り替える前に、記録の `would-restore` で何が戻るはずだったかを確かめられる。
`CCNAVI_GUARD_TICKET_APPROVAL` はモードに合わせず、いつも `enable` で書く。`dry-run` と書かれていたら `--lint` が error にする。

入れ終わったところで、ワークスペースの git の索引に `projects/` の下が載っていないかも見る。問題があれば直し方を案内するだけで、止めず、終了コードも変えない。索引も `.gitignore` も変えない（直すのはユーザ）。

置き場は固定で env には書かない（[設定](settings.md)の「置き場」）。

## 実行ファイルとルールを配る

hook が起動する実行ファイル、`rules.yml`、拒否の文面が案内する sh が、対象プロジェクトの中に要る。設定を書く 1 回で、配布物も置く。

```sh
# ソースから使うときだけ。ccnavi のリポジトリで、先に実行ファイルを組み立てる
uv run --with pyinstaller python build.py

# 対象プロジェクトへ、設定を書いて実行ファイルを配る
sh scripts/ccnavi-setup.sh /path/to/project --mode dry-run
```

配布元は既定で、ccnavi の根である。よそから配るときは `--deploy <ccnavi の根>` で名指しする。

実行ファイルは、配布先の `.ccnavi/bin/<os>-<arch>/` に入る。`<os>-<arch>` は組み立てた機械で決まる。hook は `.ccnavi/scripts/ccnavi-launcher.sh` を起動し、この sh がその機械に合う実行ファイルを選ぶので、`settings.json` を Windows・WSL・Linux・macOS で共有できる。実行ファイルは組み立てた機械の OS と CPU でしか動かない。別の機械向けの組み立ても、その機械の置き場に入るだけなので配る。Windows と WSL で同じフォルダを開くなら、それぞれの機械で打てば両方が並ぶ。

この機械で動く実行ファイルが無ければ、導入スクリプトは出力の「まだ無いもの」で知らせる。実行時に見つからないときは終了コード 127、実行できないときは 126 で終わる（[つまずきやすい点](troubleshooting.md)）。
目印が無いか読めない配布元からは配らない。名指しの `--deploy` なら終了コード 2 で断り、既定の配布元なら理由を出して設定だけ書く。

### 実行ファイルをホームディレクトリの下に置く（`--bin user`）

実行ファイルの置き方は 2 通りで、導入先のプロジェクトごとに選べる。

| `--bin` | 置き場所 | 使う場面 |
|---|---|---|
| `project`（既定） | 配布先の `.ccnavi/bin/<os>-<arch>/` | プロジェクトごとに実行ファイルを持つ |
| `user` | 実体は `~/.local/share/ccnavi/<os>-<arch>/`。macOS・Linux・WSL は、PATH に通す `~/.local/bin/ccnavi` からそこへリンクを張る。Windows（Git Bash）はリンクを張らず、この実体のフォルダを PATH に通す | 複数のプロジェクトで 1 つを使う。配布先には実行ファイルを置かない |

```sh
sh scripts/ccnavi-setup.sh /path/to/project --bin user
```

ホームディレクトリに書き込むので、ターミナルで確認を求める（`続けますか？ [y/N]`）。ターミナルでなければ、何も書かずに断る。確認を飛ばす指定は無い（`n` などで中止したときの終了コードは 1）。エージェントに実行させない運用が前提になる。
中身が配布元と同じなら、確認なしで終わる。違えば、また確認して置き換える。`~/.local/bin/ccnavi` に別のファイルが既にあれば触らない（置き換えるなら `--force`）。配布先の `.ccnavi/bin/` に実行ファイルが残っていると、そちらが先に使われるので、残っていることを案内に出す。`--check` は書かず、確認も求めない。
`--bin` は `ccnavi-install.sh` に渡せない。`ccnavi-setup.sh` を直接打つ。

実体は同じ機械の全プロジェクトが使うので、1 つのプロジェクトで再実行して置き換えると、ほかのプロジェクトにも効く。PATH に通すのは、隣に `_internal/` がある実体のフォルダで、実行ファイル単体のコピーは動かない。この機械で動く実行ファイルだけを置ける。

PATH に通すフォルダが PATH に入っていなければ、環境に合った足し方を知らせる。シェルの設定ファイルも Windows の環境変数も、書き換えない。

| 環境 | 案内する足し方 |
|---|---|
| Windows（Git Bash） | PowerShell でユーザの環境変数 `Path` に実体のフォルダを追加する。画面から追加する手順も添える。追加したあと、Git Bash と Claude Code を開き直す |
| macOS・Linux・WSL の zsh | `~/.zshrc` に `export PATH="$HOME/.local/bin:$PATH"` を追加する |
| macOS の bash | `~/.bash_profile` に同じ行を追加する |
| Linux・WSL の bash | `~/.bashrc` に同じ行を追加する |
| fish | `fish_add_path $HOME/.local/bin`（1 度実行すれば保存される） |
| そのほか | `~/.profile` に同じ行を追加する |

### 何がどこへ配られるか

| 配布元（ccnavi の根） | 配布先 |
|---|---|
| `dist/ccnavi/`（中身ごと） | `.ccnavi/bin/<os>-<arch>/` |
| `.ccnavi/config/common/` の `rules.yml`・`risks.yml`・`phases.yml` | 同じパス |
| `.ccnavi/config/common/templates/` の計画テンプレート | 同じパス |
| `.ccnavi/scripts/` の sh（`ccnavi-*.sh`）と `ccnavi-clean.js` | 同じパス。振り分けの sh には実行ビットを付ける |
| `.ccnavi/CCNAVI.md` | 同じパス |
| `.ccnavi/docs/`（エージェント向けの使い方の文書） | 同じパス |
| `.ccnavi/messages/`（拒否と案内の文面） | 同じパス |
| `.claude/skills/` の工程スキルと `ccnavi-config` | 同じパス |

`scripts/` の `ccnavi-setup.sh`・`ccnavi-install.sh`・`ccnavi-doctor.sh` は配布先へ配られない。ccnavi の根に残り、そこから打つ。

ルールと配点とフェーズ定義のひな形は、共通レイヤー（`.ccnavi/config/common/`）へ配る。ワークスペース自身のレイヤーの `phases.yml` は配らない（レイアウトに依存する定義を共通レイヤーに置くと全プロジェクトに適用されてしまうため。[ルールの書き方](rules.md)の「3 つのレイヤー」）。要るならユーザが書く。

配布先に既にあるものは触らず、並べて見せる。入れ替えるのは `--force` を付けたときだけ（ルールも sh も配布先で直されている前提）。例外は、この機械の実行ファイル（`.ccnavi/bin/<os>-<arch>/`）で、配布元と中身が違えば `--force` が無くても置き換える（組み立て直した実行ファイルが古いまま残らないように。`--check` は、中身が違うものを揃っていないものに数える）。

振り分けの sh は hook が直に起動するので実行ビットが要る。配った回は付け、配らない回でも外れていれば付け直す。別の機械で clone した直後に実行ビットが無いことがあるのは、Windows で足した sh が実行ビットを持たないため。`--check` は実行ビットの外れた sh を揃っていないものに数え、`--lint` は error として報告する（[設定の確かめ方](check.md)）。

### .gitignore

配布先が git のリポジトリなら、導入スクリプトが `.gitignore` に次の行を足す。配った実行ファイルの置き場は、入れると履歴から消すのが難しいために足す。

```
# ---ccnavi begin--- (scripts/ccnavi-setup.sh が管理する範囲)
# 実行ファイル
/.ccnavi/bin/darwin-arm64/
# --docs の索引
**/ccnavi-index.jsonl
# 使い捨ての置き場
/scratchpad
# 実行時の記録
/logs/
# ワークツリーの置き場
/.claude/worktrees/
# ---ccnavi end---
```

- ccnavi が足した行は、目印の行で囲んだ 1 つの領域にまとまる。領域の中のほかの行は書き換えない。目印の行が壊れているときは何も足さず、行番号と直し方を知らせる
- 同等の行が既にあるとき、またはそれらを `!` で否定する行があるとき（ユーザが追跡すると決めているとみなす）は足さない
- 足すのはワークスペースの `.gitignore` だけ。`projects/<名前>/` の各プロジェクトには触らない
- `.ccnavi/` を丸ごと無視すると sh と `rules.yml` が git から消えるため、足すのは `/.ccnavi/bin/<os>-<arch>/` だけ

### 失敗したとき

名指しした `--deploy` が組み立てられていなければ、終了コード 2 で断る。既定の配布元が使えないとき（組み立てていない、配布先が ccnavi 自身）は断らず、配るのを諦めた理由を 1 行出して `.claude/settings.json` は書く。

## VS Code からワークツリーを見る

ccnavi は作業を `.claude/worktrees/` の中でさせる。VS Code の設定に下の 1 行が無いと、
エディタからはワークスペースルートのブランチしか見えない。

```json
"git.detectWorktrees": true,
"git.repositoryScanMaxDepth": 2
```

2 行目は `projects/<名前>/` のようにワークスペースルートから 2 階層下にあるリポジトリを見つけるための設定で、
既定の 1 だとルートの直下の階層までしか探さない。

ccnavi の根で打つ `sh scripts/ccnavi-setup.sh <ワークスペースルート>` が、`.claude/settings.json` と同じ 1 回で
`.vscode/settings.json` にもこれらを書く。無ければ作り、あれば足りないキーだけを足す。
VS Code の他の設定は残し、`false` と書いてあれば変えずに並べて見せる。

`.vscode/settings.json` にコメントや末尾のカンマがあると（`jq` が読めない）、
何を足せばよいかだけを出し、`.claude/settings.json` は書く。自分で書きたいときや
VS Code を使わないときは `--no-vscode` を付ける。

チケットの状態を見る VS Code の拡張「ccnavi ボード」の使い方は[拡張の使い方](extensions.md)にある。

## 次に読むもの

つまずいたときは[つまずきやすい点](troubleshooting.md)を見る。
