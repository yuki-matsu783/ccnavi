"""ファイルの読み書きの型。控え、マーカー、承認済みチケット、下書きが全部これを通る。

読めなければ None、書けなければ理由の文、という形に揃えてある。hook の中で
読み書きの失敗が例外のまま上へ抜けると、判定に達しないまま終わる。ここで
受け止めて値にしておけば、呼ぶ側は「読めなかったときにどうするか」だけを
書けばよく、try が 10 か所に散らばらない。

理由の文には例外の文字列だけを入れる。「書けない」「マーカーを置けない」のような
言い回しは呼ぶ側の用件で、ここでは決めない。
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable
from typing import Any

# ファイル名に混ぜられない字。セッションやエージェントの識別子をそのまま名前に
# するので、区切り文字が混じった値でファイルを別の場所へ書かせない。
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def stamp() -> str:
    """マーカーと記録に書く時刻。手元の時計、オフセット付き。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def slashed(path: str) -> str:
    """区切りを "/" に揃える。ルールと範囲の glob は "/" で書かれている。"""
    return path.replace("\\", "/")


def full_path(path: str, cwd: str) -> str:
    """ファイルのパスを、行き着く先が 1 つに決まる綴りに直す。

    来たままの文字列に当てると、同じ場所を別の綴りで書くだけでルールを外せる。
    相対パスは呼び出し側の作業ディレクトリ次第で意味が変わるし、`..` を挟めば
    `secrets/` を通らない綴りで `secrets/` の中に届く。シンボリックリンクなら
    名前を 1 つ増やすだけで済む。守る対象は名前ではなく場所なので、
    場所まで解いてから当てる。

    解けなかったときも、絶対パスにして `..` を畳むところまではやる。
    まだ存在しないファイルへの書き込みがこれにあたる。

    実行前の判定（judge）と実行後の監視（gitstate）が同じ綴りに直す。別々に持つと、
    同じ場所が 2 通りの綴りで当たり、実行前に通った書き込みが実行後に咎められる。
    """
    if not path:
        return ""
    base = cwd or os.getcwd()
    joined = os.path.join(base, os.path.expanduser(path))
    try:
        return os.path.realpath(joined)
    except OSError:
        return os.path.normpath(os.path.abspath(joined))


def safe_name(text: str, limit: int | None = 64) -> str:
    """識別子から作る、置き場の名前。limit が None なら切り詰めない。"""
    return _UNSAFE.sub("_", text)[:limit]


def read_text(path: str, errors: str = "strict") -> str | None:
    """本文。読めなければ None。"""
    try:
        with open(path, encoding="utf-8", errors=errors) as f:
            return f.read()
    except OSError:
        return None


def read_bytes(path: str) -> bytes | None:
    """中身をそのまま読む。読めなければ None。

    バイト列で扱う。改行を変換すると、控えから戻したファイルが元と 1 バイト
    違うものになる。Windows と Linux で同じ控えを取るために、ここは解釈しない。
    """
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


# 同じ名前への書き込みが一時的に失敗する errno。同じイベントの hook は並列に走り、
# 同じセッションの控えを同じ名前に書くので、片方が置き換え・削除している最中に
# もう片方が開くことがある。Windows はそれを ERROR_DELETE_PENDING や共有違反で返し、
# Python はそれぞれ EINVAL（表に無い Win32 エラーの既定）と EACCES にして投げる。
# どちらも待てば通る失敗なので、数回だけ打ち直す。表に無い理由（ENOSPC など）は
# 待っても変わらないので、すぐ返す。
_TRANSIENT_ERRNO = (errno.EINVAL, errno.EACCES, errno.EPERM)
_WRITE_RETRIES = 3
_WRITE_RETRY_WAIT_SECONDS = 0.02
# 読みの打ち直し。差し替えの一瞬に重なっただけなら、すぐ開けるようになる。
_READ_RETRIES = 3
_READ_RETRY_WAIT_SECONDS = 0.02


def _write_with_retry(write: Callable[[], None]) -> str:
    """書き込みを、一時的な失敗なら数回まで打ち直す。書けたら空文字、駄目なら理由。"""
    for attempt in range(_WRITE_RETRIES + 1):
        try:
            write()
        except OSError as exc:
            if exc.errno not in _TRANSIENT_ERRNO or attempt == _WRITE_RETRIES:
                return f"{exc}"
            time.sleep(_WRITE_RETRY_WAIT_SECONDS)
            continue
        return ""
    return ""


def write_text(path: str, text: str, newline: str | None = None) -> str:
    """本文を書く。親ディレクトリが無ければ作る。書けたら空文字、駄目なら理由。"""

    def write() -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)

    return _write_with_retry(write)


def write_text_atomic(path: str, text: str, newline: str | None = None) -> str:
    """本文を、読む側に途中を見せずに書く。書けたら空文字、駄目なら理由。

    `write_text` の `open(path, "w")` は、開いた時点で中身を捨てる。書き終える
    までのあいだファイルは空で、そこを誰かに読まれれば「空だった」ことになるし、
    途中で落ちれば空のまま残る。取り合いになる控えと、途中で落ちたものを次の
    起動に拾わせたくない控えは、こちらで書く。

    同じ場所に一時ファイルを作って書き切り、`os.replace` で差し替える。読む側が
    見るのは差し替えの前の中身か後の中身のどちらかだけになる。**差し替えが一瞬で
    終わるのは同じファイルシステムの中だけ**なので、一時ファイルは行き先と同じ
    ディレクトリに作る。他所（`/tmp` など）に作るとコピーに化けて、この型が
    成り立たなくなる。

    一時ファイルの名前は、拡張子の前に一意な部分を挟んで作る（`a.json` なら
    `a.<一意>.part.json`）。固定の名前にすると、同時に書く 2 つが同じ一時ファイルを
    取り合い、片方の書きかけをもう片方が差し替えることになる。直そうとした事故が
    形を変えて戻るので、ここは一意でなければならない。拡張子と先頭を残すのは、
    落ちて残った一時ファイルを、本番と同じふるい（`ctxfile.forget` など）で
    掃除できるようにするため。

    **守るのは「同時に読む側」までで、電源断は守らない。** 差し替えの前に
    `fsync` をしていないので、ディスクへ実際に届く順はファイルシステム任せ。
    電源断の直後に「新しいほうに差し替わっているが中身が古い／空」になる
    余地は残る。控えは失っても取り直せるものなので、そこまでの値段は払わない。

    **名前が伸びる。** 一時ファイルの名前は元より 20 文字ほど長い。Windows の
    260 文字の上限ぎりぎりの行き先では、`write_text` なら書けたものがここでは
    書けないことがある。
    """
    directory = os.path.dirname(path) or "."
    stem, suffix = os.path.splitext(os.path.basename(path))

    def write() -> None:
        os.makedirs(directory, exist_ok=True)
        handle, part = tempfile.mkstemp(dir=directory, prefix=f"{stem}.", suffix=f".part{suffix}")
        try:
            # mkstemp は必ず 0600 で作る。os.replace は inode ごと差し替えるので、
            # そのままだと行き先の権限が黙って 0600 に締まる（POSIX で実測）。
            # 素の open(path, "w") と同じ見え方に戻す。既に在るファイルを
            # 上書きするなら、その権限を引き継ぐ。
            os.chmod(part, _mode_for(path))
            try:
                stream = os.fdopen(handle, "w", encoding="utf-8", newline=newline)
            except BaseException:
                # fdopen が持ち主になる前に落ちたら、生の記述子は誰も閉じない。
                # Windows では開いたままの一時ファイルを消せず、残骸にもなる。
                os.close(handle)
                raise
            with stream as f:
                f.write(text)
            os.replace(part, path)
        except BaseException:
            # 差し替えまで行けなかった一時ファイルは置いていかない。消せなくても
            # 名前が本番と同じふるいに掛かるので、次の掃除で消える。
            with contextlib.suppress(OSError):
                os.remove(part)
            raise

    return _write_with_retry(write)


def _mode_for(path: str) -> int:
    """差し替えたあとに残したい権限。

    既に在るならその権限のまま。無ければ、素の `open` が作るのと同じ
    「0666 から umask を引いたもの」。umask は読むだけの手段が無いので、
    いったん設定して戻す。
    """
    with contextlib.suppress(OSError):
        return stat.S_IMODE(os.stat(path).st_mode)
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def write_bytes(path: str, content: bytes) -> str:
    """中身をそのまま書く。書けたら空文字、駄目なら理由。"""

    def write() -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    return _write_with_retry(write)


def read_json(path: str) -> tuple[Any, Exception | None]:
    """JSON を読む。読めなければ (None, 例外)。

    例外を返すのは、呼ぶ側が「無い」と「壊れている」を分けるため。無いのは
    普通の状態で黙ってよいが、壊れているのは言わないと直らない。

    一時的に開けないだけなら数回打ち直す。`write_text_atomic` の差し替えは
    中身を壊さないが、Windows ではその一瞬に開こうとした側が共有違反
    （EACCES）で弾かれる。打ち直さないと、控えを読む側はそれを「読めなかった」
    ＝「まだ何も無い」と読み、数えが 0 に戻る。書き込み側が同じ errno を
    打ち直しているのと対で、片方だけでは並んで走る hook を捌けない。
    """

    def read() -> Any:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    for attempt in range(_READ_RETRIES + 1):
        try:
            return read(), None
        except OSError as exc:
            # 無い（ENOENT）は普通の状態なので打ち直さない。待っても現れない。
            if exc.errno not in _TRANSIENT_ERRNO or attempt == _READ_RETRIES:
                return None, exc
            time.sleep(_READ_RETRY_WAIT_SECONDS)
        except ValueError as exc:
            # 中身が JSON でない。待っても変わらないので、そのまま返す。
            return None, exc
    return None, None


def read_dict(path: str) -> dict | None:
    """JSON の辞書。読めなければ None、読めたが辞書でなければ空の辞書。"""
    data, failed = read_json(path)
    if failed is not None:
        return None
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: str, data: Any, indent: int | None = None) -> str:
    """JSON を、読む側に途中を見せずに書く。書けたら空文字、駄目なら理由（`write_text_atomic`）。"""
    return write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=indent))


def read_line(stream: Any) -> str:
    """人の答えを 1 行読む。読めなければ空文字（端末が閉じている、など）。"""
    try:
        return stream.readline()
    except (OSError, ValueError):
        return ""


def remove(path: str) -> None:
    """消す。無くても、消せなくても黙る。"""
    with contextlib.suppress(OSError):
        os.remove(path)
