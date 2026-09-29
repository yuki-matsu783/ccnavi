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
import datetime
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

# ファイル名に混ぜられない字。セッションやエージェントの識別子をそのまま名前に
# するので、区切り文字が混じった値でファイルを別の場所へ書かせない。
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


# ---- 時計（Clock の差し口。ADR-0093 の 6.2）
#
# 時刻はこの 2 つの関数だけが読む。`clock` で固定すると、その間の `stamp` と `utc_stamp` は
# 同じ 1 つの時刻を返す。承認の plan が承認の記録（`approved_at`）と跡（`at`）に同じ時刻を
# 書き、Chrome（Pyodide）と手元が同じ入力から同じバイト列を出すため。
_CLOCK: dict = {"fixed": ""}
_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


@contextlib.contextmanager
def clock(fixed: str) -> Iterator[None]:
    """この間の時刻を `fixed`（オフセット付き。`stamp` と同じ形）に固定する。空なら固定しない。"""
    before = _CLOCK["fixed"]
    if fixed:
        _parse_stamp(fixed)
    _CLOCK["fixed"] = fixed or before
    try:
        yield
    finally:
        _CLOCK["fixed"] = before


def _parse_stamp(text: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(text, _STAMP_FORMAT)
    except ValueError:
        raise ValueError(f"時刻の形が違う（{_STAMP_FORMAT} で読めない）: {text!r}") from None


def stamp() -> str:
    """マーカーと記録に書く時刻。手元の時計、オフセット付き。"""
    return _CLOCK["fixed"] or time.strftime(_STAMP_FORMAT)


def utc_stamp() -> str:
    """跡に書く時刻。UTC の ISO 8601（秒まで、`Z` 付き）。固定した時刻があればそれを直す。"""
    fixed = _CLOCK["fixed"]
    if fixed:
        return _parse_stamp(fixed).astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


def spelled_path(path: str, cwd: str) -> str:
    """ファイルのパスを、リンクを解かずに絶対の綴りに直す。`..` は畳む。

    `full_path` は行き着く先に直すので、リンクそのものの綴りが消える。置き場の綴りに当てる
    止める向きの検査（フローのロック）は、解いた先と解く前の両方に当てるためにこちらも使う。
    """
    if not path:
        return ""
    base = cwd or os.getcwd()
    return os.path.normpath(os.path.abspath(os.path.join(base, os.path.expanduser(path))))


def parent_resolved(path: str) -> str:
    """ディレクトリだけを行き着く先に直し、最後の名前は綴りのまま残す。空なら空。"""
    if not path:
        return ""
    head, name = os.path.split(path)
    try:
        return os.path.join(os.path.realpath(head), name)
    except OSError:
        return path


def safe_name(text: str, limit: int | None = 64) -> str:
    """識別子から作る、置き場の名前。limit が None なら切り詰めない。"""
    return _UNSAFE.sub("_", text)[:limit]


def read_text(path: str, errors: str = "strict") -> str | None:
    """本文。読めなければ None。"""
    staged, content = _staged(path)
    if staged:
        return None if content is None else _decode(content, errors)
    try:
        with open(path, encoding="utf-8", errors=errors) as f:
            text = f.read()
    except OSError:
        note_read(path, None)
        return None
    note_read(path, text)
    return text


def read_bytes(path: str) -> bytes | None:
    """中身をそのまま読む。読めなければ None。

    バイト列で扱う。改行を変換すると、控えから戻したファイルが元と 1 バイト
    違うものになる。Windows と Linux で同じ控えを取るために、ここは解釈しない。
    """
    staged, content = _staged(path)
    if staged:
        return content
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        note_read(path, None)
        return None
    note_read(path, data)
    return data


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
    if _STAGE["current"] is not None:
        return _stage_put(Op(OP_TEXT, path, _encode(text, newline), text=text, newline=newline))

    def write() -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)

    return _recorded(path, _write_with_retry(write))


def write_text_atomic(path: str, text: str, newline: str | None = None) -> str:
    """本文を、読む側に途中を見せずに書く。書けたら空文字、駄目なら理由。

    `write_text` の `open(path, "w")` は、開いた時点で中身を捨てる。書き終える
    までのあいだファイルは空で、そこを誰かに読まれれば「空だった」ことになるし、
    途中で落ちれば空のまま残る。取り合いになる控えと、途中で落ちたものを次の
    起動に拾わせたくない控えは、こちらで書く。

    同じ場所に一時ファイルを作って書き切り、`os.replace` で差し替える。読む側が
    見るのは差し替えの前の中身か後の中身のどちらかだけになる。**差し替えが一瞬で
    終わるのは同じファイルシステムの中だけ**なので、一時ファイルは行き先と同じ
    ディレクトリに作る。他所（`/tmp` など）に作るとコピーになってしまい、この型が
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
    if _STAGE["current"] is not None:
        return _stage_put(
            Op(OP_TEXT_ATOMIC, path, _encode(text, newline), text=text, newline=newline)
        )
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

    return _recorded(path, _write_with_retry(write))


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
    if _STAGE["current"] is not None:
        return _stage_put(Op(OP_BYTES, path, bytes(content)))

    def write() -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    return _recorded(path, _write_with_retry(write))


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

    staged, content = _staged(path)
    if staged:
        if content is None:
            return None, _missing(path)
        try:
            return json.loads(_decode(content)), None
        except ValueError as exc:
            return None, exc

    def read() -> Any:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        note_read(path, text)
        return json.loads(text)

    for attempt in range(_READ_RETRIES + 1):
        try:
            return read(), None
        except OSError as exc:
            # 無い（ENOENT）は普通の状態なので打ち直さない。待っても現れない。
            if exc.errno not in _TRANSIENT_ERRNO or attempt == _READ_RETRIES:
                note_read(path, None)
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
    if _STAGE["current"] is not None:
        if lexists(path):
            _stage_put(Op(OP_REMOVE, path, None), quiet=True)
        return
    with contextlib.suppress(OSError):
        os.remove(path)
        _record(path)


def unlink(path: str) -> str:
    """消す。消せたら空文字、無い・消せないなら理由（`remove` と違って黙らない）。"""
    if _STAGE["current"] is not None:
        # リンクは辿らない（`os.remove` はリンクそのものを消す）。
        if not lexists(path):
            return str(_missing(path))
        return _stage_put(Op(OP_UNLINK, path, None))
    try:
        os.remove(path)
    except OSError as exc:
        return f"{exc}"
    _record(path)
    return ""


def move(source: str, target: str) -> str:
    """ファイルを動かす。動かせたら空文字、駄目なら理由。行き先の有無は呼び手が見る。

    同じファイルシステムの中なら rename で 1 手。またぐとき（EXDEV）だけ写して消し、
    消せなければ写した側を消して戻す（両方に残さない）。写して消す側へ流すのは EXDEV に
    限る。rename が他の理由で落ちたときまで流すと、その間に別のプロセスが置いた行き先を消す。
    """
    if _STAGE["current"] is not None:
        staged, content = _staged(source)
        if not staged:
            content = _disk_bytes(source)
        if content is None:
            return str(_missing(source))
        return _stage_put(Op(OP_MOVE, target, content, source=source))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(source, target)
        _record(source)
        _record(target)
        return ""
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            return f"{exc}"
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        remove(target)
        return f"{exc}"
    try:
        os.remove(source)
    except OSError as exc:
        remove(target)
        return f"{exc}"
    _record(source)
    _record(target)
    return ""


def write_new(path: str, content: bytes) -> str:
    """まだ無いファイルとして書く（在れば書かない）。書けたら空文字、駄目なら理由。"""
    if _STAGE["current"] is not None:
        if lexists(path):
            return str(OSError(errno.EEXIST, os.strerror(errno.EEXIST), path))
        return _stage_put(Op(OP_NEW, path, bytes(content)))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "xb") as f:
            f.write(content)
    except OSError as exc:
        return f"{exc}"
    _record(path)
    return ""


def append(path: str, data: bytes) -> str:
    """末尾に足す（`O_APPEND`、リンクは辿らない）。足せたら空文字、駄目なら理由。

    1 行をまるごと 1 回の write で出す。同じファイルに 2 つのプロセスが同時に足しても
    行が混ざらないのは POSIX の `O_APPEND` に頼っている（history.py）。
    """
    if _STAGE["current"] is not None:
        if os.path.islink(path):
            return "シンボリックリンクなので書かない"
        staged, before = _staged(path)
        if not staged:
            before = _disk_bytes(path)
        return _stage_put(Op(OP_APPEND, path, (before or b"") + data, data=data))
    try:
        if os.path.islink(path):
            return "シンボリックリンクなので書かない"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        flags = (
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
            # Windows で改行を書き換えさせない。
            | getattr(os, "O_BINARY", 0)
        )
        fd = os.open(path, flags, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        # ValueError は綴りに NUL が混ざったときなど。
        return str(exc)
    _record(path)
    return ""


def replace_bytes(path: str, content: bytes, temp_suffix: str) -> str:
    """一時ファイル（`<path><temp_suffix>`）に書いてから置き換える。駄目なら理由。

    途中で止まっても半端な中身を残さない。一時ファイルの名前が決まっているのは、
    落ちて残ったものを呼び手が名前で見分けるため（configsync の `*.ccnavi-sync`）。
    """
    if _STAGE["current"] is not None:
        return _stage_put(Op(OP_REPLACE, path, bytes(content), temp_suffix=temp_suffix))
    temp = path + temp_suffix
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp, "wb") as f:
            f.write(content)
        os.replace(temp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(temp)
        return str(exc)
    _record(path)
    return ""


def exists(path: str) -> bool:
    """在るか（リンクは辿る）。控える段があれば、そこで書いた・消した分を先に見る。"""
    staged, content = _staged(path)
    if staged:
        return content is not None
    return os.path.exists(path)


def lexists(path: str) -> bool:
    """在るか（リンクそのものも数える）。控える段は `exists` と同じに見る。"""
    staged, content = _staged(path)
    if staged:
        return content is not None
    return os.path.lexists(path)


def listdir(directory: str) -> list[str]:
    """ディレクトリの名前の一覧（並びは決めない）。無ければ `os.listdir` と同じ例外。

    控える段があれば、そこで足した名前を足し、消した名前を落とす。
    """
    stage = _STAGE["current"]
    if stage is None:
        return os.listdir(directory)
    added, dropped = stage.names_in(directory)
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        if not added:
            raise
        names = []
    return [n for n in names if n not in dropped] + [n for n in added if n not in names]


def load_text(path: str) -> str:
    """本文を読む。読めなければ `open` と同じ例外（無ければ FileNotFoundError）。

    無いのと読めないのを呼び手が分けるときに使う（`ticket.load`）。
    """
    staged, content = _staged(path)
    if staged:
        if content is None:
            raise _missing(path)
        return _decode(content)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        note_read(path, None)
        raise
    note_read(path, text)
    return text


# ---- 判定が読んだ中身（ADR-0093 の 6.2 の `read_set`。段階 2c）
#
# 読みの関数（`read_text`・`read_bytes`・`read_json`・`load_text`）は、`reading` の中だけ、
# 読んだファイルの中身の指紋を控える。無かった・読めなかったファイルも「無い」として控える
# （後から現れれば判定が変わりうる）。控える段（`staging`）から読んだ分は数えない（判定の
# 入力ではなく、plan の途中の姿）。承認の指紋（`approval.approval_digest`）がこれを使う。
#
# 中身は改行を LF に揃えた本文の SHA-256（UTF-8 として読めなければバイト列のまま）。機械の
# 改行で指紋が変わらないように（Chrome のコミットと手元の plan を LF に揃えたのと同じ理由）。

# 無い・読めないファイルの印。
READ_ABSENT = "-"
_READERS: list[dict[str, str]] = []


@contextlib.contextmanager
def reading() -> Iterator[dict[str, str]]:
    """この間に読んだファイル（絶対パス → 中身の指紋か `READ_ABSENT`）を集める。

    同じファイルを 2 度読んだら最初の中身を採る。入れ子にすると外側にも同じものが入る。
    """
    seen: dict[str, str] = {}
    _READERS.append(seen)
    try:
        yield seen
    finally:
        # 同一性で外す（`list.remove` は等価で比べるので、中身の同じ別の控えを外しうる。
        # 入れ子の内と外がどちらも空のときなど）。
        for i in range(len(_READERS) - 1, -1, -1):
            if _READERS[i] is seen:
                del _READERS[i]
                break


def note_read(path: str, content: str | bytes | None) -> None:
    """読んだ中身を控える（`reading` の外では何もしない）。fsio を通らない読みもこれを呼ぶ。"""
    if not _READERS:
        return
    key = os.path.normpath(parent_resolved(os.path.abspath(path)))
    digest = READ_ABSENT if content is None else content_digest(content)
    for seen in _READERS:
        seen.setdefault(key, digest)


def content_digest(content: str | bytes) -> str:
    """中身の指紋。改行を LF に揃えた本文の SHA-256（UTF-8 として読めなければバイト列のまま）。"""
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError:
            return hashlib.sha256(content).hexdigest()
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- この実行で書いたパスの記録（ADR-0093 の 4.3「fsio の記録層」）
#
# 書き込みの関数（`write_text`・`write_text_atomic`・`write_bytes`・`write_json_atomic`・
# `remove`・`unlink`・`move`・`write_new`・`append`・`replace_bytes`）は、書けたときに
# 行き先を控える。控えるのは `recording` の中だけ。C1（段階 2d）は sh から
# `--record-writes <ファイル>` を渡し、この一覧のパスだけをコミットする。
# 一覧に漏れがあると、書いたのにコミットされない状態が残るので、置き場を書くコードは
# ここの関数だけを通す（素の `open`・`os.remove` を使わない）。

_RECORDERS: list[dict[str, None]] = []


@contextlib.contextmanager
def recording() -> Iterator[list[str]]:
    """この間に書いた・消したパス（絶対パス、書いた順、重複なし）を集める。

    渡すリストは抜けるときに埋まる。入れ子にすると外側にも同じものが入る。
    """
    seen: dict[str, None] = {}
    out: list[str] = []
    _RECORDERS.append(seen)
    try:
        yield out
    finally:
        _RECORDERS.remove(seen)
        out.extend(seen)


def _record(path: str) -> None:
    if not _RECORDERS:
        return
    # 行き着く先の綴りで控える（ディレクトリのリンクを解く。macOS の /var → /private/var など）。
    # 最後の名前は解かない（消したファイル・リンクそのものを書いた場合も、その名前で数える）。
    key = os.path.normpath(parent_resolved(os.path.abspath(path)))
    for seen in _RECORDERS:
        seen.setdefault(key, None)


def note_input(path: str) -> None:
    """読んだ入力を、書いたものと同じく一覧に載せる（在るときだけ）。

    record-risk の記録（`<子>.judge.json`）は自分では運ばず、それを読む `finish` の C1 が運ぶ
    （ADR-0093 の 4.3）。
    """
    if _RECORDERS and os.path.lexists(path) and not os.path.islink(path):
        _record(path)


def _recorded(path: str, failed: str) -> str:
    if not failed:
        _record(path)
    return failed


# ---- 書かずに控える段（承認の plan。ADR-0093 の 6.2）
#
# `staging` の中では、書き込みの関数はディスクに書かずに `Op` を控え、読みの関数
# （`read_text`・`read_bytes`・`read_json`・`exists`・`lexists`・`listdir`・`load_text`）は
# 控えた中身を先に見る。承認の書き込みの手順（`approval.plan_batch`）をこの中で
# 動かすと、何をどの順に書くか（`Changes`）が値として出る。手元の Writer(FS) はそれを
# ディスクに書き、Chrome は同じ値を 1 コミットにする。
#
# 書けなかったときの扱いは、書く側のコードが `policy` で添える（控える段では書き込みが
# 落ちないので、落ちたときの枝のコードは走らない）。

OP_TEXT = "text"  # write_text
OP_TEXT_ATOMIC = "text-atomic"  # write_text_atomic / write_json_atomic
OP_BYTES = "bytes"  # write_bytes
OP_NEW = "new"  # write_new（在れば書かない）
OP_REMOVE = "remove"  # remove（無くても消せなくても黙る）
OP_UNLINK = "unlink"  # unlink（消せなければ理由）
OP_MOVE = "move"  # move（path が行き先、source が元）
OP_APPEND = "append"  # append（data を足す。content は足した後の全体）
OP_REPLACE = "replace"  # replace_bytes

# 書けなかったときの扱い。
FAIL_STOP = "stop"  # そこで止め、理由を呼び手へ返す
FAIL_WARN = "warn"  # 標準エラーに言って続ける
FAIL_LINE = "line"  # 知らせる行を標準出力に出し、同じ組の残りを飛ばす
FAIL_QUIET = "quiet"  # 黙って続ける
FAIL_HISTORY = "history"  # 跡の書けなかった知らせに溜めて続ける（history.py）


@dataclass(frozen=True)
class Policy:
    """書けなかったときの扱い。`message` の `{reason}` に理由が入る。

    `undo` は落ちたときに消すパス（書いた側を戻して、両方に残さない）。`places` は、
    書けたらその識別子を「置いた」と数える。`group` が同じ行と書き込みは 1 つの組で、
    `FAIL_LINE` で落ちたら残りを飛ばす。`ticket` は知らせの頭に付ける識別子。
    `prefix` は `message` の前に付ける語（呼び手の用件。「マーカーを置けない: 」など）。
    `tag` は書けたときに組ごとに控える名札で、`Call` が「実際に書けたもの」を知るのに使う。
    """

    on_fail: str = FAIL_STOP
    message: str = "{reason}"
    prefix: str = ""
    undo: tuple[str, ...] = ()
    places: str = ""
    group: int = 0
    ticket: str = ""
    tag: str = ""


def failure_text(policy: Policy, reason: str) -> str:
    """書けなかった知らせの本文。`{reason}` だけを差し込む（綴りの `{` `}` は解釈しない）。"""
    return policy.prefix + policy.message.replace("{reason}", reason)


@dataclass
class Op:
    """控えた書き込み 1 つ。`content` は書いた後の中身（消したものは None）。"""

    kind: str
    path: str
    content: bytes | None
    text: str = ""
    newline: str | None = None
    source: str = ""
    data: bytes = b""
    temp_suffix: str = ""
    policy: Policy = field(default_factory=Policy)
    # 見え方（Changes）にだけ載せ、Writer(FS) は書かない。同じ中身を `Call` が書く（跡）。
    view_only: bool = False


@dataclass
class Call:
    """Writer(FS) が、同じ組の書き込みを済ませた後に呼ぶ手順。

    `run` は組の中で書けた `tag` の集まりを受け、見せる行を返す。書けたものに合わせて
    行と跡を書く所（マーカーの消去）で使う。控える段では呼ばない。
    """

    run: Callable[[set[str]], list[str]]
    group: int = 0
    # 全部書けたときに出る行（Changes の見せる行。Chrome は 1 コミットで全部書く）。
    lines: list[str] = field(default_factory=list)


STREAM_OUT = "stdout"
STREAM_ERR = "stderr"


@dataclass
class Line:
    """人に見せる 1 行。書き込みと同じ並びに置き、同じ組が落ちたら出さない。"""

    text: str
    group: int = 0
    stream: str = STREAM_OUT


class Stage:
    """控えた書き込みの並びと、控えた後の中身の見え方。"""

    def __init__(self) -> None:
        self.items: list[Op | Line | Call] = []
        self._view: dict[str, tuple[str, bytes | None]] = {}
        # 最初に控えたときに、ディスクに在ったか（create と update を分けるため）。
        self._before: dict[str, bool] = {}
        self._groups = 0

    def new_group(self) -> int:
        self._groups += 1
        return self._groups

    def line(self, text: str, group: int = 0, stream: str = STREAM_OUT) -> None:
        self.items.append(Line(text, group, stream))

    def get(self, path: str) -> tuple[bool, bytes | None]:
        found = self._view.get(_key(path))
        return (False, None) if found is None else (True, found[1])

    def put(self, path: str, content: bytes | None) -> None:
        key = _key(path)
        if key not in self._before:
            self._before[key] = os.path.lexists(path)
        self._view[key] = (os.path.normpath(os.path.abspath(path)), content)

    def names_in(self, directory: str) -> tuple[list[str], set[str]]:
        """このディレクトリの直下で、控えた書き込みが足した名前と消した名前。"""
        base = _key(directory)
        added: list[str] = []
        dropped: set[str] = set()
        for key, (spelled, content) in self._view.items():
            if os.path.dirname(key) != base:
                continue
            name = os.path.basename(spelled)
            if content is None:
                dropped.add(name)
            else:
                added.append(name)
        return added, dropped

    def touched(self) -> list[tuple[str, bytes | None, bool]]:
        """控えた後の中身（書いた順）。消したものは None。3 つめは控える前にディスクに在ったか。"""
        return [
            (spelled, content, self._before[key]) for key, (spelled, content) in self._view.items()
        ]


_STAGE: dict = {"current": None, "policy": Policy(), "view_only": False}


@contextlib.contextmanager
def staging() -> Iterator[Stage]:
    """この間の書き込みをディスクに書かずに控える。入れ子にはしない。"""
    if _STAGE["current"] is not None:
        raise RuntimeError("控える段は入れ子にしない")
    stage = Stage()
    _STAGE["current"] = stage
    try:
        yield stage
    finally:
        _STAGE["current"] = None


def current_stage() -> Stage | None:
    return _STAGE["current"]


@contextlib.contextmanager
def policy(**changes: Any) -> Iterator[None]:
    """この間に控える書き込みの、書けなかったときの扱い。外側の扱いを上書きする。"""
    before = _STAGE["policy"]
    _STAGE["policy"] = Policy(**{**before.__dict__, **changes})
    try:
        yield
    finally:
        _STAGE["policy"] = before


@contextlib.contextmanager
def view_only() -> Iterator[None]:
    """この間に控える書き込みは見え方（Changes）にだけ載せ、Writer(FS) は書かない。"""
    before = _STAGE["view_only"]
    _STAGE["view_only"] = True
    try:
        yield
    finally:
        _STAGE["view_only"] = before


def _stage_put(op: Op, quiet: bool = False) -> str:
    stage: Stage = _STAGE["current"]
    op.policy = _STAGE["policy"]
    op.view_only = _STAGE["view_only"]
    if quiet:
        op.policy = Policy(**{**op.policy.__dict__, "on_fail": FAIL_QUIET})
    if op.kind == OP_MOVE:
        stage.put(op.source, None)
    stage.put(op.path, op.content)
    stage.items.append(op)
    return ""


def _staged(path: str) -> tuple[bool, bytes | None]:
    stage = _STAGE["current"]
    if stage is None:
        return False, None
    return stage.get(path)


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _disk_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _missing(path: str) -> FileNotFoundError:
    return FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)


def _encode(text: str, newline: str | None) -> bytes:
    """控える中身のバイト列。改行は `newline` を明示したときだけ書き換える。

    `newline=None`（`open` の既定）はディスクでは機械の改行（Windows は CRLF）になるが、
    Changes の中身は LF に固定する。Chrome のコミットと手元の plan が機械に依らず同じ
    バイト列になるように。ディスクへは Writer(FS) が元の `newline` で書くので、手元の
    ファイルは前と同じ。
    """
    if newline not in (None, "", "\n"):
        text = text.replace("\n", newline)
    return text.encode("utf-8")


def _decode(content: bytes, errors: str = "strict") -> str:
    """`open(path, encoding="utf-8")` が読むのと同じ本文（改行は `\\n` に揃う）。"""
    text = content.decode("utf-8", errors=errors)
    return text.replace("\r\n", "\n").replace("\r", "\n")
