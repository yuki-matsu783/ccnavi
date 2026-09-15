---
version: 1
ticket: shellread-subst
issue: 28
title: 引用の中とバッククォートのコマンド置換を、独立したコマンドとして読む
rationale: 'shellread は字句の分割を shlex に任せていて、shlex は二重引用の中の `(` `)` を

  区切りとして返さない。バッククォートは引用の外でも区切りにならない。そのため

  `$( )` とバッククォートの中身は、shell が実行するのに判定に入らないことがある。

  `grep -n "$(git push)" f` は allow になり、確認も出ずに通る。


  二重引用で包むのは、変数展開を安全にするための普通の書き方で、わざと回避する

  形ではない。`h="$(git rev-parse HEAD)"` も raw-git に当たらない。設計 §2.1 が主対象に

  置く「逸れていく LLM」がそのまま踏むので、§12.1 の「敵対的な回避」には入らない。

  失敗の向きが素通りなので、§12.2 の方針どおり塞ぐ。


  判断の基準は「回避策の無い誤検知を作らない」。中身を切り出すと誤検知が増えるが、

  書き直せば通るものは受け入れる。文字として書く道（単一引用）を必ず残し、

  止めたときは文面で回避策を名指しする。


  見本（.ccnavi/common/rule-samples.yml）への追記は、組み込みのルールで守られていて

  エージェントは書けないので、このチケットの外で利用者に依頼する。

  '
human_review:
  required: true
  reason: 判定の入力の形が変わり、今 allow の形（heredoc で本文を渡すコミットなど）が deny に変わる
plan:
- type: design
  review: defer
- type: acceptance
  review: defer
- implement
- docs
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: HANDOVER.md
started_at: ''
completed_at: ''
base_sha: ''
ccnavi_approved:
  approved_at: 2026-09-14T01:06:54+0900
  source_tree: shellread-subst
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/shellread-subst/wip/tickets/todo/shellread-subst.md
  revised_at: 2026-09-15T17:50:05+0900
  feedback_at: 2026-09-15T17:50:05+0900
feedback: []
---

# 引用の中のコマンド置換を読む

Issue #28。

## いま

| 書いたコマンド | shell | 今の判定 |
|---|---|---|
| `echo $(git push origin main)` | 実行する | deny（raw-git） |
| `echo "$(git push origin main)"` | 実行する | ask（どのルールにも当たらない） |
| `grep -n "$(git push)" f` | 実行する | **allow**（prefer-read-grep） |
| `h="$(git rev-parse HEAD)"` | 実行する | ask（どのルールにも当たらない） |
| `echo "$(rm -rf /tmp/x)"` | 実行する | ask（recursive-delete に当たらない） |
| `` echo `find . -delete` ``（引用の外） | 実行する | ask（find-writes に当たらない） |
| `cat <<EOF` の本文の `$(x)` | 実行する | 本文ごと捨てている。heredoc ルールの無いプロジェクトでは抜ける |

shellread.py 63〜66 行目のコメントと ccnavi.md §6.3 の 5 番（「`$( )` の中身は独立したコマンドになる」）は、
`$( )` が引用の外にあるときにしか成り立たない。

## 変えたあと

引用の有無にかかわらず、shell が実行する `$( )` とバッククォートの中身を独立したコマンドとして読む。
上の表はすべて `echo $(git push origin main)` と同じ読みになる。

## 守る条件

1. **文字として書く道を必ず残す。** 単一引用の中、`$'...'` の中、`\$(` と `` \` ``、
   引用付き heredoc（`<<'EOF'` `<<"EOF"` `<<\EOF`）の本文からは切り出さない。
   どんな文字列も単一引用（`'` は `'"'"'`）で文字として渡せる状態を保つ
2. **読み切れないときは縮退する。** 閉じない `$(` とバッククォート、shell で答えが割れる形
   （`$( )` の中の `case` の `)`。bash 3.2 は構文エラー、zsh は実行する）
3. **引用の中から切り出したコマンドにルールが当たったら、文面で回避策を名指しする。**
   書いた側は文字を書いただけのつもりでいる。回避策を知らなければ、あっても使われない

## 増える誤検知と回避策

| 誤検知 | 回避策 |
|---|---|
| ``"use `git push` here"`` のように、二重引用の中に Markdown のバッククォートを書いた（shell は実際に実行する） | `` \` ``、単一引用、`--body-file` |
| `commit -m "$(cat <<'EOF' … EOF)"` に heredoc ルールが当たる（今は allow） | 本文を Write で置いて `commit -F <ファイル>`。1 行の `-m`、改行を含む `-m "…"` |
| `gh pr create --body "$(cat <<'EOF' …)"` | `--body-file <ファイル>`、単一引用 |
| `grep -rn foo "$(pwd)"` などで allow が外れて ask になる | 値をそのまま書く。引用しない `grep foo $(pwd)` は今も ask なので揃うだけ |
| `cd "$(git rev-parse --show-toplevel)"` が raw-git に当たる | `ccnavi-git.sh rev-parse …` で出して、読んだ値を次のコマンドに書く |
| `$( )` の中の縮退（`xargs`、`sh -c`、shell で割れる形）が全体に広がる | スクリプトを Write で置いて `sh <ファイル>` |
| 閉じないバッククォートで、本文全体が生の文字列に当たる | shell でも構文エラーなので、本文を直す |

回避策の綴りは、どれも今の判定で止まらないことを確かめてある（`commit -F` は allow、それ以外は ask）。

## 決めたこと

- **heredoc ルールは、切り出した中身にも当てる。** 回避策（`-F`、`--body-file`）があるので例外を作らない
- **`"$(pwd)"` などで allow が外れるのは受け入れる。** 害の無いコマンドの一覧で allow を残す案は、判定を緩めるうえに保守が要るので採らない
- **引用しない heredoc の本文と、引用の外のバッククォートも範囲に入れる**
- **ccnavi-git.sh は変えない。** git の値を変数に取る形は「出して読み、値を書く」の 2 手で扱い、文面で案内する。
  `logs/log.jsonl` の Bash 1932 件（重複を除く）に、実作業で git の値を `$( )` で取った例は無い

## 範囲に入れないもの

- 変数展開、alias、`eval` の中身（§12.1）
- 応答の subject で空白が落ちる件（`echo "$(git push origin main)"` が `echo $(gitpushoriginmain)` と出る）。別件
- `$'...'` の中の `\'` で shlex が閉じない引用と読み、縮退する件（既存の限界）
- 見本ファイルへの追記（rationale のとおり、チケットの外で利用者に依頼する）

## shell の実測

bash 3.2（macOS の `/bin/bash`）と zsh で、中身の代わりに記録を残す関数を置いて確かめた。
設計フェーズはこれを見本の表の元にし、Git Bash（bash 5 系）でも取り直す。

**中身を実行する**

- `echo "$(x)"`、`` echo "`x`" ``、`echo "\\$(x)"`、`echo "it's $(x)"`、`echo a#$(x)`
- `cat <<EOF` の本文の `$(x)` とバッククォート、``echo "$(cat <<EOF … `x` … EOF)"``
- 入れ子: `echo "$(echo "$(x)")"`、`` echo `echo \`x\`` ``、`"${v:-$(x)}"`、`` "${v:-`x`}" ``、`"${a[$(x)]}"`
- 算術の中: `"$(( $(x) + 1 ))"`。空白を挟んだ `"$( (x) )"` はコマンド置換
- `$( )` の中の `)` をまたぐ: `"$(echo "a)b"; x)"`、`"$(echo 'a)b'; x)"`、`"$(echo a # )⏎x)"`
- `cat <<< "$(x)"`、`"$(⏎x⏎)"`、`h="$(x)"`、`echo x > "$(x)"`、`for f in "$(x)"`、`[[ "$(x)" = y ]]`、`cat <(x)`

**中身を実行しない**

- `echo "\$(x)"`、`` echo "\`x\`" ``、`echo '$(x)'`、`` echo '`x`' ``、`echo $'$(x)'`
- `echo hi # $(x)`
- 引用付き heredoc（`<<'EOF'` `<<"EOF"` `<<\EOF`）の本文。``echo "$(cat <<'EOF' … `x` $(x) (see) don't … EOF)"`` も
- `"$(echo '$(x)')"`
- `echo "$((1 + 2))"`、`echo "$[1+2]"`、`echo "<(x)"`
- 閉じない `echo "$(x"`、`` echo "`x" ``（構文エラー）
- `"$(echo \")\"; x)"`（bash も zsh も `\"` の直後の `)` で閉じる）

**shell で割れる**

- `"$(case a in a) x;; esac)"`: bash 3.2 は構文エラー、zsh は実行する

## 確かめること

- 上の「実行する」はすべて切り出され、「実行しない」は切り出されないこと
- `(^|\x00)` を使う既存の regex（raw-git、find-writes、prefer-webfetch、組み込みのルール）が、切り出した中身に
  引用の外と同じように当たること。allow の `[^\x00]*$` が、中身の続くコマンドで外れること
- 回避策の綴り（単一引用、`\$(`、`commit -F`、`--body-file`）が止まらないこと
- 条件 3 の文面が、引用の中から切り出したコマンドに当たったときだけ出ること
- 今の tests/test_shellread.py と見本の判定が変わらないこと。変わるものは一覧にして理由を書く

## 進め方

1. 設計（延期。次のレビューと一緒に見る）: 切り出し方、切り出した中身を組み直す位置、縮退の条件、
   文面、影響する regex の一覧を `wip/design/shellread-subst.md` に書く
2. 受入テスト作成（延期）: 「shell の実測」と「確かめること」を tests/ に書く
3. 実装とテスト: shellread.py と、文面を出す呼び手を直す。1 と 2 をここで一緒にレビュー
4. 文書: README「Bash のコマンドは実行される部分だけを見る」（714 行目の例を含む）、ccnavi.md §6.3 と §12.2、
   HANDOVER.md
