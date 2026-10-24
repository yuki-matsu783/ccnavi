"""組み込みの既定ルール。

deny（取り返しの付かない操作を止めるルール）は、共通レイヤーと config の有無・状態によらず
常に適用される土台（`load_base`）。`Read` を通す allow まで含めた既定の全部を
使うのは、共通レイヤーと config が両方無いときと、共通レイヤーが読めないとき。
以下は、読めないときの扱いの理由。

## なぜ既定が要るか

ルールファイルが破損している、あるいはまだ無いときに拒否にすると、
破損したファイルを直すための呼び出しまで止まって回復できなくなる。既定モードが
block なので、ファイルを置く前に hook を登録した時点でセッションの呼び出しが全部止まる。
「設定が読めない」は「判断できない」ではなく「設定が破損している」であり、
扱いを分ける必要がある（要件の例外）。

## 何を入れて、何を入れないか

ここに入れるのは、取り返しの付かない操作だけに絞る。ガードが機能していない間は
ルールの一覧が信用できないので、広く止めても「なぜ止まったのか」をユーザが
確かめる手立てが無い。止める数ではなく、止めそこねたときに戻せないものを見る。

入れないもの: ガード自身の設定そのものの保護（hooks・settings・scripts・共通の設定 3 本への
名前で書くツールの保護）。これは自己防衛の設定（`CCNAVI_GUARD_CORE_FILES`）で切れる側なので、
常に効く土台には置かない。`selfguard_shell.add_named_rules` が、ルールファイルが読めるかどうかに
よらず、設定が disable でない間だけ足す。

読めない間の修復は、エージェントが共通のルールファイルを Write / Edit で直接直す形では通さない。
読めない間も `builtin:guard-ccnavi-config` は外れず、Write / Edit は止まる。直し方は 1 つで、
`wip/handoff/.ccnavi/config/common/rules.yml` に下書きを置き（置き場は例外で書ける）、ユーザが端末の
`ccnavi-user-handoff.sh` で取り込むか、手で直す。今の中身が読めない rules.yml の取り込みは、
`handoff._defense_change` が「確かめられないので端末に限る」と扱う。そのため、
ボードからの取り込みは断られる。
ルールを書き換えて保護の甘い既定に戻す手順は、シェルからも名前で書くツールからも成り立たない。

入れるもの: Bash からガード自身の設定へ書き込む形（`builtin:guard-config-via-bash`）。
ルールファイルが読めないと既定に戻るので、シェルの書き込みで壊した結果として保護の甘い
既定に戻る、という手順を成り立たせないために、読めない間も止める。

止めるのは書き込む形だけで、場所の名前が出たかどうかでは止めない。
同じ場所には `selfguard_shell.add_rules` の組み込みも当たる（ルールファイルが読める間の
`builtin:guard-setting-files`）。ルールファイルの側にシェルからの書き込みを止める 1 本は
無いので、ここが外れているときに残る保護はこれだけになる。
場所の名前で止めると、`git add <パス>` も `git restore --ours -- <パス>` も止まる。
どちらもファイルの中身を書かないのに。

これが問題になるのはマージの衝突を解くとき。衝突マーカーの入ったルールファイルは
YAML として読めないので既定に戻る。そこで解決の手が止まると、ガードが機能しない
状態から出られなくなる。名前が出たら止める形は、ガードを直したいときほど強く効いてしまう。

シェルから入るのは、既にコミットされている内容だけになる。新しい文面は
下書きに置いてユーザが取り込む。壊す側（シェルの書き込み）と直す側（下書きとユーザの取り込み）が
別の経路なので、その狙いは保たれている。
書き込む形をここで数え漏らしても、既定はプロジェクトの `allow` を持たないので、
当たらなかった呼び出しは権限モードに渡り、ユーザが居るモードなら 1 件ずつ確認が出る。

## 既定に戻ると、ほとんどを権限モードに委ねる

どのルールも言及しない呼び出しについて、ccnavi は判定を持たず、Claude Code の
権限モードに従う（cli.py）。既定にはプロジェクトの `allow` が入っていないので、
既定を使っているあいだは、書き込みもシェルもほとんどがそこへ渡る。
ユーザが居るモードなら 1 件ずつ確認が出る。これは不具合ではなく、そのほうがよい。
ガードが機能していない間に状態を変える操作は、ユーザが見ているべきものになる。

既定に戻ったこと自体は、通した回にも毎回言う。委ねる先が判断してしまうモードでは、
何も言わないと誰も気づかないまま既定のルールで走り続けることになる。

`Read` だけは通す。作業ツリーを変えようがないうえ、いちばん数が多い。
ここまで確認を出すと、本当に見てほしい 1 件がその中に埋もれる。
"""

from __future__ import annotations

from ..infra import settings
from . import rules, selfguard_shell


def rule_data(root: str, conf: settings.Settings) -> dict:
    """組み込みの既定ルール。ファイルから読むルールと同じ形で、同じ `rules.parse` を通す。

    シェルの書き込みと照合する場所は、呼び出しごとに実際の設定から組む。実行ファイル・
    ccnavi ディレクトリ・共通レイヤーの 3 本は設定で動くので、空の設定で 1 度だけ組んだ形だと、
    動かしたワークスペースではルールファイルが破損したときにだけ保護が外れる。

    設定は省けない。省ける形にしておくと、渡し忘れた呼び出しがその弱い形のまま気づかないうちに動く。
    """
    shell = selfguard_shell.guard_shell_regex(
        root, conf.bin, conf.project_home, selfguard_shell.common_layer_files(conf)
    )
    return {
        "version": rules.VERSION,
        "deny": [
            _config_via_bash(shell, settings.git_command(root), settings.has_git_wrapper(root)),
            *_deny(root),
        ],
        "allow": _ALLOW,
    }


def base_data(root: str = "") -> dict:
    """常に有効な組み込みの deny（土台）。取り返しの付かない操作を止めるもので、設定に依らない。

    共通レイヤーと config の有無・状態によらず適用し、共通レイヤーと config のルールはその上に足す。
    `rule_data` の deny のうち、設定で動かない残り（`_deny`）と同じもの。
    `Read` だけ通す allow はここに入れない。あれは組み込みの既定だけで判定するとき
    （共通レイヤーと config が両方無い・共通レイヤーが破損している）に限る。

    root は文面の git の打ち方を決めるだけで、止める形は変えない（`settings.git_command`）。
    """
    return {"version": rules.VERSION, "deny": _deny(root)}


def load_base(root: str = "") -> tuple[rules.RuleSet, list[rules.Problem]]:
    """常に有効な組み込みの deny を組み立てる。`load` と同じく、問題はこのビルドの不備。"""
    built, problems = rules.parse(base_data(root), builtin=True)
    for rule in built.all():
        rule.base = True
    return built, problems


def read_allow() -> list[rules.Rule]:
    """組み込みの既定の `Read` を通す allow。共通レイヤーと config が両方無いときに足す。"""
    built, _ = rules.parse({"version": rules.VERSION, "allow": _ALLOW}, builtin=True)
    return built.allow


CONFIG_VIA_BASH_RULE_ID = "builtin:guard-config-via-bash"


def _config_via_bash(regex: str, git: str = "git", wrapper: bool = False) -> dict:
    # 競合の解き方の一文は、ラッパーがあるときだけ書く。無いときは生の git が止まるので、
    # 打てる形を案内せず、ユーザに頼む。
    resolve = (
        "マージの衝突を解くことは止めていません。"
        f"{git} add <パス>、{git} restore --ours -- <パス>（または --theirs）は、"
        "コミット済みの版を戻すだけなので使えます。"
        if wrapper
        else "マージの衝突を解くことは、ここではエージェントが git を実行しないため、"
        "ユーザに頼んでください。"
    )
    return {
        "id": CONFIG_VIA_BASH_RULE_ID,
        "match": "Bash",
        # 照合する形は selfguard と同じものを 1 か所から取る（`guard_shell_regex`）。
        # 書き写すと既定のほうだけが弱くなり、ルールファイルを破損させることが
        # そのまま保護を弱めることになる。
        "regex": regex,
        "message": (
            "ルールファイルを読めなかったため、ccnavi は組み込みの既定で動いています。"
            "シェルで書き込むのは適切ではありません。"
            "共通のルールファイルは Write / Edit でも直せません。"
            "wip/handoff/.ccnavi/config/common/rules.yml に下書きを置き、"
            "ユーザに端末の 'ccnavi-user-handoff.sh' での取り込みを頼んでください。"
            "ユーザが差分を見て取り込めば直せます。" + resolve
        ),
    }


# 表記違いに外れないように、止める形は glob の文字列一致ではなく regex で書く。
# glob（`*git push*` `*rm -rf *`）だと、`git -C . push`・`git --no-pager push`・`rm -fr`・
# `rm -r -f`・`rm --recursive --force` が外れる。どの式も先読みを使わない（ルールファイルの
# regex と同じ制約。rules.parse を通す）。
#
# どの式も、照合にかかる時間が入力の長さにほぼ比例する形で書く。判定の期限を確かめるのは
# ルールの合間だけなので、1 本の照合が後戻りで長引くと、期限に気づかないまま hook の timeout に
# 達し、呼び出しは止められずに通る。そのために次の 3 つを守る。
#
# - 語を読む繰り返しは所有的（`*+`）にし、どの語をどの枠で読むかが 1 通りに決まる形にする
# - 照合の始まりが多い入力（`rm rm rm …`）で、始まりごとに残りを読み直さない。読み直すのは、
#   ある始まりで当たるなら、もっと前の始まりでも当たる式のとき（rm と reset）。そのときは
#   コマンドの切れ目から最初の始まりまでを 1 度だけ探し、そこに決める（アトミックグループ）
# - パスの前置き（`/usr/bin/git` の `/usr/bin/`）は読まない。`/` も前の切れ目の字なので、
#   名前の直前の `/` で当たる

# 名前の前の切れ目。`legit` のような語や、誘導先の `ccnavi-git.sh` には当たらない。
_BEFORE = r"(?:^|[^\w.-])"
# 名前の後ろの切れ目。`push.default` や `git-pushx` のような続きの語には当たらない。
_AFTER = r"(?:[^\w.-]|$)"
# 語の終わり。`\x00` はコマンドの切れ目なので、次のコマンドへまたがない。
_WORD_END = r"(?:[\s\x00]|$)"
# コマンドの切れ目から、名前の前の切れ目までを読む。最初の始まりに決めるときに使う。
# `??` で、切れ目の直後に名前がある形を先に見る。
_FIRST = r"(?:^|\x00)(?>(?:[^\x00]*?[^\w.\x00-])??"

# 語の中の、名前の前の切れ目までを読む。語の頭か、切れ目の字の直後に名前があることを表す。
_IN_WORD = r"(?:[^\s\x00]*?[^\w\s\x00.-])??"

# ---- git
#
# 語の中の字を、名前の前の切れ目になる字（`_ANCHOR`）とそれ以外（`_PLAIN`）に分ける。
_PLAIN = r"[\w.-]"
_ANCHOR = r"[^\w\s\x00.-]"


def _plain_but(c: str) -> str:
    """`_PLAIN` のうち、文字 c（大文字小文字を問わない）を除いたもの。"""
    return rf"(?:[^\W{c}]|[.-])"


# 最後の切れ目の後ろが `git` / `git.exe` でない語の、その後ろの部分（空でない）。
# 選択肢は長いほうから並べる。短いほう（`g`）を先に置くと、`gitx` の頭の `g` だけで当たったことに
# なり、所有的な繰り返しが読み直さないので、値の残り（`itx`）で読みが止まる。
_TAIL = (
    r"(?:"
    rf"git\.exe{_PLAIN}++"
    rf"|git\.ex(?:{_plain_but('e')}{_PLAIN}*+)?"
    rf"|git\.e(?:{_plain_but('x')}{_PLAIN}*+)?"
    rf"|git\.(?:{_plain_but('e')}{_PLAIN}*+)?"
    rf"|git[\w-]{_PLAIN}*+"
    rf"|gi(?:{_plain_but('t')}{_PLAIN}*+)?"
    rf"|g(?:{_plain_but('i')}{_PLAIN}*+)?"
    rf"|{_plain_but('g')}{_PLAIN}*+"
    r")"
)
_UPTO_ANCHOR = rf"(?:{_PLAIN}*+{_ANCHOR})++"
# 中に git の始まり（`git` / `/usr/bin/git`）を持たない語。git のオプションとその値は、この語だけを
# 読む。始まりを持つ語で読むのをやめても、その始まりから読み直せば同じ続きで当たるので、当たる形は
# 変わらない。1 つの始まりから読む長さが次の始まりまでになり、`git -c git -c …` でも
# 読み直しが積もらない。
_GIT_FREE = rf"(?:{_UPTO_ANCHOR}{_TAIL}?|{_TAIL})"
_GIT_FREE_OPTION = rf"-{_PLAIN}*+(?:{_UPTO_ANCHOR}{_TAIL}?)?"
# サブコマンドの前に挟める git のグローバルオプション。値を空白で区切って取るもの
# （`-C <path>` `-c <k=v>` `--git-dir <dir>` など）は値まで読み、それ以外は `-` で始まる語を
# 1 つ読む（`--no-pager` `--git-dir=.git` `-C.`）。オプションでない語で読むのをやめるので、
# `git commit -m push` の `push` は値の位置で、サブコマンドには読まない。値まで読んだ語を
# 読み直さない（所有的）ので、`git -c push.default=current status` の値の `push` も
# サブコマンドには読まない。
#
# 値とのあいだの空白は 1 字だけ読む。読み（shellread）は語を空白 1 字でつなぎ、空の語
# （`git -C '' push` の `''`）は空のまま残すので、空白が 2 字続くところは空の値になる。
# 空白をまとめて読むと、空の値の次の `push` を値として読んで外れる。
_GIT_OPTIONS = (
    r"(?:\s++(?:(?:-c|--(?:git-dir|work-tree|namespace|config-env|super-prefix"
    rf"|attr-source))\s{_GIT_FREE}?|{_GIT_FREE_OPTION}))*+"
)


def _git_sub(sub: str) -> str:
    """`git <オプション> <sub>` と、ハイフンでつないだ `git-<sub>`。

    `git-<sub>` は `/usr/lib/git-core/git-push` のように、git のサブコマンドを直接起こす形。
    """
    return rf"(?:git(?:\.exe)?{_GIT_OPTIONS}\s++{sub}|git-{sub}(?:\.exe)?)"


GIT_PUSH_REGEX = rf"{_BEFORE}{_git_sub('push')}{_AFTER}"
# `--hard` は reset の後ろのどこにあってもよい（`git reset -q --hard`）。git は長いオプションの
# 略（`--har`）も受け付ける。後ろの語はどれでも読み飛ばせるので、コマンドの中で最初の reset に
# 決める。後の reset で当たるなら、その `--hard` は最初の reset の後ろにもある。
# reset の後ろの語のうち、`--`（ここから後ろはパス）と `--ha` `--har` `--hard` でないもの。
_RESET_OTHER = (
    r"(?:(?:[^\s\x00-]|-[^\s\x00-]|--[^\s\x00h]|--h[^\s\x00a]|--ha[^\s\x00r]"
    r"|--har[^\s\x00d]|--hard[^\s\x00])[^\s\x00]*+|--h|-)"
)
# `--` から後ろはパスで、`--hard` には読まない（`git reset -- --hard`）。そのあと同じコマンドの中で
# 次に reset の始まりが来たら、そこから読み直す（rm の `_RM_RESTART` と同じ考え方）。
_RESET_RESTART = (
    rf"(?:(?:{_RESET_OTHER}\s++)*?--(?:\s++[^\s\x00]++)*?\s++{_IN_WORD}{_git_sub('reset')}"
    r"\s\s*+)*+"
)
GIT_RESET_HARD_REGEX = (
    rf"{_FIRST}{_git_sub('reset')}\s)\s*+{_RESET_RESTART}"
    rf"(?:{_RESET_OTHER}\s++)*?--ha(?:rd?)?{_WORD_END}"
)

# ---- rm
#
# rm の再帰と強制。短い形はまとめても（`-rf` `-fr` `-Rf` `-rfv`）分けても（`-r -f`）、
# 長い形（`--recursive` `--force`。略した `--rec` `--for` も rm は受け付ける）でもよく、
# 順序もオペランドとの前後も問わない。片方だけ（`rm -r` `rm -f`）は当てない。
#
# 後ろの語はどれでも読み飛ばせるので、コマンドの中で最初の rm に決める（後の rm で当たるなら、
# その選択肢は最初の rm の後ろにもある）。そこから語を 1 つずつ読み、最初の選択肢の語で
# 「両方」なら当たり、「再帰」なら後ろに「強制」を、「強制」なら後ろに「再帰」を探す。
# 選択肢でない語の読み飛ばしは、選択肢の語を読み飛ばさない形（`_rm_other`）で書く。
_RM_START = rf"{_FIRST}rm(?:\.exe)?\s)"
_RM_NEITHER = "[a-eg-qs-z]"
_RM_BOTH = rf"-{_RM_NEITHER}*+(?:r[a-eg-z]*+f|f[a-qs-z]*+r)[a-z]*+"
_RM_RECURSIVE = r"(?:-[a-qs-z]*+r[a-z]*+|--r[a-z]*+)"
_RM_FORCE = r"(?:-[a-eg-z]*+f[a-z]*+|--f[a-z]*+)"


def _rm_other(flags: str) -> str:
    """`flags` のどの字の選択肢でもない語。`-` の後ろが英字だけで `flags` の字を含む語と、
    `--` の後ろが `flags` の字で始まる英字だけの語、それに `--` そのもの（ここから後ろは
    オペランド）を除いたもの。"""
    rest = "".join(c for c in "abcdefghijklmnopqrstuvwxyz" if c not in flags)
    letters = f"[{rest}]"
    other = r"[^\s\x00a-z]"
    return (
        r"(?:[^\s\x00-][^\s\x00]*+"
        rf"|-(?:{letters}++(?:{other}[^\s\x00]*+)?|[^\s\x00a-z-][^\s\x00]*+|)"
        rf"|-{letters}*+[{flags}][a-z]*+{other}[^\s\x00]*+"
        rf"|--(?:[^\s\x00{flags}][^\s\x00]*+|[{flags}][a-z]*+{other}[^\s\x00]*+)"
        r")"
    )


# 片方だけの選択肢。`--` の手前で読みを切り替えるときに使う（両方を持つ語はそこで当たりにする）。
_RM_RECURSIVE_ONLY = r"(?:-[a-eg-qs-z]*+r[a-eg-z]*+|--r[a-z]*+)"
_RM_FORCE_ONLY = r"(?:-[a-eg-qs-z]*+f[a-qs-z]*+|--f[a-z]*+)"
# `--` から後ろはオペランドで、選択肢には読まない（`rm -f -- -r`）。そのあと同じコマンドの中で
# 次に rm の始まり（読み切れずに生の文字列に当てる回の `; rm …`）が来たら、そこから読み直す。
# `--` までに当たりが決まる並び（両方の語、再帰と強制の両方）は、ここを通らずに下で当たる。
_RM_RESTART = (
    rf"(?:(?:{_rm_other('rf')}\s++)*+"
    rf"(?:{_RM_RECURSIVE_ONLY}\s++(?:{_rm_other('f')}\s++)*+"
    rf"|{_RM_FORCE_ONLY}\s++(?:{_rm_other('r')}\s++)*+)?"
    rf"--(?:\s++[^\s\x00]++)*?\s++{_IN_WORD}rm(?:\.exe)?\s++)*+"
)
RECURSIVE_DELETE_REGEX = (
    rf"{_RM_START}\s*+{_RM_RESTART}(?:{_rm_other('rf')}\s++)*+"
    rf"(?:{_RM_BOTH}{_WORD_END}"
    rf"|{_RM_RECURSIVE}\s++(?:{_rm_other('f')}\s++)*+{_RM_FORCE}{_WORD_END}"
    rf"|{_RM_FORCE}\s++(?:{_rm_other('r')}\s++)*+{_RM_RECURSIVE}{_WORD_END})"
)


# 設定で動かない残り。止める形は設定に依らない。文面の git の打ち方だけがワークスペースで変わる。
# ラッパーが無いときは生の git も止まるので、`git` の形を案内せず、ユーザに頼む文にする。
def _deny(root: str) -> list[dict]:
    """設定に依らない組み込みの deny を、ルールの dict の列で返す。`rules.parse` が読む形。

    処理フロー:
    1. `settings.git_command` と `settings.has_git_wrapper` で、文面に出す git の打ち方を決める
    2. 次の順に dict を並べる
       - `builtin:recursive-delete`: 再帰と強制の両方を持つ rm（`RECURSIVE_DELETE_REGEX`）
       - `builtin:git-push`: git push（`GIT_PUSH_REGEX`）
       - `builtin:git-reset-hard`: git reset --hard（`GIT_RESET_HARD_REGEX`）
       - `builtin:credentials`: 認証情報の場所の形（Bash・Read・Write・Edit・NotebookEdit の入力）
       - `builtin:guard-git-dir`: `.git` の中を書く Write・Edit・NotebookEdit
         （`selfguard_shell.GIT_DIR_PATH_REGEX`）
       - `builtin:guard-git-dir-via-bash`: `.git` の中を書く Bash
         （`selfguard_shell.git_dir_shell_regex`）
    3. `.git` 向けの 2 本の文面は `_git_dir_message` で組む

    ラッパーが無いときは、打てる git の形を案内せず、ユーザに頼む文にする。
    """
    git = settings.git_command(root)
    wrapper = settings.has_git_wrapper(root)
    return [
        {
            "id": "builtin:recursive-delete",
            "match": "Bash",
            "regex": RECURSIVE_DELETE_REGEX,
            "message": (
                "再帰的な強制削除は取り消せません。削除するものを 1 つずつ名指しするか、"
                f"ファイルが追跡されているなら {git} rm <パス> を使ってください。"
                if wrapper
                else "再帰的な強制削除は取り消せません。削除するものを 1 つずつ名指しするか、"
                "ユーザに削除を頼んでください。"
            ),
        },
        {
            "id": "builtin:git-push",
            "match": "Bash",
            "regex": GIT_PUSH_REGEX,
            "message": (
                f"git push は直接実行しません。親のワークツリーのブランチは {git} push で送ります。"
                "子のワークツリーのブランチは送らず、親が取り込んでから親のワークツリーで送ります。"
                f"統合先への送りはユーザが打ちます。通る形は {git} の --help を見てください。"
                if wrapper
                else "エージェントは git push を実行しません。"
                "ブランチはそのままにして、push はユーザに頼んでください。"
            ),
        },
        {
            "id": "builtin:git-reset-hard",
            "match": "Bash",
            "regex": GIT_RESET_HARD_REGEX,
            "message": (
                f"作業中の変更が失われます。{git} stash push -u で退避するか、"
                f"ファイルを名指しして {git} restore <パス> を使ってください。"
                if wrapper
                else "作業中の変更が失われます。変更を捨てる前に、ユーザに確認してください。"
            ),
        },
        {
            "id": "builtin:credentials",
            # 常に効く土台なので、設定で切れない。誤検知が出ても外せない。範囲を広げるときは、
            # 誤検知の出方（見本の `.env.example` も `x.pem.bak` も止まる）を承知の上で広げる。
            # 対象は Bash / Read / Write / Edit / NotebookEdit の 5 つ。Grep / Glob / PowerShell は
            # match に無いので当たらない（探す先のパスは取り出せるが、探索の中身を代表しない）。
            "match": "Bash|Read|Write|Edit|NotebookEdit",
            # 先頭の境界（行頭・空白・区切り）を付ける。常時有効の土台なので、
            # `jq '.env.X'` のようなフィールド参照や `process.env` を巻き込まない。
            # `.env` の後ろに続く `.envrc` `.env.local` `.env.example` は止める。
            # 鍵のファイルは拡張子として書かれた形（`x.pem`、`x.pem.bak`、`x.key.json`）。
            "regex": (
                r"(^|[ \\/\x00])\.(env|dev\.vars|netrc|npmrc|pypirc)"
                r"|(^|[ \\/\x00])\.(aws|ssh)[\\/]"
                r"|[\w\-\\/]\.(pem|key)\b"
                r"|[\\/]secrets[\\/]"
                r"|[\\/]credentials([\\/]|\.json\b)"
                r"|\b(id_rsa|id_ed25519)\b"
            ),
            "message": "認証情報を含む場所です。中身を読まず、必要な値をユーザに尋ねてください。",
        },
        {
            "id": GIT_DIR_RULE_ID,
            "match": "Write|Edit|NotebookEdit",
            "regex": selfguard_shell.GIT_DIR_PATH_REGEX,
            "message": _git_dir_message(git, wrapper),
        },
        {
            "id": GIT_DIR_SHELL_RULE_ID,
            "match": "Bash",
            "regex": selfguard_shell.git_dir_shell_regex(),
            "message": _git_dir_message(git, wrapper),
        },
    ]


# `.git` の中身（参照・索引・設定・hook）を直接書き換える形。書き換えは git の記録に残らず、
# 壊すと履歴や作業中の変更ごと失う。hook を書けば、以後の git の操作のたびに任意のコマンドが走る。
# 名指しのツールとシェルで当てる先が違う（パスとコマンド文字列）ので、id を分ける。
# 読むだけ（`cat .git/config`、名指しの Read）は止めない。
GIT_DIR_RULE_ID = "builtin:guard-git-dir"
GIT_DIR_SHELL_RULE_ID = "builtin:guard-git-dir-via-bash"


def _git_dir_message(git: str, wrapper: bool) -> str:
    how = (
        f"git のサブコマンドでできる作業なら、{git} <サブコマンド> で実行してください。"
        if wrapper
        else ""
    )
    return (
        ".git ディレクトリの中に直接書き込もうとしています。"
        "git を通さない変更は、履歴を失わせたり、"
        "フックを通してコマンドを走らせたりするおそれがあります。"
        f"{how}"
        "フック、.git/info/exclude、MERGE_MSG、index.lock、ワークツリーの .git ファイルは、"
        "どのサブコマンドでも編集されません。"
        "変更が必要なら、ファイル名と理由を添えてユーザに頼んでください。"
        "index.lock が古く残っているときは、git のプロセスが動いていないことを確かめてから、"
        "ユーザに削除を頼んでください。"
        "ファイルを無視したいときは、.gitignore を編集してください。"
        ".git の下のファイルを読むことは止めていません。"
    )


_ALLOW: list[dict] = [
    {
        # 作業ツリーを変えようがない読み取り。既定を使っている最中でも、
        # ここまで確認を出すと、本当に見てほしい 1 件が見落とされる。
        # Grep と Glob は書かない。対象は取り出せる（探し始める場所のパス）が、
        # それは探索の中身を代表しない。Read は 1 ファイルずつなので
        # builtin:credentials（match は Bash|Read|Write|Edit|NotebookEdit）が先に当たって
        # 止まるのに対し、Grep はその deny の match に無いので、ここで広く許すと
        # credentials ごと止められずに通る。
        "id": "builtin:read-anything",
        "match": "Read",
        "glob": "*",
    },
]

# 既定に戻ったことを記録に残すための値。呼び出しごとの記録を数えれば、
# ガードが機能しないまま何回動いたかが後から分かる。
FALLBACK = "builtin:rules"

# 返す理由に載せる出所。既定を使っているとき、適用しているルールは読めなかった
# ファイルの中には無い。そのファイルの名前を出所として出すと、見に行ったユーザが
# 適用されたルールを見つけられず、文面と設定が食い違っているように見える。
SOURCE = "(ccnavi built-in defaults)"


def load(root: str, conf: settings.Settings) -> tuple[rules.RuleSet, list[rules.Problem]]:
    """組み込みの既定ルールを組み立てる。

    ここが問題を返したら、それはルールファイルではなくこのビルドの不備なので、
    呼び手はそのまま報告してよい。
    """
    return rules.parse(rule_data(root, conf), builtin=True)
