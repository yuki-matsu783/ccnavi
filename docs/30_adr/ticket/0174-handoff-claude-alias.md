---
type: adr
title: 下書きの置き場では .claude の階層を handoff-.claude に読み替え、表は sh と実行ファイルが同じファイルから読む
description: Claude Code 本体は .claude という名前の階層を機密パスとして扱い、非対話では wip/handoff/.claude/… の下書きを編集できなかった。下書きの相対パスの階層名を表で読み替えて置き、取り込むときに戻す。表は .ccnavi/scripts のデータファイル 1 本にして、sh（ccnavi-stage.sh）と実行ファイルが同じものを読む
tags: [handoff, selfguard, sh-scripts]
keywords: [handoff, 下書き, .claude, handoff-.claude, 読み替え, 予約名, ccnavi-stage.sh, 非対話, claude -p, 機密パス, ccnavi-handoff-names.txt, 後方互換]
---
# ADR-0174: 下書きの置き場では .claude の階層を handoff-.claude に読み替え、表は sh と実行ファイルが同じファイルから読む

状態: 採用

## 状況

`ccnavi-stage.sh` は保護対象を `wip/handoff/<元と同じ相対パス>` にコピーして編集させる。`.claude/settings.json` の写し先は `wip/handoff/.claude/settings.json` になる。

Claude Code 本体は、`.claude` という名前の階層（深さを問わない）を機密パスとして扱い、`--allowedTools Edit` を渡しても編集を断る。対話なら許可できるが、非対話（`claude -p`）では許可できない。そのため、保護対象のうち `.claude/settings*.json` と `.claude/hooks/` は、非対話では下書きを編集できず、直す道が無かった。

実機で確かめた本体の断り（`wip/handoff/` の下で Edit を試し、ファイルの中身が変わったかで判定）。

- 断る: `.claude/` 配下（深さを問わない）、`.vscode/`、`.git/`、`.idea/`、`.husky/`、`.mcp.json`、`.gitconfig`、`.gitmodules`、`.bashrc`、`.zshrc`、`.profile`、`.ripgreprc`、`.claude.json`
- `.claude/` の中で通る: `skills/`、`commands/`、`agents/`、`worktrees/`
- 通る: `.github/`、`.ccnavi/`、`.env`、`.gitignore`、`.gitattributes`、`wip/handoff/handoff-.claude/settings.json`

ccnavi の保護対象と重なるのは `.claude/settings*.json` と `.claude/hooks/` だけ。

## 決定

| 決めたこと | なぜ |
|---|---|
| 下書きの相対パスの中の `.claude` の階層を、深さを問わず `handoff-.claude` に読み替えて置く。取り込むときに戻す | 本体が断るのは名前が `.claude` の階層。名前を変えれば、下書きの置き場の中は編集できる |
| 読み替える名前は表（元の階層名 → 下書きでの階層名）にする。今は `.claude` の 1 行だけ。ほかの名前（`.vscode` など）は、表に 1 行足せば広がる | 本体の断りは増えうる。足すたびに sh と Python の分岐を書き足したくない |
| `handoff-.claude` は予約した名前にする。元のパスにその名前の階層がある保護対象は読み替えの対象外にし、`ccnavi-stage.sh` が断る | 読み替えた下書きから元のパスを一つに戻すため。黙って許すと、別の 2 つの元が同じ下書きに落ちる |
| 読み替える前の形（`wip/handoff/.claude/…`）の下書きは特別に扱わない。表の下書きでの名前を含まないふつうのパスとして、そのまま元のパスと読む | 検出も断りも案内も持たない。後方互換の道を作らず、実装を増やさない |
| 名前の比べ方は、大文字小文字を区別しない（`.CLAUDE`、`Handoff-.Claude` は同じ）。表の名前は ASCII だけで、非 ASCII の要素は表に当てない | 決まり（パスと名前は大文字小文字を区別せず比べる）に従う。sh は ASCII の大文字小文字しか畳めないので、Python も ASCII の要素だけを当てて、同じ答えにする |
| 表は `.ccnavi/scripts/ccnavi-handoff-names.txt` の 1 か所にし、`ccnavi-stage.sh`（sh）が実行時に読み、実行ファイルには `build.py` が同じファイルを同梱する。実行ファイルはソースでは同じパスから読む | 表を sh と Python に二重に書くと食い違う。元を 1 つにする |
| 変換の位置は、sh が下書きのパスを組むところ（`ccnavi-stage.sh`）と、Python が下書きを読むところ（`handoff.walk`・`wanted_key`・`draft_rel`）の 2 つ。`ccnavi-git.sh integrate` は、下書きを起点の同じパスと比べるだけなので読み替えを知らない。取り込み漏れは実行ファイルの `tree handoff-pending` が数える | 変換を持つ場所を最小にする |

## 理由

- 本体の断りは `.claude` という名前の階層に掛かっている。置き場の中の名前を変えれば編集できる。内容も、置き場も、取り込みの仕組みも変えずに済む。
- 表の持ち方は、実行ファイルに聞く案（Python が正本で、sh は実行ファイルの小さなサブコマンドを呼ぶ）と、データファイルを共有する案を比べた。`ccnavi-stage.sh` は実行ファイルが無くても動く作りで、受入テストも、実行ファイルを置かない使い捨てのワークスペースで sh を起こしている。stage が実行ファイルに頼ると、実行ファイルが起動しない状況（保護対象の sh を直して直すときなど）で、保護対象を直す道そのものを失う。データファイルなら、sh は実行ファイル無しで動き、Python は同じファイルを読むので、表は 1 つのままになる。
- 予約名を黙って許さないのは、「同じ名前を両立させない」と同じ考えで、読み替えを逆向きにたどれることを守るため。
- 古い形を特別に扱わないのは、その形の下書きは本体が編集を断るので、非対話では編集済みのものが生まれないため。読み替えの対象でないふつうのパスとして扱えば、検出の実装も案内も要らない。

## 得たもの・失ったもの

- 得たもの: 非対話でも `.claude/settings*.json` と `.claude/hooks/` を、stage → 編集 → ユーザの取り込みまで通せる。
- 得たもの: ほかの名前も、表に 1 行足し、テストの見本に 1 行足せば広がる。
- 失ったもの: 下書きのパスが元のパスと一致しない。下書きを探す側は読み替えを通す必要がある（VS Code ボードは、JSON の `path`（元のパス）と `draft`（下書きのパス）の両方を受け取るので、直していない）。
- 失ったもの: `handoff-.claude` という名前の階層を持つ保護対象は、下書きにできない。
- 失ったもの: 表のファイルを配布物に加えた。`scripts/ccnavi-setup.sh` が配り、`build.py` が実行ファイルに同梱する。配り忘れると stage は何も作らずに断る。

## 採らなかった案

- `.claude` の下書きだけ別の置き場（`wip/handoff-claude/` など）に置く案: 置き場が 2 つになり、`pending`・`ready`・ガードの例外・ボードが 2 か所を見ることになる。
- 名前を単純に消す案（`wip/handoff/claude/settings.json`）: `claude/` という実在の階層と区別がつかない。予約名の `handoff-.claude` は、先頭が `handoff-` で始まり、実在する階層と重なりにくい。
- 表を Python に持たせ、sh が実行ファイルのサブコマンド（`handoff-path to-draft`）を呼ぶ案: 上の理由で、stage が実行ファイルに頼る。
- 表を sh と Python の両方に書き、テストで食い違いを見る案: 元が 2 つになる。テストは食い違いを見つけるだけで、防げない。
- 下書きを書く側が `Bash` の `sed` で直接直す案: 保護対象の生の書き換えを許す形になる。
