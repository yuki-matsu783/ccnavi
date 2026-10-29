"""`ccnavi text <識別子> [名前=値]... [@欄=場合]...`。文面ファイルの文を組み立てて出す。

sh が文面ファイルを直接読まずに文を引く入口。標準出力に組み立てた文（末尾に改行）。
引けなければ（識別子が無い・値の不足・文面ファイルが壊れている）標準出力は空で、理由を標準エラーに出し、
終了コード 1。引数の誤りは 2。呼ぶ側は、引けなくても止める・止めないの判定を変えない。
"""

from __future__ import annotations

from typing import TextIO

from ..policy import texts

EXIT_OK = 0
EXIT_UNAVAILABLE = 1
EXIT_USAGE = 2


def run(stdout: TextIO, stderr: TextIO, root: str, words: list[str]) -> int:
    if not words or not texts.ID.fullmatch(words[0]):
        stderr.write("ccnavi: text <識別子> [名前=値]... [@欄=場合]...\n")
        return EXIT_USAGE
    values: dict[str, str] = {}
    variants: dict[str, str] = {}
    for word in words[1:]:
        key, sep, value = word.partition("=")
        if not sep or not key:
            stderr.write(f"ccnavi: text の引数 {word!r} は 名前=値 の形ではありません。\n")
            return EXIT_USAGE
        if key.startswith("@"):
            variants[key[1:]] = value
        else:
            values[key] = value
    try:
        entry = texts.find(root, words[0])
        if entry is None:
            raise texts.TextError(f"{words[0]}: 文面が無い")
        text = texts.render(entry, values, variants, root)
    except (texts.TextError, OSError, ValueError) as exc:
        stderr.write(f"ccnavi: 文面を引けません: {exc}\n")
        return EXIT_UNAVAILABLE
    stdout.write(text + "\n")
    return EXIT_OK
