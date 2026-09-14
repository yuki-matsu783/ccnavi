# 引用の中のコマンド置換を読む（設計）

課題（Issue #28）: `ccnavi/shellread.py` は字句の分割を shlex に任せていて、shlex は二重引用の中の
`$( )` とバッククォートを区切りとして返さない。そのため shell が実行する中身が判定に入らず、
`grep -n "$(git push)" f` が allow になる。

親チケット `shellread-subst` の「守る条件」「決めたこと」はそのまま前提にする。この文書は、
子 `shellread-subst-01` の「決めること」4 つと「洗い出すこと」に答える。

## 0. 範囲に足したもの

設計の途中で、同じ種類の穴（shell が実行するのに、判定では独立したコマンドとして読まれない）を
今の main に 4 つ見つけた。利用者の判断（2026-09-14）で、このチケットに含める。

| 穴 | 例 | 今の判定 | 足したあと |
|---|---|---|---|
| 改行がコマンドの区切りにならない | `grep -n x f⏎sh evil.sh` | **allow**（prefer-read-grep） | ask |
| プロセス置換の中身が独立しない | `cat <(sh evil.sh)` | **allow**（prefer-read-grep） | ask |
| 語の途中の `#` から後ろを shlex がコメントとして捨てる | `grep -n a#b f; sh evil.sh` | **allow**（prefer-read-grep） | ask |
| 予約語の直後がコマンドの先頭として読まれない | `if true; then find . -delete; fi` | ask（find-writes に当たらない） | deny |

どれも失敗の向きが素通りで、うち 3 つは allow が後ろのコマンドまで通す。親チケットの
「引用の外の `$( )` の今の形と揃えるか」（子 01 の決めること 2）を詰めるうちに、次の穴も見つかった。

| 穴 | 例 | 今の判定 | 変更後 |
|---|---|---|---|
| 引用の外の `$( )` が外側のコマンドを 2 本に割る | `find $(pwd) -name x -delete` | ask（find-writes に当たらない） | deny |
| リダイレクト先の `"$(…)"` が組み込みの守りに当たらない | `echo x > "$(pwd)/.ccnavi/common/rules.yml"` | ask | deny |

親チケットの本文（範囲）は改版で直せない（改版で変えられるのは `plan` と `feedback` だけ。README「改版」）。
書き込む場所は親の `allow` がすでに覆っているので、足した範囲は後続の子（受入テスト・実装・文書）の
本文に書き、その承認で見てもらう。

## 1. 決めたこと

### 1.1 中身の見つけ方: shlex に渡す前に原文を 1 回走査する

子 01 の 2 案を比べた。

| | A. shlex の前に原文を走査する（採用） | B. shlex のトークンに引用の情報を足す |
|---|---|---|
| 得るもの | 引用の状態を自分で持つので、`$( )`・バッククォート・プロセス置換・heredoc・コメント・改行を 1 回の走査で片付けられる。shlex は語の分割にだけ使い続けるので、`_join` と `_render`、既存の縮退はそのまま残る | 字句の規則が 1 か所に閉じ、2 つの読みが食い違う余地が無い |
| 失うもの | 引用の規則を shlex と自前の 2 か所で持つことになり、食い違えばそこが穴になる（試作では 65 形を shell と突き合わせて食い違い 0）。コードが約 300 行増える。ADR-0001「字句は shlex に任せる」を一部やめるので、文書に理由を書き足す必要がある。大きな入力で遅くなる（§1.6） | `shlex.read_token` の状態機械は公開の API ではなく、中の状態（引用の中か）を取り出すには関数ごと写して直すことになる。Python の版で壊れる。heredoc と改行とコメントは、それでも別に扱う必要が残る |

A を採る。B は結局 shlex の中身を写すことになり、2 つの読みを持つ点は A と変わらないうえ、
写した先が標準ライブラリの変更に追従できない。

走査（`_Scanner`）がすることは次の表のとおり。shlex には、この結果の文字列を渡す。

| 走査で出会うもの | 扱い |
|---|---|
| 単一引用 `'…'`、`$'…'`（二重引用の外） | 文字。中を見ない |
| `\` の後ろの 1 文字 | 文字。`\$(` と `` \` `` は切り出さない |
| 二重引用 `"…"` | 中の `$( )`・バッククォート・`${ }`・`$(( ))` だけを見る |
| `$( … )` | 対になる `)` まで読んで中身を切り出し、外側には `$` 1 文字を残す。中では引用の状態が最初からに戻る |
| `` `…` `` | 閉じる `` ` `` まで。中身は `` \` `` `\$` `\\`（二重引用の中なら `\"` も）を外してから切り出す |
| `<( … )` `>( … )`（引用の外） | `$( )` と同じく切り出す |
| `$(( … ))` | 算術。中の `$( )` とバッククォートだけを切り出す。算術そのものは今までどおり `_drop_arithmetic` が落とす |
| `${ … }` | 中の引用と置換を見る |
| `#`（引用の外で、語の始まり） | 行末までコメントとして落とす。語の途中の `#`（`a#b`、`$#`、`${#x}`）は文字 |
| 改行（引用の外） | ` ; ` に置き換える。コマンドの区切り |
| `<<` `<<-` | heredoc の始まり。区切りの語を読み、次の改行のあとで本文と終端の行を落とす。区切りが引用されていなければ（`<<EOF`）本文の `$( )` とバッククォートを切り出す。引用されていれば（`<<'EOF'` `<<"EOF"` `<<\EOF`）本文は文字 |
| `<<<` | here-string。演算子のまま |

shlex には `commenters = ""` を渡す。shlex は語の途中の `#` からもコメントにする（`echo a#b c` → `['echo', 'a']`、
bash は `a#b c` と出す）ので、コメントは走査の側だけで扱う。トークンで heredoc の本文を落とす
`_drop_heredoc_bodies` は使わなくなる（本文は走査が落としてある）。

予約語（`if then else elif fi do done while until for case esac select time ! { }`）がコマンドの位置に
立ったら、それだけで 1 本のコマンドにする（`_split_commands`）。`then find . -delete` の find を
コマンドの先頭として読ませるため。落とさずに残すのは、`! grep x f` を `grep x f` と読んで
prefer-read-grep の allow に当てないため（今も allow ではない）。

### 1.2 組み直す位置: 外側のコマンドの後ろに `\x00` でつなぐ

切り出した中身は `read()` で再帰して読み、外側のコマンドの読みの後ろに、出てきた順に `\x00` でつなぐ。
外側の、置換があった場所には `$` 1 文字を残す。引用の外の `$( )` も同じ形にそろえる。

```
echo "$(git push origin main)"    →  echo $␀git push origin main
echo $(git push) foo              →  echo $ foo␀git push          （今は echo $␀git push␀foo）
find $(pwd) -name x -delete       →  find $ -name x -delete␀pwd   （今は find $␀pwd␀-name x -delete）
```

| 案 | 得るもの | 失うもの |
|---|---|---|
| 後ろにつなぐ（採用） | 外側のコマンドが割れないので、`[^\x00]*` で「同じコマンドの中」を見るルール（find-writes、selfguard の書き込み動詞）が外側を丸ごと見られる。中身は `\x00` の直後に立つので `(^\|\x00)` のルールがそのまま当たる。中身が 1 本でもあれば `[^\x00]*$` が外れ、allow が中身まで通さない | `$` の位置と中身の対応が文字列からは読めない（記録の subject を読む人には、どの `$` がどの中身か分からない）。今の引用の外の形（`echo $␀git push␀foo`）から変わる |
| 置換のあった位置で挟む（今の引用の外の形） | 書かれた順に並ぶ | 外側のコマンドが割れ、`find $(pwd) -name x -delete` の `-delete` が find と別のコマンドに見える（今ある穴） |

`$` を残すのは、外側の語の数と形を保つため。`-m "$(cat f)"` は `-m $`、`> "$(pwd)/x"` は `> $/x` になり、
リダイレクト先の後半（`/.ccnavi/…`）が語として残る。今は `$␁(␁pwd␁)␁/…` で `[^ \x00\x01]*` が止まり、
selfguard のリダイレクト先の式が当たらない（§0 の最後の行）。

### 1.3 縮退の条件

中身の読みが縮退したら、全体を縮退させ、理由は中身のものを返す。§6.3「複合コマンドは 1 つの区間が
読めなければ全体が縮退する」と同じ扱い。

| 理由 | 条件 | 新旧 |
|---|---|---|
| `unterminated-quote` | 閉じない引用、閉じない heredoc、本文の始まらない heredoc（`cat <<'EOF' > f` だけ） | 既存 |
| `unterminated-quote` | 引用が `<<` だけの 1 語（`grep -n "<<" f`）。トークンに出た `<<` の数が、走査が heredoc と読んだ数より多いときに判定する | 既存の読みを保つ（§12.2 の許容した誤検知） |
| `command-taken-as-code` | `eval` `xargs` `sh -c` など。中身の側で当たっても全体が縮退する（`echo "$(xargs echo < f)"`） | 既存 |
| `unterminated-substitution` | 閉じないバッククォート、閉じない `$((`、引用の外で閉じない `$(`。二重引用の中で閉じない `$(`（`echo "$(git push"`）は、後ろの `"` を `$( )` の中の引用の始まりと読むので `unterminated-quote` になる | **新規** |
| `ambiguous-substitution` | `$( )` か `<( )` の中の `case`（bash 3.2 は構文エラー、zsh は実行する）。`$((cmd) …)` のように算術かコマンド置換かが綴りから決まらない形。入れ子が 16 段を超える形 | **新規** |

理由の名前を 2 つ足すのは、読み手に直す先を名指しするため。`unterminated-quote` に寄せると
「引用は閉じているのに」と読み手が迷う。記録の `degraded` と `reasons.unreadable()` に足す。

入れ子の深さは `read()` の再帰で 16 段、走査の中の Python の再帰は `RecursionError` を捕まえて
`ambiguous-substitution` にする（`"$(` を 400 段重ねた入力で 0.7ms で縮退することを確かめた）。

### 1.4 文面と記録

`Reading` に `bare` を足す。`text` から、引用の中（二重引用と、区切りを引用しない heredoc の本文）で
切り出したコマンドを除いたもの。入れ子の中身も、外側が引用の中なら除く。

```
gh issue create --body "use `git push` here"
  text  gh issue create --body use␁$␁here␀git push
  bare  gh issue create --body use␁$␁here
```

判定は今までどおり `text` に当てる。当たった deny / ask のルールごとに `rule.matches(tool, bare)` も見て、
`bare` には当たらなければ「引用の中から切り出したコマンドに当たった」とする。そのときだけ理由に
次の 1 文を足す（条件 3）。

> note: this rule matched a command inside double quotes or an unquoted heredoc. The shell runs
> $( ) and backquotes there; they are not only written down. To keep them as text, use single
> quotes, escape them as \$( and \`, or pass the text from a file (git commit -F <file>,
> gh --body-file <file>). To use a value such as a git revision, print it with one command first
> and write the value into the next.

| 案 | 得るもの | 失うもの |
|---|---|---|
| `bare` に当て直して比べる（採用） | ルールがどこに当たったかを regex の一致位置から推し量らずに済む。ルールの書き方（glob / regex、`^` / `(^\|\x00)`）に依らない | ルール 1 件ごとに当て直しが 1 回増える（当たったルールだけなので、件数は小さい） |
| 切り出した区間の位置と一致位置を突き合わせる | 当て直しが要らない | glob（fnmatch）は一致位置を返さない。regex でも一致が区間をまたぐ形の扱いを決める必要がある |

記録には `quoted` 欄（そうして当たったルールの id の並び）を足す。空なら書かない（audit.py の他の欄と同じ）。
`--test` の出力にも `quoted:` の行を出す。

縮退の断り（`reasons.unreadable`）に 2 つ足す。

| 理由 | 文 |
|---|---|
| `unterminated-substitution` | a $( ) or backquote in this command never closes |
| `ambiguous-substitution` | a $( ) in this command holds a form that shells read differently (such as case inside $( )), or nests too deep |

`reasons.undeclared` の縮退の文「no heredoc, no string handed to something that runs it」に
「no case inside $( )」を足す。

### 1.5 heredoc の終端の比べ方

終端の行は前後の空白を落として区切りの語と比べる。shell は完全一致（`<<-` なら先頭のタブを落として）で比べる。

| | 得るもの | 失うもの |
|---|---|---|
| 空白を落として比べる（採用） | shell より早く閉じることはあっても遅く閉じることは無い。早く閉じたときは本文の残りをコマンドとして読む（厳しい側）。CRLF の行（`EOF\r`）でも閉じる | 本文に ` EOF` のような行を持つ heredoc で、本文の残りが判定に入り、誤検知になりうる |
| 完全一致 | shell と同じ | shell が閉じるのにこちらが閉じない形（CRLF、`<<-` と空白の字下げ）で、本文の後ろのコマンドを本文として捨てる（素通りの側） |

### 1.6 速さ

判定の期限は 3 秒（`judge.DEADLINE_SECONDS`）。試作で測った（macOS、Python 3.12）。

| 入力 | 大きさ | 今 | 試作 |
|---|---|---|---|
| 引用しない heredoc、本文 5000 行に `$(x)` と `` `y` `` が 1 つずつ | 160KB | 69ms | 382ms |
| 同じ本文を引用した heredoc | 160KB | — | 2ms |
| `"$(aN)"` を 2000 個並べる | 21KB | 12ms | 92ms |
| `"$(echo ` を 15 段入れ子 | 156B | 0.2ms | 1.2ms |

遅くなるのは切り出した中身ごとに shlex を組み直すためで、中身 1 つあたり約 45µs。引用した heredoc
（中身を切り出さない）が 2ms で済むので、走査そのものの重さは小さい。試作の走査は、立ち止まる文字
（`` \ ' " ` $ # ``、改行、`< > ( )`）まで正規表現で飛ばす。
中身の数に上限を設けて縮退させる案は採らない。1 万個でも期限の 1/8 で、上限の値を決める根拠が無い。

## 2. 影響の洗い出し

試作（`wip/design/shellread-subst-proto.py`）を作業ツリーの写しに入れ、既存のテスト 678 件を回した。
**テストに手を入れずに全部通る**（`OK (skipped=32)`、写す前も同じ）。今のテストは、この変更で変わる
読みを固定していない。判定の変化は、写す前と後の写しで `main.py --test` を並べて実測した（§3）。

### ccnavi/

| 場所 | 使い方 | 変わること |
|---|---|---|
| shellread.py `read()` | 入口 | 走査を足し、切り出した中身を再帰して読み、`bare` を組む |
| shellread.py `_tokenize` | shlex | `commenters = ""`。行番号は要らなくなる |
| shellread.py `_drop_heredoc_bodies` | 本文を落とす | 使わなくなる（走査が落とす）。消す |
| shellread.py `_split_commands` | コマンドを切る | 予約語を 1 本にする |
| shellread.py `REASON_*` | 縮退の理由 | 2 つ足す（§1.3） |
| shellread.py 冒頭と 63〜66 行目のコメント | 「コマンド置換の括弧も区切りに落ちるので何もしなくてよい」 | 引用の外でしか成り立たなかった。走査の説明に書き直す |
| judge.py `screen()` | `read()` の結果を subject にする | `bare` も返す（または record に持たせる）。deny / ask の理由を組むところで `bare` に当て直す |
| reasons.py `reason_for` | 理由の 1 件 | 引用の中の断り（§1.4）を足す引数 |
| reasons.py `unreadable` / `undeclared` | 縮退の断り | 2 つの理由の文、`case` の一言 |
| audit.py `Record` / 書き出し | 記録の欄 | `quoted` を足す |
| diagnose.py | `--test` の出力 | `quoted:` の行 |
| phase.py `commands()` / `exempt()` | ゲートの免除はコマンドが全部ラッパースクリプトであること | 置換や改行があるとコマンドが増え、免除から外れる。`sh ccnavi-git.sh commit -m "$(cat f)"` はゲートが閉じている間止まるようになる（厳しい側） |
| phase.py `forbidden()` | サブエージェントに許さない形 | `"$(sh ccnavi-ticket.sh done x)"` にも当たる（厳しい側） |
| phase.py `ticket_approval_rule` | `(^\|\x00\|[;&\|]\s*)…` | `echo "$(ccnavi --approve x)"`、`ls⏎ccnavi --approve x` が deny になる（今は ask。厳しい側） |
| selfguard.py `_WRITE_VERBS` / `_COPY_VERBS` | `(^\|\x00)(mv\|rm\|tee…)\b[^\x00]*` | 中身の書き込みに当たる。`echo "$(tee {root}/.ccnavi/common/rules.yml < x)"`、`cat x >(tee …)`、`ls⏎sed -i … rules.yml` が deny になる |
| selfguard.py リダイレクト先 `>[>\|&]* ?[^ \x00\x01]*` | 行き先の語 | `> "$(pwd)/.ccnavi/…"` が `> $/.ccnavi/…` になり当たる（今は穴）。式は変えない |
| ticket.py `guard_rules` のシェル側 | 状態の置き場への書き込み動詞 | selfguard と同じ式を使うので、中身の書き込みに当たる |
| builtin.py（ルールファイルが読めないときの既定） | glob `*git push*` `*rm -rf *` など | 中身にも当たる（厳しい側） |
| post.py / hookio.py | — | 変わらない |

### .ccnavi/common/rules.yml の Bash のルール

| ルール | 式 | 変わること |
|---|---|---|
| raw-git | `(^\|[^\w.-])(\S*[\\/])?git(\.exe)?\s` | 中身の git に当たる。`h="$(git rev-parse HEAD)"`、`cd "$(git rev-parse --show-toplevel)"` が deny（後者は誤検知。§3） |
| git-reset-hard / git-branch-force-delete | glob | 中身に当たる |
| credentials | `(^\|[ \\/])\.(env…)` | 中身の `cat .env` に当たる |
| recursive-delete | glob `*rm -rf *` | 中身に当たる |
| find-writes | `(^\|\x00)…find…\s[^\x00]*\s-(delete…)` | 中身、改行の後ろ、`then` の後ろ、`find $(pwd) … -delete` に当たる |
| heredoc | `<<` | 中身の heredoc に当たる。`commit -m "$(cat <<'EOF' …)"` が deny（親チケットで決めたとおり） |
| prefer-webfetch | `(^\|\x00)…(curl\|wget)…\s` | 中身、改行の後ろに当たる（ask） |
| ccnavi-git（allow） | glob `*ccnavi-git.sh*` | 変わらない。`sh ccnavi-git.sh commit -m "$(cat /tmp/msg)"` は今も後も allow。deny に当たる中身があれば deny が勝つ |
| prefer-read-grep / prefer-glob（allow） | `^…\s[^\x00]*$` | 置換、プロセス置換、改行を 1 つでも含むと外れる（ask） |

`ccnavi-git` の glob は、同じ行のどこかにラッパースクリプトの名前があれば allow になる（`sh ccnavi-git.sh status; sh evil.sh` も
今 allow）。この変更で広がりも狭まりもしないが、改行を区切りにすると `sh ccnavi-git.sh status⏎sh evil.sh` も同じ扱いに
なることは残る。範囲の外（ルールの書き方の問題）。

### tests/

既存のテストは全部そのまま通る（上）。直す必要があるのはテストの中の説明だけ。

| 場所 | 内容 |
|---|---|
| test_acceptance.py 303 `test_引用された記号を止めるのは許容した誤検知` | 「shlex は引用された << と素の << を同じ文字列で返し」の説明。判定と文面は変わらない（§1.3 の数の比べ方で保つ）。説明を「走査が heredoc と読まなかった `<<`」に直す |

足すテストは §4。

### 文書

| 場所 | 内容 |
|---|---|
| README.md 697〜732「Bash のコマンドは実行される部分だけを見る」 | 例に `echo "$(git push origin main)"`（止まる）、`grep -n "\$(git push)" f`（通る）、改行とプロセス置換を足す。「`$( )` の中は実行される」を引用の有無によらない書き方に |
| README.md 734〜「読み切れないとき」の表 | `unterminated-substitution` と `ambiguous-substitution` の行 |
| ccnavi.md §6.3 の読み方 1〜7 | 2 の前に走査の段を足す。4（トークンで heredoc の本文を落とす）を走査に移す。5 の「`$( )` の中身は独立したコマンドになる」を、切り出して後ろにつなぐ形に直す。予約語。記録の `quoted` |
| ccnavi.md §12.2 許容する誤検知 | §3 の誤検知と回避策の行 |
| ccnavi.md の ADR-0001（字句は shlex） | 走査を足した理由（§1.1）を追記するか、ADR を足すかは文書フェーズで決める |
| HANDOVER.md 377〜385 | heredoc の本文を行番号で落とす説明が古くなる |
| .ccnavi/common/rule-samples.yml | 組み込みのルールで守られていてエージェントは書けない。§4.3 の見本を足す下書きを、実装フェーズの終わりに利用者へ渡す |

## 3. 判定が変わる見本

`.ccnavi/common/rules.yml` と組み込みのルールで、今（main）と試作を並べた。`{root}` はルートの絶対パス。

### 変わるべきもの（課題と、足した範囲）

| 見本 | 今 | 変更後 |
|---|---|---|
| `echo "$(git push origin main)"` | ask（UNDECLARED） | deny（raw-git） |
| `grep -n "$(git push)" f` | **allow**（prefer-read-grep） | deny（raw-git） |
| ``grep -n "`rm -rf /tmp/x`" f`` | **allow**（prefer-read-grep） | deny（recursive-delete） |
| `h="$(git rev-parse HEAD)"` | ask | deny（raw-git） |
| `echo "$(rm -rf /tmp/x)"` | ask | deny（recursive-delete） |
| ``echo `find . -delete` `` | ask | deny（find-writes） |
| ``cat <<EOF⏎use `rm -rf /tmp/x`⏎EOF`` | deny（heredoc） | deny（recursive-delete、heredoc） |
| `grep -n x f⏎sh evil.sh` | **allow** | ask |
| `cat <(sh evil.sh)` | **allow** | ask |
| `grep x <(curl https://example.com)` | **allow** | ask（prefer-webfetch） |
| `find . -name x⏎python evil.py` | **allow**（prefer-glob） | ask |
| `grep -n a#b f; sh evil.sh` | **allow** | ask |
| `find $(pwd) -name x -delete` | ask | deny（find-writes） |
| `echo a#; git push origin main` | ask | deny（raw-git） |
| `curl https://example.com/#top; rm -rf /tmp/x` | ask（prefer-webfetch） | deny（recursive-delete） |
| `ls⏎find . -delete` | ask | deny（find-writes） |
| `echo hi⏎curl https://example.com` | ask（UNDECLARED） | ask（prefer-webfetch） |
| `if true; then find . -delete; fi` | ask | deny（find-writes） |
| `echo "$(ccnavi --approve x)"` | ask | deny（DENY_TICKET_APPROVAL_CLI） |
| `ls⏎ccnavi --approve x` | ask | deny（DENY_TICKET_APPROVAL_CLI） |
| `echo "$(tee {root}/.ccnavi/common/rules.yml < /tmp/x)"` | ask | deny（組み込み） |
| `cat /tmp/x >(tee {root}/.ccnavi/common/rules.yml)` | **allow** | deny（組み込み） |
| `ls⏎sed -i s/a/b/ {root}/.ccnavi/common/rules.yml` | ask | deny（組み込み） |
| `echo x > "$(pwd)/.ccnavi/common/rules.yml"` | ask | deny（組み込み） |

### 変わってはいけないもの

| 見本 | 判定（今も後も） |
|---|---|
| `echo $(git push origin main)` | deny（raw-git） |
| `echo $((1 << 2))` | ask（UNDECLARED） |
| `grep -n "git push" README.md` | allow（prefer-read-grep） |
| `grep -n '$(git push)' f` | allow |
| `grep -n "\$(git push)" f` | allow |
| ``grep -n "\`git push\`" f`` | allow |
| `echo hi # $(git push)` | ask |
| ``cat <<'EOF' > notes.md⏎$(git push) `rm -rf /tmp/x`⏎EOF`` | deny（heredoc だけ） |
| `cat <<'EOF' > notes.md⏎git push origin main⏎EOF⏎echo done` | deny（heredoc だけ） |
| `# git push origin main` | skip（nothing-to-run） |
| `cat /repo/README.md \| head -20` | ask |
| `grep -n "regex: '(>" f` | allow |
| `grep -n "<<EOF" /repo/README.md` | allow |
| `cat <<'EOF' > notes.md` | deny（PARSE_UNCERTAIN、heredoc） |
| `grep -n "<<" f` | deny（PARSE_UNCERTAIN、heredoc） |
| `cat a.txt⏎`（末尾の改行） | allow |
| `curl -s 'https://example.com/a#frag'` | ask（prefer-webfetch） |
| `export PATH="$(go env GOPATH)/bin:$PATH"` | ask |
| `eval "$(ssh-agent -s)"` | ask（PARSE_UNCERTAIN） |
| `sed -n "$(grep -n '^### レビュー' README.md \| cut -d: -f1),+60p" README.md` | ask |

### 増える誤検知と回避策

| 見本 | 今 | 変更後 | 回避策（どれも変更後に止まらないことを確かめた） |
|---|---|---|---|
| `sh .ccnavi/scripts/ccnavi-git.sh commit -m "$(cat <<'EOF'⏎fix: don't push⏎EOF⏎)"` | allow | deny（heredoc） | 本文を Write で置いて `commit -F <ファイル>`（allow）。改行を含む `-m "…"`（allow） |
| ``gh pr create --title t --body "$(cat <<'EOF'⏎- `rm -rf x` is gone⏎EOF⏎)"`` | ask | deny（heredoc） | `--body-file <ファイル>`（ask） |
| ``gh issue create --title t --body "use `git push` here"`` | ask | deny（raw-git）＋引用の中の断り | `` \` `` で書く、単一引用（どちらも ask） |
| ``gh issue create --title t --body "odd ` backtick and git push"`` | ask | deny（PARSE_UNCERTAIN） | shell でも構文エラー。本文を直す |
| `cd "$(git rev-parse --show-toplevel)"` | ask | deny（raw-git）＋引用の中の断り | `sh ccnavi-git.sh rev-parse --show-toplevel`（allow）で出して、値を次のコマンドに書く |
| `grep -rn foo "$(pwd)"`、`find "$(pwd)" -name x`、`cat "$(ls -t logs/git-*.log \| head -1)"` | allow | ask | 値をそのまま書く |
| `grep -n foo f⏎grep -n bar g` | allow | ask | Grep ツール。1 行ずつ打つ |
| `echo "$(case a in a) git push;; esac)"` | ask | deny（PARSE_UNCERTAIN） | スクリプトを Write で置いて `sh <ファイル>`（ask） |
| `echo "$(xargs echo < f)"` | ask | ask（PARSE_UNCERTAIN） | 同上 |

## 4. 受入テストに渡すこと

### 4.1 shell の実測と試作の読み

`M` を「呼ばれたら記録を残す関数」に置き換えて bash 3.2（`/bin/bash`）と zsh で走らせ、試作の読みで
`M` が `(^|\x00)` の直後に立つか（切り出すか）を並べた。1〜46 は親チケットの実測、47〜65 は足した範囲。
受入テストは「実行 → 切り出す」「実行しない → 切り出さないか縮退」「割れる → 縮退か切り出す」を確かめる。

| # | 分類 | 入力（M が中身） | bash 3.2 | zsh | 試作の読み |
|---|---|---|---|---|---|
| 01 | 基本 | `echo "$(M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 02 | 基本 | `` echo "`M`" `` | 実行 | 実行 | 切り出す（引用の中） |
| 03 | エスケープ | `echo "\$(M)"` | - | - | 切り出さない |
| 04 | エスケープ | `` echo "\`M\`" `` | - | - | 切り出さない |
| 05 | エスケープ | `echo "\\$(M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 06 | 単一引用 | `echo '$(M)'` | - | - | 切り出さない |
| 07 | 単一引用 | `` echo '`M`' `` | - | - | 切り出さない |
| 08 | ANSI-C | `echo $'$(M)'` | - | - | 切り出さない |
| 09 | 二重の中の' | `echo "it's $(M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 10 | コメント | `echo hi # $(M)` | - | - | 切り出さない |
| 11 | コメント | `echo a#$(M)` | 実行 | 実行 | 切り出す（引用の外） |
| 12 | heredoc | `` cat <<'EOF'⏎$(M) `M`⏎EOF `` | - | - | 切り出さない |
| 13 | heredoc | `` cat <<"EOF"⏎$(M) `M`⏎EOF `` | - | - | 切り出さない |
| 14 | heredoc | `` cat <<\EOF⏎$(M) `M`⏎EOF `` | - | - | 切り出さない |
| 15 | heredoc | `cat <<EOF⏎$(M)⏎EOF` | 実行 | 実行 | 切り出す（引用の中） |
| 16 | heredoc | `` cat <<EOF⏎use `M` here⏎EOF `` | 実行 | 実行 | 切り出す（引用の中） |
| 17 | commit形 | `` echo "$(cat <<'EOF'⏎fix: use `M` and $(M) (see §6)⏎don't⏎EOF⏎)" `` | - | - | 切り出さない |
| 18 | commit形 | `` echo "$(cat <<EOF⏎use `M`⏎EOF⏎)" `` | 実行 | 実行 | 切り出す（引用の中） |
| 19 | 入れ子 | `echo "$(echo "$(M)")"` | 実行 | 実行 | 切り出す（引用の中） |
| 20 | 入れ子 | `` echo `echo \`M\`` `` | 実行 | 実行 | 切り出す（引用の外） |
| 21 | パラメータ展開 | `echo "${x:-$(M)}"` | 実行 | 実行 | 切り出す（引用の中） |
| 22 | パラメータ展開 | `` echo "${x:-`M`}" `` | 実行 | 実行 | 切り出す（引用の中） |
| 23 | 算術 | `echo "$((1 + 2))"` | - | - | 切り出さない |
| 24 | 算術 | `echo "$(( $(M) + 1 ))"` | 実行 | 実行 | 切り出す（引用の中） |
| 25 | 算術風 | `echo "$( (M) )"` | 実行 | 実行 | 切り出す（引用の中） |
| 26 | プロセス置換 | `cat <(M)` | 実行 | 実行 | 切り出す（引用の外） |
| 27 | プロセス置換 | `echo "<(M)"` | - | - | 切り出さない |
| 28 | 中の) | `echo "$(case a in a) M;; esac)"` | - | 実行 | 縮退（ambiguous-substitution） |
| 29 | 中の) | `echo "$(echo "a)b"; M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 30 | 中の) | `echo "$(echo 'a)b'; M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 31 | 中の) | `echo "$(echo \")\"; M)"` | - | - | 切り出さない |
| 32 | 中の) | `echo "$(echo a # )⏎M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 33 | 中の単一引用 | `echo "$(echo '$(M)')"` | - | - | 切り出さない |
| 34 | 閉じない | `echo "$(M"` | - | - | 縮退（unterminated-quote） |
| 35 | 閉じない | `` echo "`M" `` | - | - | 縮退（unterminated-substitution） |
| 36 | here-string | `cat <<< "$(M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 37 | 複数行 | `echo "$(⏎M⏎)"` | 実行 | 実行 | 切り出す（引用の中） |
| 38 | 代入 | `h="$(M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 39 | 代入 | `h=$(M)` | 実行 | 実行 | 切り出す（引用の外） |
| 40 | 代入 | `` h=`M` `` | 実行 | 実行 | 切り出す（引用の外） |
| 41 | 引用の直後 | `echo "\"$(M)\""` | 実行 | 実行 | 切り出す（引用の中） |
| 42 | 配列添字 | `a=(1); echo "${a[$(M)]}"` | 実行 | 実行 | 切り出す（引用の中） |
| 43 | 旧算術 | `echo "$[1+2]"` | - | - | 切り出さない |
| 44 | リダイレクト先 | `echo x > "$(M)"` | 実行 | 実行 | 切り出す（引用の中） |
| 45 | for | `for f in "$(M)"; do :; done` | 実行 | 実行 | 切り出す（引用の中） |
| 46 | [[ ]] | `[[ "$(M)" = x ]]` | 実行 | 実行 | 切り出す（引用の中） |
| 47 | 改行 | `echo hi⏎M` | 実行 | 実行 | 切り出す（引用の外） |
| 48 | 改行 | `ls \|⏎M` | 実行 | 実行 | 切り出す（引用の外） |
| 49 | 改行 | `echo hi # x⏎M` | 実行 | 実行 | 切り出す（引用の外） |
| 50 | 改行 | `cat <<EOF⏎x⏎EOF⏎M` | 実行 | 実行 | 切り出す（引用の外） |
| 51 | 改行 | `cat <<'EOF'⏎x $(y)⏎EOF⏎M` | 実行 | 実行 | 切り出す（引用の外） |
| 52 | 改行 | `if true⏎then M⏎fi` | 実行 | 実行 | 切り出す（引用の外） |
| 53 | プロセス置換 | `diff <(echo a) >(M)` | 実行 | 実行 | 切り出す（引用の外） |
| 54 | プロセス置換 | `cat < <(M)` | 実行 | 実行 | 切り出す（引用の外） |
| 55 | 語の中の# | `echo a#b; M` | 実行 | 実行 | 切り出す（引用の外） |
| 56 | 語の中の# | `echo a#b⏎M` | 実行 | 実行 | 切り出す（引用の外） |
| 57 | 語の中の# | `echo $#; M` | 実行 | 実行 | 切り出す（引用の外） |
| 58 | 語の中の# | `echo ${#x}; M` | 実行 | 実行 | 切り出す（引用の外） |
| 59 | commit形 | `echo "$(cat <<'EOF'⏎fix: don't⏎EOF⏎)"; M` | - | 実行 | 切り出す（引用の外） |
| 60 | case | `case a in a) M;; esac` | 実行 | 実行 | 切り出す（引用の外） |
| 61 | heredoc終端の空白 | `cat <<EOF⏎x⏎ EOF⏎EOF⏎M` | 実行 | 実行 | 切り出す（引用の外） |
| 62 | heredoc 2 本 | `cat <<A <<B⏎a⏎A⏎b $(M)⏎B` | 実行 | 実行 | 切り出す（引用の中） |
| 63 | 引用の中の改行 | `echo "a⏎M"` | - | - | 切り出さない |
| 64 | 単一引用の中の# | `echo 'a #b'; M` | 実行 | 実行 | 切り出す（引用の外） |
| 65 | コメントの中の引用 | `echo hi # don't⏎M` | 実行 | 実行 | 切り出す（引用の外） |

59 は bash 3.2 が構文エラーにし（`$( )` の中の、引用した heredoc の本文に `'` がある形）、zsh は実行する。
切り出す側に読むのは厳しい側なので、そのまま受け入れる。Git Bash（bash 5 系）での取り直しは未実施（§6）。

### 4.2 入力 → 読み

`text` がルールを当てる文字列、`bare` が引用の中から切り出したものを除いた文字列。`␀` = `\x00`、`␁` = `\x01`。

| 入力 | text | bare | 縮退 |
|---|---|---|---|
| `echo "$(git push origin main)"` | `echo $␀git push origin main` | `echo $` | |
| `grep -n "$(git push)" f` | `grep -n $ f␀git push` | `grep -n $ f` | |
| `h="$(git rev-parse HEAD)"` | `h=$␀git rev-parse HEAD` | `h=$` | |
| `` echo `find . -delete` `` | `echo $␀find . -delete` | `echo $␀find . -delete` | |
| `echo $(git push origin main)` | `echo $␀git push origin main` | `echo $␀git push origin main` | |
| `echo $(git push) foo` | `echo $ foo␀git push` | `echo $ foo␀git push` | |
| `find $(pwd) -name x -delete` | `find $ -name x -delete␀pwd` | `find $ -name x -delete␀pwd` | |
| `cat <(sh evil.sh)` | `cat $␀sh evil.sh` | `cat $␀sh evil.sh` | |
| `diff <(ls a) >(tee f)` | `diff $ $␀ls a␀tee f` | `diff $ $␀ls a␀tee f` | |
| `grep -n x f⏎sh evil.sh` | `grep -n x f␀sh evil.sh` | `grep -n x f␀sh evil.sh` | |
| `echo a#b; git push` | `echo a#b␀git push` | `echo a#b␀git push` | |
| `if true; then find . -delete; fi` | `if␀true␀then␀find . -delete␀fi` | `if␀true␀then␀find . -delete␀fi` | |
| `! grep x f` | `!␀grep x f` | `!␀grep x f` | |
| `` cat <<EOF⏎use `rm -rf /tmp/x`⏎EOF `` | `cat << EOF␀rm -rf /tmp/x` | `cat << EOF` | |
| `cat <<'EOF' > notes.md⏎$(git push)⏎EOF⏎echo done` | `cat << EOF > notes.md␀echo done` | `cat << EOF > notes.md␀echo done` | |
| `sh .ccnavi/scripts/ccnavi-git.sh commit -m "$(cat <<'EOF'⏎fix: don't push⏎EOF⏎)"` | `sh .ccnavi/scripts/ccnavi-git.sh commit -m $␀cat << EOF` | `sh .ccnavi/scripts/ccnavi-git.sh commit -m $` | |
| `` gh issue create --body "use `git push` here" `` | `gh issue create --body use␁$␁here␀git push` | `gh issue create --body use␁$␁here` | |
| `echo "$(echo "$(git push)")"` | `echo $␀echo $␀git push` | `echo $` | |
| `echo "${x:-$(git push)}"` | `echo ${x:-$}␀git push` | `echo ${x:-$}` | |
| `echo "$(( $(git push) + 1 ))"` | `echo $␁(␁␁(␁␁$␁+␁1␁␁)␁␁)␁␀git push` | `echo $␁(␁␁(␁␁$␁+␁1␁␁)␁␁)␁` | |
| `grep -n "\$(git push)" f` | `grep -n \$␁(␁git␁push␁)␁ f` | `grep -n \$␁(␁git␁push␁)␁ f` | |
| `grep -n '$(git push)' f` | `grep -n $␁(␁git␁push␁)␁ f` | `grep -n $␁(␁git␁push␁)␁ f` | |
| `echo $'$(git push)'` | `echo $$␁(␁git␁push␁)␁` | `echo $$␁(␁git␁push␁)␁` | |
| `echo hi # $(git push)` | `echo hi` | `echo hi` | |
| `echo $((1 << 2))` | `echo` | `echo` | |
| `cat a.txt⏎` | `cat a.txt` | `cat a.txt` | |
| `grep -n "<<" f` | — | — | unterminated-quote |
| `echo "$(git push"` | — | — | unterminated-quote |
| `` echo "`git push" `` | — | — | unterminated-substitution |
| `echo "$(case a in a) git push;; esac)"` | — | — | ambiguous-substitution |
| `echo "$(xargs echo < f)"` | — | — | command-taken-as-code |
| `git␀push` | `git push` | `git push` | |

2 つの読みは、実装で直すか受入テストで決めること。

- `echo "$(( $(git push) + 1 ))"` の外側に `$((` の文字が語として残る（`_drop_arithmetic` は引用の中の算術を落とせない）。
  今も同じ（引用の中の算術は 1 語）で、判定には効かない
- `grep -n "\$(git push)" f` の `\$` の `\` が残る。shlex の二重引用の中のエスケープの扱いで、今も同じ

### 4.3 見本 → 判定

§3 の 3 つの表をそのまま使う。加えて次を確かめる。

- 引用の中の断り（§1.4）が、`gh issue create --title t --body "use `git push` here"` の deny には出て、
  `echo $(git push origin main)` の deny には出ないこと
- 記録の `quoted` が前者に `["raw-git"]`、後者には欄ごと無いこと
- `unterminated-substitution` と `ambiguous-substitution` の縮退で、断りの文が理由ごとに違うこと

## 5. 実装の手順の案

1. `ccnavi/shellread.py`: 試作（`wip/design/shellread-subst-proto.py`）の走査・`bare`・予約語・理由 2 つを入れる。
   冒頭と各関数のコメントを今の書き方に合わせて書き直す。`_drop_heredoc_bodies` を消す
2. `ccnavi/judge.py` / `reasons.py` / `audit.py` / `diagnose.py`: `bare` に当て直して断りを足し、記録と `--test` に `quoted` を出す
3. `tests/test_shellread.py`: §4.1 と §4.2 の表。`tests/test_acceptance.py`: §3 と §4.3。`test_引用された記号を止めるのは許容した誤検知` の説明を直す
4. 全テストと `tests/test_sh_portability.py`、ruff を回す。rule-samples.yml に足す見本の下書きを scratchpad に置いて利用者に渡す

## 6. やらないこと・残る限界

| 項目 | 理由 |
|---|---|
| 変数展開、alias、`eval` の中身 | §12.1。変わらない |
| 引用が演算子の文字だけの 1 語（`grep -n ">" f` は `>` に見える、`grep -n "<<" f` は縮退） | 走査は引用の範囲を知っているので直せるが、止まっていたものが通る方向の変更になる。別に相談する |
| `$'…\'…'` を shlex が閉じない引用と読んで縮退する | 既存の限界。走査は正しく読むが、shlex に渡した先で縮退する（厳しい側） |
| 応答の subject で空白が落ちる件 | 親チケットのとおり別件 |
| Git Bash（bash 5 系）での実測 | この機械には bash 3.2 と zsh しか無い。docker のイメージも手元に無く、取りに行くのは外へ出るので今回はしない。受入テストのフェーズか、Windows の機械で取り直す |
| zsh の対話モードで `INTERACTIVE_COMMENTS` が無いときの `#` | Claude Code のコマンドは非対話で走るので対象外 |
| `ccnavi-git` の glob が同じ行のどのコマンドにも allow を掛ける | §2 の表のとおり。ルールの書き方の問題で、この変更で広がりも狭まりもしない |

## 7. 試作と確かめ方

- 試作: `wip/design/shellread-subst-proto.py`（`ccnavi/shellread.py` と差し替えて動く全文）
- shell との突き合わせ: §4.1 の 65 形。試作で食い違い 0
- 既存のテスト: 作業ツリーの写しで 678 件、手を入れずに全部通る
- 判定の比較: §3 の見本を、写す前と後の写しで `main.py --test Bash` に通して並べた
