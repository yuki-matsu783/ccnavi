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

入れないもの: Write / Edit によるガード自身の設定の書き換え。
通常のルールファイルはそこを止めているが、既定では通す。ルールファイルが
破損している状態で編集まで止めると、直す方法が 1 つも残らない。要件にも
「読み取りと設定自身の修復を妨げない」とあるのはこの意味。

入れるもの: Bash からガード自身の設定へ書き込む形。
上と対になっている。修復は Write / Edit という構造化された、記録の残る経路だけに
限る。ここを通すと、`echo x > .ccnavi/common/rules.yml` でルールを破損させ、
その結果として保護の甘い既定に戻る、という手順が成り立ってしまう。
破損させる側と直す側で経路を分けることで、その手順を成り立たなくする。

止めるのは書き込む形だけで、場所の名前が出たかどうかでは止めない。
同じ場所には `selfguard_shell.add_rules` の組み込みも当たる（ルールファイルが読める間の
`builtin-guard-setting-files`）。ルールファイルの側にシェルからの書き込みを止める 1 本は
無いので、ここが外れているときに残る保護はこれだけになる。
場所の名前で止めると、`git add <パス>` も `git restore --ours -- <パス>` も止まる。
どちらもファイルの中身を書かないのに。

これが問題になるのはマージの衝突を解くとき。衝突マーカーの入ったルールファイルは
YAML として読めないので既定に戻る。そこで解決の手が止まると、ガードが機能しない
状態から出られなくなる。名前が出たら止める形は、いちばんガードを直したいときに
いちばん強く適用される。

シェルから入れられるのは、既にコミットされている内容だけになる。新しい文面は
Write / Edit を通る。破損させる側と直す側を分ける狙いはそこで保たれている。
書き込む形をここで数え漏らしても、既定はプロジェクトの `allow` を持たないので、
当たらなかった呼び出しは権限モードに渡り、ユーザが居るモードなら 1 件ずつ確認が出る。

## 既定に戻ると、ほとんどを権限モードに委ねる

どのルールも言及しない呼び出しについて、ccnavi は判定を持たず、Claude Code の
権限モードに従う（cli.py）。既定にはプロジェクトの `allow` が入っていないので、
既定を使っているあいだは、書き込みもシェルもほとんどがそこへ渡る。
ユーザが居るモードなら 1 件ずつ確認が出る。これは不具合ではなく、そのほうがよい。
ガードが機能していないあいだ状態を変える操作は、ユーザが見ているべきものになる。

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
        "deny": [_config_via_bash(shell), *_DENY],
        "allow": _ALLOW,
    }


def base_data() -> dict:
    """常に有効な組み込みの deny（土台）。取り返しの付かない操作を止めるもので、設定に依らない。

    共通レイヤーと config の有無・状態によらず適用し、共通レイヤーと config のルールはその上に足す。
    `rule_data` の deny のうち、設定で動かない残り（`_DENY`）と同じもの。
    `Read` だけ通す allow はここに入れない。あれは組み込みの既定だけで判定するとき
    （共通レイヤーと config が両方無い・共通レイヤーが破損している）に限る。
    """
    return {"version": rules.VERSION, "deny": _DENY}


def load_base() -> tuple[rules.RuleSet, list[rules.Problem]]:
    """常に有効な組み込みの deny を組み立てる。`load` と同じく、問題はこのビルドの不備。"""
    built, problems = rules.parse(base_data(), builtin=True)
    for rule in built.all():
        rule.base = True
    return built, problems


def read_allow() -> list[rules.Rule]:
    """組み込みの既定の `Read` を通す allow。共通レイヤーと config が両方無いときに足す。"""
    built, _ = rules.parse({"version": rules.VERSION, "allow": _ALLOW}, builtin=True)
    return built.allow


CONFIG_VIA_BASH_RULE_ID = "builtin-guard-config-via-bash"


def _config_via_bash(regex: str) -> dict:
    return {
        "id": CONFIG_VIA_BASH_RULE_ID,
        "match": "Bash",
        # 照合する形は selfguard と同じものを 1 か所から取る（`guard_shell_regex`）。
        # 書き写すと既定のほうだけが弱くなり、ルールファイルを破損させることが
        # そのまま保護を弱めることになる。
        "regex": regex,
        "message": (
            "ccnavi is running on its built-in defaults because its rule file "
            "could not be read, and the shell is not the way to write it. "
            "Put new content there with the Write or Edit tool, so the repair "
            "goes through a path that is checked and recorded. Resolving a merge "
            "conflict is not blocked: 'git add' and 'git restore --ours' put back "
            "a version that is already committed instead of writing new content."
        ),
    }


# 設定で動かない残り。
_DENY: list[dict] = [
    {
        "id": "builtin-recursive-delete",
        "match": "Bash",
        "glob": "*rm -rf *",
        "message": (
            "A recursive forced delete cannot be undone. Name what to remove "
            "one at a time, or use 'git rm' when the files are tracked."
        ),
    },
    {
        "id": "builtin-git-push",
        "match": "Bash",
        "glob": "*git push*",
        "message": (
            "git push is not run by the agent. Leave the branch as it is and ask the user to push."
        ),
    },
    {
        "id": "builtin-git-reset-hard",
        "match": "Bash",
        "glob": "*git reset --hard*",
        "message": (
            "Work in progress would be lost. Use 'git stash' to set it aside, "
            "or name the files and use 'git restore'."
        ),
    },
    {
        "id": "builtin-credentials",
        "match": "Bash|Read|Write|Edit",
        # 先頭の境界（行頭・空白・区切り）を付ける。常時有効の土台なので、`jq '.env.X'` のような
        # フィールド参照や `process.env` を巻き込まない（リポジトリ自身の `credentials` と同じ形）。
        # `.env` の後ろに続く `.envrc` `.env.local` は止める。
        "regex": (
            r"(^|[ \\/\x00])\.(env|netrc|npmrc)"
            r"|(^|[ \\/\x00])\.ssh[\\/]"
            r"|\b(id_rsa|id_ed25519)\b"
        ),
        "message": (
            "This is a place credentials live. Do not read it; ask the user for the value you need."
        ),
    },
]

_ALLOW: list[dict] = [
    {
        # 作業ツリーを変えようがない読み取り。既定を使っている最中でも、
        # ここまで確認を出すと、本当に見てほしい 1 件が見落とされる。
        # Grep と Glob は書かない。対象は取り出せる（探し始める場所のパス）が、
        # それは探索の中身を代表しない。Read は 1 ファイルずつなので
        # builtin-credentials が先に当たって止まるのに対し、Grep はその deny の
        # match に無いので、ここで広く許すと credentials ごと止められずに通る。
        "id": "builtin-read-anything",
        "match": "Read",
        "glob": "*",
    },
]

# 既定に戻ったことを記録に残すための値。呼び出しごとの記録を数えれば、
# ガードが機能しないまま何回動いたかが後から分かる。
FALLBACK = "builtin-rules"

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
