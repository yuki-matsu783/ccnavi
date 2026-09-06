"""ルールファイルを読めなかったときに使う、組み込みの既定ルール。

## なぜ既定が要るか

ルールファイルが壊れている、あるいはまだ無いときに拒否側へ倒すと、
壊れたファイルを直すための呼び出しまで止まって回復できなくなる。既定モードが
block なので、ファイルを置く前に hook を登録した時点でセッションが死ぬ。
「設定が読めない」は「判断できない」ではなく「設定が壊れている」であり、
扱いを分ける必要がある（REQ-PRE-06、requirements.md の REQ-PRE-04 の例外）。

## 何を入れて、何を入れないか

ここに入れるのは、取り返しの付かない操作だけに絞る。ガードが落ちている最中は
ルールの一覧が信用できないので、広く止めても「なぜ止まったのか」を利用者が
確かめる手立てが無い。止める数ではなく、止めそこねたときに戻せないものを見る。

**入れないもの: Write / Edit によるガード自身の設定の書き換え。**
通常のルールファイルはそこを止めているが、既定では通す。ルールファイルが
壊れている状態で編集まで止めると、直す道が 1 本も残らない。REQ-PRE-06 が
「読み取りと設定自身の修復を妨げない」と書いているのはこの意味。

**入れるもの: Bash からガード自身の設定へ書き込む形。**
上と対になっている。修復は Write / Edit という構造化された、記録の残る経路だけに
限る。ここを開けると、`echo x > .claude/ccnavi/rules.json` でルールを壊し、
壊れた結果として緩んだ既定に落ちる、という順路ができてしまう。
壊す側と直す側で経路を分けることで、その順路を閉じる。
"""

from __future__ import annotations

from . import rules

# ファイルから読むルールと同じ形。同じ `rules.parse` を通す。
RULES: dict = {
    "version": rules.VERSION,
    "rules": [
        {
            "id": "builtin-guard-config-via-bash",
            "match": "Bash",
            # 前に区切り文字を求めない。リダイレクト先は "> .claude/ccnavi/x" の
            # ように語の先頭に来るので、求めると素通りする。実際に踏んだ。
            "regex": r"\.claude[\\/](ccnavi|hooks)[\\/]",
            "message": (
                "ccnavi is running on its built-in defaults because its rule file "
                "could not be read, and the shell is not the way to repair it. "
                "Edit the file with the Write or Edit tool instead, so the repair "
                "goes through a path that is checked and recorded."
            ),
        },
        {
            "id": "builtin-recursive-delete",
            "match": "Bash",
            "pattern": "rm -rf *",
            "message": (
                "A recursive forced delete cannot be undone. Name what to remove "
                "one at a time, or use 'git rm' when the files are tracked."
            ),
        },
        {
            "id": "builtin-git-push",
            "match": "Bash",
            "pattern": "git push *",
            "message": (
                "git push is not run by the agent. Leave the branch as it is and "
                "ask the user to push."
            ),
        },
        {
            "id": "builtin-git-reset-hard",
            "match": "Bash",
            "pattern": "git reset --hard *",
            "message": (
                "Work in progress would be lost. Use 'git stash' to set it aside, "
                "or name the files and use 'git restore'."
            ),
        },
        {
            "id": "builtin-credentials",
            "match": "Bash|Read|Write|Edit|MultiEdit",
            "regex": r"\.env|\.ssh[\\/]|id_rsa|id_ed25519|\.netrc|\.npmrc",
            "message": (
                "This is a place credentials live. Do not read it; ask the user "
                "for the value you need."
            ),
        },
    ],
}

# 既定に落ちたことを記録に残すための印。呼び出しごとの記録を数えれば、
# ガードが落ちたまま何回動いたかが後から分かる。
FALLBACK = "builtin-rules"

# 返す理由に載せる出所。既定に落ちているとき、当てているルールは読めなかった
# ファイルの中には無い。そのファイルの名前を出所として名乗ると、見に行った人が
# 当たったルールを見つけられず、文面と設定が食い違っているように見える。
SOURCE = "(ccnavi built-in defaults)"


def load() -> tuple[rules.RuleSet, list[rules.Problem]]:
    """組み込みの既定ルールを組み立てる。

    ここが問題を返したら、それはルールファイルではなくこのビルドの不備なので、
    呼び手はそのまま報告してよい。
    """
    return rules.parse(RULES)
