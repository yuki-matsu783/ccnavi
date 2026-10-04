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


def script_fields_set(text: str) -> tuple[str, ...]:
    """その版が既に値を持っている、スクリプトの欄。

    正規化した内容を突き合わせる側が「落としてよい欄」を決めるのに使う（`post_findings._script_writes`）。
    **落としてよいのは、コミット済みの版がまだ持っていない欄だけ。** 副命令はどれも
    1 度しか書かない（`ops.start` は着手済みを拒む）ので、既に値がある欄が変わったのなら、
    それは副命令が書いたものではない。

    とくに `base_sha` は、サブエージェント終了時チェック（`phase_scope.scope_findings` の
    `base_sha..HEAD`）と実績リスク（`risk.measure`）の基準点。ここを書き換えられると、
    コミット済みの範囲外の変更が検査から消える。落とす欄を「いつでも」にすると、その
    書き換えが実行後チェックからも消える。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != ticket_model.FENCE:
        return ()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == ticket_model.FENCE), None)
    if end is None:
        return ()
    held = []
    for line in lines[1:end]:
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() in ticket_model.SCRIPT_FIELDS and value.strip():
            held.append(key.strip())
    return tuple(held)


def script_shape(text: str, drop: tuple[str, ...] = ticket_model.SCRIPT_FIELDS) -> str | None:
    """frontmatter を持つチケットなら、`drop` の欄を落として正規化した内容を返す。無ければ None。

    実行後チェックが「この変更は ccnavi の副命令が書いたぶんか」を、台帳ではなく内容で
    答えるのに使う（`post_findings._script_writes`）。台帳を持たないのは、承認とマーカーが親の
    ブランチに乗って別の機械へ届くため。台帳はワークスペース側にあって git に入らないので、
    clone した続きでは 1 件も残っていない。内容で見るなら、どの機械でも同じ答えになる。

    落とすのは `drop` に挙げた欄の行と、その欄の値として続く字下げの行だけ。`drop` は
    `ticket_model.SCRIPT_FIELDS` の部分集合で、決めるのは呼ぶ側（`script_fields_set` を引いて、
    コミット済みの版がまだ持っていない欄だけを渡す）。範囲
    （`allow` / `ask` / `deny`）も `parent` も `project` も `phase` も本文も残るので、
    そこが 1 文字でも変われば別の内容になり、チェックは今までどおり報告する。

    切り出し方は `set_fields` と揃える。あちらが行単位で書き換えるので、こちらも行単位で
    落とす。揃えないと、スクリプトが書いた直後の内容が「スクリプトが書いていない形」に見える。

    frontmatter を持たないもの（マーカー、`.risk.json`、閉じの記録）は None。範囲を
    宣言しないので、正規の設置と偽の設置を内容からは見分けられない。**そこは外れる。**
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
