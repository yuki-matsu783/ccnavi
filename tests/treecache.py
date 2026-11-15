"""テストの木を組む手順の結果を取っておき、次のテストでは同じ絶対パスへ戻す。

親子のチケットの受入テストは、テストごとに git のリポジトリとワークツリーを組み、承認・着手・
依頼まで実行ファイルを何度も起こしてから、確かめたい 1 手を打つ。組む手順はクラスの中で同じなので、
1 度目の結果を写しに取り、2 度目からは写しへ戻す（プロセスを起こさない）。

- 木はクラスごとに決まった置き場（`root(test)` の親の作業用ディレクトリ）に組む。git の
  ワークツリー・origin・状態の置き場は絶対パスで互いを指すので、写しは同じパスへ戻す
- `@cached` を付けた手順は、呼ぶ前の木の中身（パス・権限・中身）・引数・テストの属性が
  前に呼んだときと同じなら、木を写しへ戻し、そのとき変わった属性と返り値を当てる。どれかが違えば
  ふつうに走らせ、結果を取っておく。更新時刻は鍵に入れないので、更新時刻だけを変えた木から呼ぶと、
  変わったファイルは写しの更新時刻に戻る
- 戻すときは、いまの木と写しの違うファイルだけを消す・写す（木を丸ごと写し直さない）。
  `root(test)` を呼んだ後の最初の手順は、前のテストが残した木を空の木とみなして戻す。テストどうしで
  状態は持ち越さない（いつも写しと同じ中身から始まる）
- `@cached` を付ける手順は、テストの属性への代入と `root(test)` の下への書き込みだけで済ませる。
  属性の値は浅く取っておくので、リスト・辞書の中身の書き換え（`self.x.append(...)`）は再生で戻らない。
  `root(test)` の外へ書いたものも写しに入らず、戻らない
"""

from __future__ import annotations

import atexit
import copy
import functools
import hashlib
import os
import shutil
import stat
import tempfile

_WORK = "work"
_ROOT = "root"
# 鍵に入れる属性の値の型。ほかの型は型の名前だけを入れる（repr にアドレスが混ざるもの）。
_PLAIN = (str, int, float, bool, type(None), tuple, list, dict)
# 空の木の中身の印。
_EMPTY = "empty"


def _force_remove(function, path, _error):
    """読み取り専用のファイル（git の objects）も消す。Windows では書き込みを許さないと消せない。"""
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, onexc=_force_remove)
    elif os.path.lexists(path):
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)


def _listing(top):
    """木の中身。相対パス → (種類, 権限, 中身のハッシュ)。無ければ空。"""
    found = {}
    for here, dirs, files in os.walk(top):
        rel = os.path.relpath(here, top)
        for name in dirs + files:
            path = os.path.join(here, name)
            key = os.path.normpath(os.path.join(rel, name))
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                found[key] = ("l", 0, os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                found[key] = ("d", 0, "")
            elif stat.S_ISREG(info.st_mode):
                with open(path, "rb") as f:
                    digest = hashlib.sha1(f.read()).hexdigest()
                found[key] = ("f", stat.S_IMODE(info.st_mode), digest)
            else:
                # FIFO などは開くと待つので読まない。FIFO を含む木は写しを取れない
                # （写すときの copytree が止まる）。
                found[key] = ("o", info.st_mode, "")
    return found


def _fingerprint(listing):
    return hashlib.sha1(repr(sorted(listing.items())).encode()).hexdigest()


def _sync(work, now, snap, wanted):
    """work（中身は now）を、写し snap（中身は wanted）と同じ中身にする。違う所だけを触る。"""
    for rel in sorted(now, reverse=True):
        if rel not in wanted or wanted[rel][0] != now[rel][0]:
            _remove(os.path.join(work, rel))
    os.makedirs(work, exist_ok=True)
    for rel in sorted(wanted):
        kind, _mode, value = wanted[rel]
        target = os.path.join(work, rel)
        if kind == "d":
            os.makedirs(target, exist_ok=True)
        elif now.get(rel) != wanted[rel]:
            _remove(target)
            if kind == "l":
                os.symlink(value, target)
            else:
                shutil.copy2(os.path.join(snap, rel), target)


class _Home:
    """1 つのクラスの作業用ディレクトリと、取っておいた写し。"""

    def __init__(self, prefix):
        self.home = tempfile.mkdtemp(prefix=prefix)
        atexit.register(_remove, self.home)
        self.work = os.path.join(self.home, _WORK)
        self.saved = {}
        # root(test) の直後。前のテストが残した木を空の木とみなす。
        self.fresh = True

    def step(self, test, method, args, kw):
        now = _listing(self.work)
        state = _EMPTY if self.fresh else _fingerprint(now)
        key = (method.__qualname__, repr(args), repr(sorted(kw.items())), _attrs_key(test), state)
        hit = self.saved.get(key)
        if hit is not None:
            snap, wanted, changed, result = hit
            _sync(self.work, now, snap, wanted)
            self.fresh = False
            test.__dict__.update(copy.deepcopy(changed))
            return copy.deepcopy(result)
        if self.fresh:
            _remove(self.work)
            os.makedirs(self.work)
            self.fresh = False
        before = dict(_public(test))
        result = method(test, *args, **kw)
        changed = {k: v for k, v in _public(test) if k not in before or before[k] is not v}
        snap = os.path.join(self.home, f"snap-{len(self.saved)}")
        shutil.copytree(self.work, snap, symlinks=True)
        self.saved[key] = (snap, _listing(snap), copy.deepcopy(changed), copy.deepcopy(result))
        return result


_HOMES: dict[type, _Home] = {}


def _home(test):
    cls = type(test)
    if cls not in _HOMES:
        _HOMES[cls] = _Home("ccnavi-tree-")
    return _HOMES[cls]


def _public(test):
    return [(k, v) for k, v in vars(test).items() if not k.startswith("_")]


def _attrs_key(test):
    return repr(
        sorted(
            (k, repr(v) if isinstance(v, _PLAIN) else type(v).__name__) for k, v in _public(test)
        )
    )


def root(test):
    """テストの木のルート。クラスごとに同じパス。この後の最初の `@cached` の手順が木を空から組む
    （前のテストが残した木は、その手順が写しへ戻すか消す）。"""
    home = _home(test)
    home.fresh = True
    return os.path.join(home.work, _ROOT)


def cached(method):
    """木を組む手順の結果を取っておき、同じ木・引数・属性から呼ばれたら写しへ戻す。"""

    @functools.wraps(method)
    def run(self, *args, **kw):
        return _home(self).step(self, method, args, kw)

    return run
