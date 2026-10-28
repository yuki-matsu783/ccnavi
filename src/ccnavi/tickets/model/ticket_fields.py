"""スクリプトが書く欄の、行単位の書き換えと読み取り。

ユーザが書いたコメントや `|` のブロックを残すため、frontmatter を読み直さずに行で扱う。
ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

import yaml

from . import ticket_model


def set_fields(text: str, fields: dict[str, str]) -> str:
    """提案の frontmatter の、スクリプトが書く欄だけを行単位で書き換える。

    読み直して書き出す（ticket.render）と、ユーザが書いたコメントや `|` のブロックが
    消えて、親のブランチの diff に余計な変更が出る。提案はユーザも読むものなので、
    触るのは欄の行だけにする。無い欄は閉じの `---` の前に足す。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != ticket_model.FENCE:
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)
    if end is None:
        return text
    pending = {k: v for k, v in fields.items() if k in ticket_model.SCRIPT_FIELDS}
    for i in range(1, end):
        key = lines[i].split(":", 1)[0].strip()
        if key in pending and not lines[i].startswith((" ", "\t")):
            lines[i] = f"{key}: {_yaml_scalar(pending.pop(key))}"
    for key, value in pending.items():
        lines.insert(end, f"{key}: {_yaml_scalar(value)}")
        end += 1
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


MR_KEY = "mr"

# `set_mr` の結果。
MR_SET = "set"  # 空だったので書いた
MR_SAME = "same"  # 同じ番号が既にあった（何もしない）
MR_DIFFERS = "differs"  # 違う値が既にあった（書かない）
MR_UNREADABLE = "unreadable"  # frontmatter が無い・閉じていない


def _front_end(lines: list[str]) -> int | None:
    if not lines or lines[0].strip() != ticket_model.FENCE:
        return None
    return next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)


def _field_line(lines: list[str], end: int, key: str) -> int | None:
    for i in range(1, end):
        line = lines[i]
        if not line.startswith((" ", "\t")) and line.split(":", 1)[0].strip() == key:
            return i
    return None


def plain_int_field(text: str | None, key: str) -> int | None:
    """欄が `key: 12` の 1 行だけの正の整数ならその値。それ以外は None。

    無い・空・引用符つき・`!12`・続きの行・注釈つきは None（ccnavi が書く形だけを読む）。
    """
    if text is None:
        return None
    lines = text.splitlines()
    end = _front_end(lines)
    if end is None:
        return None
    i = _field_line(lines, end, key)
    if i is None or (i + 1 < end and lines[i + 1].startswith((" ", "\t"))):
        return None
    value = lines[i].split(":", 1)[1].strip()
    return int(value) if value.isascii() and value.isdigit() and int(value) > 0 else None


def mr_is_empty(text: str | None, key: str = MR_KEY) -> bool:
    """欄が無いか、値なしか。続きの行があれば値ありとする（`script_fields_set` と同じ読み方）。"""
    if text is None:
        return False
    lines = text.splitlines()
    end = _front_end(lines)
    if end is None:
        return False
    i = _field_line(lines, end, key)
    if i is None:
        return True
    if i + 1 < end and lines[i + 1].startswith((" ", "\t")):
        return False
    return not _has_value(lines[i].split(":", 1)[1])


def _held_number(raw: str) -> int | None:
    """欄の値の表記が表す MR の番号（`12`・`"!12"`・`#12`）。読めなければ None。"""
    if len(raw) > _VALUE_PARSE_LIMIT:
        return None
    try:
        value = yaml.safe_load(raw)
    except (yaml.YAMLError, RecursionError, ValueError):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().lstrip("!#").strip()
    text = str(value)
    return int(text) if text.isascii() and text.isdigit() else None


def set_mr(text: str, number: int) -> tuple[str, str]:
    """`mr:` が空のときだけ番号を書く。答えは (書き直した全文, MR_* の結果)。

    同じ番号が既にあれば何もしない（MR_SAME）。違う値があれば書かない（MR_DIFFERS）。
    行単位で書き、ユーザの書いたコメントや本文は触らない（`set_fields` と同じ）。
    `!12`・`#12` と書かれた欄も、番号が同じなら MR_SAME。
    """
    lines = text.splitlines()
    end = _front_end(lines)
    if end is None:
        return text, MR_UNREADABLE
    i = _field_line(lines, end, MR_KEY)
    if i is not None and not mr_is_empty(text):
        return text, (MR_SAME if _held_number(lines[i].split(":", 1)[1]) == number else MR_DIFFERS)
    if i is not None:
        lines[i] = f"{MR_KEY}: {number}"
    else:
        lines.insert(end, f"{MR_KEY}: {number}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), MR_SET


def script_fields_set(text: str) -> tuple[str, ...]:
    """その版が既に値を持っている、スクリプトの欄。

    正規化した内容を照合する側が「除いてよい欄」を決めるのに使う（`post_findings._script_writes`）。
    除いてよいのは、コミット済みの版がまだ持っていない欄だけ。 サブコマンドはどれも
    1 度しか書かない（`ops.start` は着手済みを拒む）ので、既に値がある欄が変わったのなら、
    それはサブコマンドが書いたものではない。

    とくに `base_sha` は着手の基準点で、
    親の自分のコミットの数え（`lifecycle.stop._own_commits`）が使う。
    子の範囲検査（`phase_scope.scope_findings`）と実績リスク（`risk.measure`）は、親のワークツリーの
    HEAD との共通の祖先（`gitstate.child_base`）から数える。
    ただし `base_sha` はスクリプトだけが書く欄で、
    書き換えられると着手の記録が偽物になる。除く欄を「いつでも」にすると、その
    書き換えが実行後チェックからも消える。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != ticket_model.FENCE:
        return ()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)
    if end is None:
        return ()
    held = []
    for i in range(1, end):
        line = lines[i]
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        # 続きの字下げ行は `script_shape` が欄と一緒に落とす。続きがあれば値は 2 行目以降に
        # あるかもしれない（`|` や `# c` の次の行など）ので、1 行目だけで値なしと読まない。
        continued = i + 1 < end and lines[i + 1].startswith((" ", "\t"))
        if key.strip() in ticket_model.SCRIPT_FIELDS and (continued or _has_value(value)):
            held.append(key.strip())
    return tuple(held)


# 値なしかを YAML で読む表記の長さの上限。承認が置く空の欄は数文字なので、これを超えるものは
# 読まずに値ありとする。巨大な表記を YAML に掛けると実行後チェックが止まる。
_VALUE_PARSE_LIMIT = 1024


def _has_value(raw: str) -> bool:
    """欄の 1 行目の値の表記が、空でない値を持つか。

    承認（`approval_ops`）は欄を `started_at: ""` の形で置く。表記の字面で見ると `""` が
    値に見え、まだ書かれていない欄を「既に値がある」と数えて、`finish` の移動まで報告する。
    YAML として読んで、空文字と null を値なしとする。読めない表記・長すぎる表記は値ありとする
    （除く欄が減る側。報告は増えるが、外れすぎない）。
    """
    if not raw.strip():
        return False
    if len(raw) > _VALUE_PARSE_LIMIT:
        return True
    try:
        value = yaml.safe_load(raw)
    except (yaml.YAMLError, RecursionError, ValueError):
        return True
    return value is not None and value != ""


def script_shape(text: str, drop: tuple[str, ...] = ticket_model.SCRIPT_FIELDS) -> str | None:
    """frontmatter を持つチケットなら、`drop` の欄を除いて正規化した内容を返す。無ければ None。

    実行後チェックが「この変更は ccnavi のサブコマンドが書いたぶんか」を、台帳ではなく内容で
    答えるのに使う（`post_findings._script_writes`）。台帳を持たないのは、承認とマーカーが親の
    ブランチに乗って別の機械へ届くため。台帳はワークスペース側にあって git に入らないので、
    clone した続きでは 1 件も残っていない。内容で見るなら、どの機械でも同じ答えになる。

    除くのは `drop` に挙げた欄の行と、その欄の値として続く字下げの行だけ。`drop` は
    `ticket_model.SCRIPT_FIELDS` の部分集合で、決めるのは呼ぶ側（`script_fields_set` を引いて、
    コミット済みの版がまだ持っていない欄だけを渡す）。範囲
    （`allow` / `ask` / `deny`）も `parent` も `project` も `phase` も本文も残るので、
    そこが 1 文字でも変われば別の内容になり、チェックは今までどおり報告する。

    切り出し方は `set_fields` と揃える。あちらが行単位で書き換えるので、こちらも行単位で
    除く。揃えないと、スクリプトが書いた直後の内容が「スクリプトが書いていない形」に見える。

    frontmatter を持たないもの（マーカー、`.risk.json`、閉じの記録）は None。範囲を
    宣言しないので、正規の設置と偽の設置を内容からは見分けられない。そこは外れる。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != ticket_model.FENCE:
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)
    if end is None:
        return None
    kept, dropping = [], False
    for i, line in enumerate(lines):
        if not 1 <= i < end:
            kept.append(line)
            continue
        indented = line.startswith((" ", "\t"))
        if dropping and indented:
            continue
        dropping = not indented and line.split(":", 1)[0].strip() in drop
        if not dropping:
            kept.append(line)
    return "\n".join(kept)


def _yaml_scalar(value: str) -> str:
    """欄の値を、YAML が文字列として読み戻せる表記にする。"""
    return (
        yaml.safe_dump(value, allow_unicode=True, default_style='"').strip().removesuffix("\n...")
    )
