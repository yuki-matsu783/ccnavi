# shellread の印を分ける（設計）

課題: `ccnavi/shellread.py` は「シェルなら一致がまたげない場所」3 つに同じ印 `SEP = "\x00"` を置く。
rules.yml の regex からは 3 つが区別できず、`prefer-read-grep` の `[^\x00]*$`（後ろにコマンドが
続かない）が、引用の中の空白にも当たって外れる。(1) コマンドの間は `\x00` のまま、(2) 引用が
つないだ空白と (3) 語の中の演算子の両側を別の 1 文字にする。

## 1. 決めたこと

### 語の中の切れ目の文字: `\x01`

| 条件 | 根拠（作業ツリーの中で `uv run python -c` で確認） |
|---|---|
| 実際のコマンドラインが運べない | 制御文字。payload の JSON には `\u0001` と書けるが、`read()` が入力から取り除くので騙れない（`\x00` と同じ扱い。下の「入力からの除去」） |
| `\s` に当たらない | `re.search(r"\s", "\x01")` → `None` |
| `\w` に当たらない | `re.search(r"\w", "\x01")` → `None` |
| `_join` が印として扱わない | `"\x01".isspace()` → `False`、`"\x01" in "();<>|&"` → `False` |
| shlex が語の一部として残す | `shlex.shlex('grep -n a\x01b\x00c "x y" f', posix=True, punctuation_chars=True)`、`whitespace_split=True` で `['grep', '-n', 'a\x01b\x00c', 'x y', 'f']`。`lexer.whitespace` は `' \t\r\n'` だけで、`wordchars` にも `punctuation_chars` にも無いが、`whitespace_split` が真なので空白以外は全部語に付く。引用の中 `"c\x01 d"` も `'c\x01 d'` で残る |

名前は `SEP = "\x00"` をそのまま残し（意味を「コマンドの切れ目」に絞る）、`WORD_SEP = "\x01"` を足す。
`SEP` の綴りを変えないのは、`tests/test_shellread.py` と `phase.py` の `split("\x00")` がコマンドの
区切りの意味で使っていて、そちらは変更後も同じ判定だから。

### 入力からの除去

- 今の場所: `ccnavi/shellread.py` `read()` の先頭、`src = src.replace(SEP, " ")`（102 行目）。
  行継続の除去（109 行目）と `_tokenize` より前で、shlex に渡す前の唯一の入口
- 方針: 同じ行で `WORD_SEP` も空白に置き換える。`src = src.replace(SEP, " ").replace(WORD_SEP, " ")`
- 効果: 入力 `git\x01push` は今 `git\x01push` の 1 語（どのルールにも当たらない。実在しない
  コマンド名なので害も無い）だが、変更後は `git push` と読まれ raw-git に当たる。厳しい側

### 3 つの置き先と文字の対応

| 置き先 | 例 | 今 | 変更後 |
|---|---|---|---|
| (1) コマンドとコマンドの間（`_render` の `SEP.join`） | `cd /repo && git push` → `cd /repo␀git push` | `\x00` | `\x00` のまま |
| (2) 引用が 2 語を 1 語につないだ空白（`_join` の `isspace`） | `grep -n "git push" f` → `grep -n git␁push f` | `\x00` | `\x01` |
| (3) 語の中の演算子の文字の両側（`_join` の `_PUNCTUATION`） | `grep -n "x>y" f` → `grep -n x␁>␁y f` | `\x00` | `\x01` |
| 引用が演算子だけの 1 語 | `grep -n ">" f` → `grep -n > f` | 演算子 | 演算子のまま（shlex が引用の有無を返さない。今どおり） |
| 引用が `<<` だけの 1 語 | `grep -n "<<" f` | degraded（unterminated-quote） | 変わらず。許容した誤検知（ccnavi.md §12.3、tests/test_acceptance.py 299 行目） |

`_join` の中の 2 か所（313 行目 `out.append(SEP)`、315 行目 `out.append(SEP + c + SEP)`）を
`WORD_SEP` に変えるだけ。`_render`（299 行目）は `SEP` のまま。

## 2. 影響の洗い出し

`\x00` と `SEP` の使用箇所。「区切り」= コマンドの区切りの意味だけ（変更後も同じ判定）、
「語の中」= 語の中の切れ目まで含めて使っている（判定が変わる）。判定の変化は隔離した
スクリプトで `_join` だけ差し替えて実測した。

### ccnavi/

| 場所 | 使い方 | 区分 | 根拠 |
|---|---|---|---|
| shellread.py:42 `SEP = "\x00"` | 定義 | 区切り | 名前と値はそのまま。`WORD_SEP` を隣に足す |
| shellread.py:102 `src.replace(SEP, " ")` | 入力からの除去 | 語の中 | `WORD_SEP` も同じ行で取り除く。`git\x01push` が `git push` と読まれるようになる（厳しい側） |
| shellread.py:299 `SEP.join(...)` | コマンドの連結 | 区切り | (1) の置き先そのもの。変えない |
| shellread.py:313, 315 `out.append(SEP…)` | 語の中の空白・演算子 | 語の中 | (2)(3) の置き先。`WORD_SEP` に変える |
| shellread.py:28-41 冒頭コメント | 説明 | — | 「置く先は 3 つ」を「印は 2 つ」に書き直す |
| phase.py:70 `subject.split("\x00")` | `commands()`。ゲートの免除と子の禁止形をコマンド 1 本ずつに当てる | 語の中 | 今は引用の空白でも割れる。`sh .claude/scripts/ccnavi-git.sh commit -m "docs: a b"` は `['sh … -m docs:', 'a', 'b']` の 3 本になり `exempt()` が偽。変更後は 1 本で `exempt()` が真。ゲートが閉じている間、引用に空白を含むラッパ呼び出しが通るようになる（緩む方向だが、これが本来の読み。§3 に見本） |
| phase.py:83 `forbidden()`（`_FORBIDDEN_COMMAND`） | 同じ `commands()` を使う | 区切り | `(sh\|bash)\s+` は `\x01` をまたげない。`echo "sh ccnavi-ticket.sh done x"` は今も変更後も偽、`ls; sh … done x` は今も変更後も真 |
| phase.py:38 `_EXEMPT_COMMAND` | `^(sh\|bash)\s+\S*ccnavi-…\.sh(\s\|$)` | 区切り | `\x00` を含まない。`commands()` の割り方が変わる影響は上の行 |
| phase.py:93 `ticket_approval_rule` `(^\|\x00\|[;&\|]\s*)…\s+[^\x00]*{_CLI_FORMS}` | `(^\|\x00)` はコマンドの先頭、`[^\x00]*` は同じコマンドの中 | 語の中 | `uv run python -m ccnavi --rules r.yml --test Bash "ccnavi --approve x"` が今は当たらず、変更後は `[^\x00]*` が `ccnavi␁--approve` をまたいで deny になる（厳しい側。`--test` の対象に承認の綴りを書く形だけ） |
| selfguard.py:170-171 コメント | 説明 | 区切り | 「`\x00` はコマンドの切れ目」の説明は変更後も正しい。語の中の印が別にあることを 1 行足す |
| selfguard.py:177 `>[>\|&]* ?[^ \x00]*` | リダイレクトの行き先 | 語の中 | **直す必要あり**。`grep -n "> /repo/.claude/ccnavi/rules.yml" f` は今 `␀>␀␀/repo/…` で当たらないが、変更後 `␁>␁␁/repo/…` は `[^ \x00]*` が `␁␁/repo/.claude/…` を食って deny になる（誤検知が増える）。`[^ \x00\x01]*` にする。素の `echo x > /repo/.claude/ccnavi/rules.yml` は `> ` の後ろが `/repo` なので今どおり当たる |
| selfguard.py:178 `(^\|\x00)(mv\|rm\|tee\|…)\b[^\x00]*` | 書き換える動詞から行き先まで | 語の中 | `tee "a b" /repo/.claude/ccnavi/rules.yml` は今 `tee a␀b …` で `[^\x00]*` が止まり当たらない（穴）。変更後は deny。厳しい側 |
| selfguard.py:179 `(^\|\x00)sed\b[^\x00]*-i[^\x00]*` | 同上 | 語の中 | `sed -i "s/a b/c/" /repo/.claude/ccnavi/rules.yml` が今は通り、変更後は deny。厳しい側 |
| selfguard.py:181 `_COPY_VERBS` `(^\|\x00)(cp\|ln\|install)\b[^\x00]*` | 同上 | 語の中 | `cp "a b" /repo/.claude/ccnavi/rules.yml` が今は通り、変更後は deny。厳しい側 |
| selfguard.py:206 `[^ \x00]*($\|\x00)` | 写す動詞の行き先が最後の引数 | 区切り | `($\|\x00)` はコマンドの終わり。`[^ \x00]*` は `\x01` を含みうるが、行き先の名前に引用の空白がある形が当たる側に倒れるだけ |
| selfguard.py:222 `[^\\/ \x00]+` | プロジェクト名 1 語 | 区切り | 同上。`\x01` を含む名前は当たる側 |
| ticket.py:712 `[^\\/\s\x00]+` | 置き場の綴りのプロジェクト名 | 区切り | 同上 |
| ticket.py:751 `[^ \x00]*($\|\x00)` | 写す動詞の行き先 | 区切り | 同上 |
| ticket.py:752 `_WRITE_VERBS` / `_COPY_VERBS` を使う | 状態の置き場を守るシェル側のルール | 語の中 | selfguard.py:178-181 と同じ理由で `mv "a b" wip/tickets/done/` が今は通り、変更後は deny。厳しい側 |
| judge.py:415-419 `screen()` | `shellread.read` の結果をそのまま subject にする | — | `\x00` を直接は見ない。返る文面（reasons.py の `subject:` 行）に `\x01` が混ざるようになるが、`\x00` が混ざるのと同じ |
| post.py | — | — | `\x00` も `shellread` も使っていない |
| reasons.py / diagnose.py / audit.py | `degraded` の理由だけ | — | 印は見ない |

### .claude/ccnavi/rules.yml

| ルール | regex の `\x00` | 区分 | 根拠 |
|---|---|---|---|
| raw-git（51） | 無し。`(^\|[^\w.-])…git(\.exe)?\s` | 区切り | `\x00` も `\x01` も `[^\w.-]` に当たる。`grep -n x"git" push` のような形は今も変更後も同じ当たり方 |
| find-writes（91）`(^\|\x00)…find…\s[^\x00]*\s-(delete\|…)\b` | 先頭は区切り、`[^\x00]*` は語の中 | 語の中 | `find /repo -name "a b" -delete` が今は当たらず（穴）、変更後は deny。厳しい側 |
| prefer-webfetch（158）`(^\|\x00)…(curl\|wget)…\s` | 先頭だけ | 区切り | `echo "a b" \| curl -d @- x` は今も変更後も ask |
| prefer-read-grep（184）`…\s[^\x00]*$` | 後ろにコマンドが続かない | 語の中 | **課題そのもの**。`grep -n "git push" README.md` が allow になる。`cat f \| head` は `␀` が残るので今どおり当たらない |
| prefer-glob（194）`…find…\s[^\x00]*$` | 同上 | 語の中 | `find /repo -name "a b"` が allow になる（`-delete` 付きは deny が先） |
| heredoc（99）`<<` | 無し | 区切り | `grep -n "<<EOF" f` は `␁<␁␁<␁EOF` で今も変更後も当たらない |
| recursive-delete / git-reset-hard / git-branch-force-delete（glob） | 無し | 区切り | 引用の中は `␁` で繋がるので `rm -rf ` の空白に当たらない。今と同じ |
| credentials（77） | 無し。`(^\|[ \\/])\.(env…)` | 区切り | `\x00` も `\x01` も `[ \\/]` に無い。同じ |

### tests/

| 場所 | 使い方 | 区分 | 根拠 |
|---|---|---|---|
| test_shellread.py:3 `SEP` を import | — | 区切り | `WORD_SEP` も import する |
| test_shellread.py:9 `show()` `replace(SEP, "<join>")` | 失敗メッセージの可視化 | 語の中 | `WORD_SEP` も別の綴り（`<word>` など）で見えるようにする |
| test_shellread.py:26-27 `"cd /repo" + SEP + "git push"` | コマンドの区切りの期待 | 区切り | 変更後も `\x00` |
| test_shellread.py:38-48 `assertNotIn("git push", …)` | 引用の空白が空白でないこと | 区切り | `␁` でも真 |
| test_shellread.py:62-74 `assertNotIn("> rules.yml" / "x>y" / "<<")` | 語の中の演算子の両側に印 | 区切り | `␁` でも真 |
| test_shellread.py:140-142 `git\x00push` を持ち込めない | 入力からの除去 | 語の中 | `git\x01push` の行を足す |
| test_acceptance.py:203 `grep -n "git push" README.md` は stdout 空 | tests/fixtures/rules.yml で判定 | 区切り | fixture に prefer-read-grep は無い（`\x00` を使うルールも無い）ので同じ |
| test_acceptance.py:299-311 `grep -n "<<" README.md` は deny・raw text | 許容した誤検知 | 区切り | degraded は変わらない |
| test_test_json.py / vscode-extension/…/fixtures/test.json:24 `cd /repo\u0000git push` | 返る文面の中の `\x00` | 区切り | コマンドの区切り。同じ |

### 文書

| 場所 | 内容 | 区分 |
|---|---|---|
| README.md:612-620「引用が 1 語につないだ空白は…」「語の中に入った `>` …」 | 2 種類の印があることは書いていないが、`grep -n "git push"` が「通る」と書いてある。allow に当たるようになった旨（Read / Grep の勧めが出る）を 1 文足す | 語の中 |
| ccnavi.md:994-997「前者は両側に区切りの印を置いて渡す」 | 「区切りの印」を「語の中の印（コマンドの区切りとは別の文字）」に直す | 語の中 |
| ccnavi.md:999-1006、README.md:619-620 `grep -n "<<"` の限界 | 変わらない | 区切り |
| requirements.md | `\x00` にも印の文字にも触れていない | — |
| .claude/ccnavi/rule-samples.yml:213-229 | ask 節の引用付き grep 4 件（`"git push"`、`"rm -rf"`、`"regex: '(>"`、`"<<EOF"`）。`why` に「権限モードへ渡る」と書いてある | 語の中。allow 節へ移し、why を書き直す |
| .claude/skills/ccnavi-config | `\x00` に触れていない | — |

## 3. 判定が変わる見本

判定は `.claude/ccnavi/rules.yml` と組み込みルールで。

### 変わるべきもの

| 見本 | 今 | 変更後 | 根拠 |
|---|---|---|---|
| `grep -n "git push" README.md` | UNDECLARED（ask） | allow（prefer-read-grep） | 課題そのもの |
| `grep -n "rm -rf" /repo/.claude/ccnavi/rules.yml` | UNDECLARED | allow | 同上 |
| `grep -n "regex: '(>" /repo/.claude/ccnavi/rules.yml` | UNDECLARED | allow | 同上。`>` の両側は `␁` |
| `grep -n "<<EOF" /repo/README.md` | UNDECLARED | allow | heredoc の `<<` は `␁<␁␁<␁` で当たらない |
| `cat "a b.txt"` | UNDECLARED | allow | 引用の空白 |
| `find /repo -name "a b"` | UNDECLARED | allow（prefer-glob） | 同上 |
| `find /repo -name "a b" -delete` | UNDECLARED（穴） | deny（find-writes） | `[^\x00]*` が語をまたげるようになる |
| `sed -i "s/a b/c/" /repo/.claude/ccnavi/rules.yml` | UNDECLARED（穴） | deny（組み込み selfguard） | 同上 |
| `tee "a b" /repo/.claude/ccnavi/rules.yml` | UNDECLARED（穴） | deny | 同上 |
| `cp "a b" /repo/.claude/ccnavi/rules.yml` | UNDECLARED（穴） | deny | 同上 |
| `mv "a b" wip/tickets/done/` | UNDECLARED（穴） | deny（状態の置き場） | 同上 |
| `uv run python -m ccnavi --rules r.yml --test Bash "ccnavi --approve x"` | UNDECLARED | deny（ticket-approval） | `[^\x00]*` が `ccnavi␁--approve` をまたぐ。厳しい側。試すなら `--test` の対象をファイルに逃がす |
| ゲートが閉じている間の `sh .claude/scripts/ccnavi-git.sh commit -m "docs: a b"` | DENY_PHASE_GATE（`commands()` が 3 本に割る） | 免除（1 本） | phase.py:70。緩む方向なので受入テストで固定する |

### 変わってはいけないもの

| 見本 | 判定 | 根拠 |
|---|---|---|
| `cat /repo/README.md \| head -20` | UNDECLARED（allow に当たらない） | `␀` はそのまま。`[^\x00]*$` が止まる |
| `grep -n ">" f` | allow（`grep -n > f`。演算子扱い） | 引用だけの 1 語は今どおり |
| `grep -n ">" /repo/.claude/ccnavi/rules.yml` | deny（リダイレクトに見える） | 今と同じ許容した誤検知 |
| `grep -n "<<" README.md` | deny（PARSE_UNCERTAIN、raw text） | degraded は変わらない |
| `grep -n "> /repo/.claude/ccnavi/rules.yml" f` | UNDECLARED | selfguard.py:177 を `[^ \x00\x01]*` に直す前提。直さないと deny に変わる |
| `echo $(git push origin main)` | deny（raw-git） | `$␀git push …`。`(` は演算子で区切り |
| `cd /repo && git push` | deny | 区切りは `␀` のまま |
| `echo x>f` | `echo x > f`。deny には当たらない | 素の演算子は空白付きで残る |
| `echo "a b" > /repo/.claude/ccnavi/rules.yml` | deny | 行き先は素のリダイレクト |
| `echo "a b" \| curl -d @- x` | ask（prefer-webfetch） | `\|` の区切りは `␀` |
| `grep "-c" f && rm -rf /x` | deny（recursive-delete） | 同上 |
| `echo "sh ccnavi-ticket.sh done x"`（子） | 禁止形に当たらない | `(sh\|bash)\s+` が `␁` をまたげない |
| `cat "/home/u/.env"` | deny（credentials） | 引用の中身はそのまま |
| `git` + `\x00` + `push`（入力に印を混ぜる） | deny（`git push` と読む） | 除去は今どおり |

## 4. 受入テストに渡す対応表

### 入力 → `shellread.read().text`（変更後）。`␀` = `\x00`、`␁` = `\x01`

| 入力 | 返る文字列 |
|---|---|
| `cd /repo && git push` | `cd /repo␀git push` |
| `echo hi; git push` | `echo hi␀git push` |
| `grep -n "git push" README.md` | `grep -n git␁push README.md` |
| `grep -n "x>y" notes.md` | `grep -n x␁>␁y notes.md` |
| `grep -n "regex: '(>" rules.yml` | `grep -n regex:␁'␁(␁␁>␁ rules.yml` |
| `grep -n "<<EOF" README.md` | `grep -n ␁<␁␁<␁EOF README.md` |
| `grep -n ">" f` | `grep -n > f` |
| `echo x>f` | `echo x > f` |
| `cmd 2>&1` | `cmd 2 >& 1` |
| `echo "a b" \| curl -d @- x` | `echo a␁b␀curl -d @- x` |
| `echo $(git push origin main)` | `echo $␀git push origin main` |
| `git` + `\x01` + `push` | `git push` |
| `git` + `\x00` + `push` | `git push` |
| `grep -n "<<" f` | degraded、reason = `unterminated-quote` |

### 見本 → 判定（変更後）。ルールは `.claude/ccnavi/rules.yml` + 組み込み

| 見本（tool: Bash） | 判定 | 当たるルール |
|---|---|---|
| `grep -n "git push" README.md` | allow | prefer-read-grep |
| `grep -n "rm -rf" /repo/.claude/ccnavi/rules.yml` | allow | prefer-read-grep |
| `grep -n "regex: '(>" /repo/.claude/ccnavi/rules.yml` | allow | prefer-read-grep |
| `grep -n "<<EOF" /repo/README.md` | allow | prefer-read-grep |
| `find /repo -name "a b"` | allow | prefer-glob |
| `find /repo -name "a b" -delete` | deny | find-writes |
| `sed -i "s/a b/c/" /repo/.claude/ccnavi/rules.yml` | deny | 組み込み（selfguard） |
| `grep -n "> /repo/.claude/ccnavi/rules.yml" f` | allow | prefer-read-grep（selfguard の行き先の式を直した後） |
| `cat /repo/README.md \| head -20` | UNDECLARED（ask） | 無し |
| `grep -n "<<" README.md` | deny（PARSE_UNCERTAIN） | 無し（生の文字列に heredoc が当たる） |
| `echo $(git push origin main)` | deny | raw-git |
| `echo "a b" \| curl -d @- x` | ask | prefer-webfetch |
| `git` + `\x01` + `push` | deny | raw-git |

## 5. 実装の手順の案

1. `ccnavi/shellread.py`: `WORD_SEP = "\x01"` を足し、`read()` の除去と `_join` の 2 か所を変え、冒頭コメントを「印は 2 つ」に書き直す。`tests/test_shellread.py` に `show()` の可視化・`git\x01push` の除去・§4 の対応表を足す
2. `ccnavi/selfguard.py:177` のリダイレクトの行き先を `[^ \x00\x01]*` にし、170-171 のコメントに語の中の印を 1 行足す。`tests/test_selfguard.py` に `grep -n "> …rules.yml" f` が通ること、`sed -i "s/a b/c/" …rules.yml` が止まることを足す
3. `.claude/ccnavi/rule-samples.yml`: ask の引用付き grep 4 件を allow へ移して why を書き直し、`find … "a b" -delete`（deny）と `cat f | head`（ask、既にある）を確かめる。`uv run python -m ccnavi --test-samples .claude/ccnavi/rule-samples.yml` で食い違い 0。rules.yml 自体は変えない（`[^\x00]*` の意味が「コマンドの終わりまで」に狭まるだけ）
4. `tests/test_ticket.py`（ゲートの免除）に `-m "docs: a b"` 付きのラッパ呼び出しが免除されることを足す
5. README.md 612-620 の段落（「引用が 1 語につないだ空白は…」）に、引用付きの grep / cat が allow の `prefer-read-grep` に当たるようになった旨と、印が「コマンドの間」と「語の中」で別であることを足す。ccnavi.md 996 の「区切りの印」を「語の中の印」に直す
