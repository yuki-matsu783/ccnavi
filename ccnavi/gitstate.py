"""作業ツリーで実際に何が変わったかを git に聞く。

## なぜ実物を見るか

実行前の判定はツール呼び出しの引数しか見ていない。引数に現れない書き込みは
そこを素通りする。ビルドが出力先を持っている、許可されたスクリプトが内部で
open する、想定していないシェル構文で書く。どれも「何が起きるか」を引数から
言い当てる限り追いつけないので、実行後に「何が起きたか」を見る側が要る。

## なぜここだけ外部プロセスを起こすか

配布物の条件は「判定の間に外部プロセスを起こさない」。それは実行前の判定に
掛かる条件で、あそこはツール呼び出しを止めている最中であり、遅れがそのまま
エージェントの待ち時間になる。加えて期限に達した hook は素通りするので、
遅さがガードの穴に化ける。

実行後は止めていない。すでに走ったものについて後から言うだけなので、遅れは
待ち時間にしかならず、読めなければ何も言わないだけで、穴も開かない。そのうえ
「作業ツリーの今の状態」を自前で持つには、保護領域に当たりうるファイルを
毎回歩いて指紋を取り続けることになり、それは git がすでに持っている情報である。
持っている側に聞く。

聞く相手はローカルの git だけで、ネットワークへは出ない。

## 何が見えないか

git の管理から外れたファイルは見えない。`.gitignore` に入っているもの、
作業ツリーの外にあるもの。ccnavi 自身の記録と状態がここに入るので、
自分の書き込みを自分の違反として報告することはない。代わりに、無視されている
場所に置かれた保護対象は最初から見えない。この限界は README に書いてある。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

# 変更の種類。元に戻す手順がこの 3 つで割れるので、この 3 つにしてある。
KIND_NEW = "new"  # 無かったものが現れた
KIND_CHANGED = "changed"  # 中身が変わった
KIND_GONE = "gone"  # 追跡されていたものが消えた

# git に与える時間。実行前の判定に張る期限より短くしてある。
# 大きなリポジトリでは status も待たされるが、待たせるくらいなら何も言わない。
TIMEOUT_SECONDS = 2.0

# 読めなかった理由。記録に入れるので、監視が動いていない期間を後から数えられる。
REASON_NO_WORKTREE = "not-a-git-worktree"
REASON_NO_GIT = "git-not-found"
REASON_FAILED = "git-failed"
REASON_TIMEOUT = "git-timed-out"


@dataclass
class Change:
    """作業ツリーの変更 1 件。"""

    kind: str = ""
    # path は git が返した綴り。作業ツリーのルートからの相対で、区切りは "/"。
    # 人に見せる側と、元に戻す手順で git に渡す側は、こちらを使う。
    path: str = ""
    # full は行き着く先まで解いた絶対パス。ルールを当てるのはこちら。
    # 実行前の判定がファイルのパスを解いてから当てるのと同じ理由で、
    # 綴りを変えただけでルールを外せないようにする。
    full: str = ""
    # status は git の 2 文字。索引側と作業ツリー側。報告にそのまま載せる。
    status: str = ""
    staged: bool = False

    def key(self) -> str:
        """同じ変更を同じものとして数えるための名前。

        種類まで含める。中身を変えられたファイルが次に消されたら、それは
        すでに知っている変更ではなく新しい出来事なので、もう一度言う。
        """
        return f"{self.kind} {self.path}"


def top_level(root: str) -> str:
    """root から上へ辿って作業ツリーのルートを探す。見つからなければ空文字。

    git に聞かずに自分で探す。ここで `git rev-parse` を起こすと、git の無い
    環境と git はあるがリポジトリではない環境の両方で、何も得られないまま
    プロセスを 1 つ余計に起こすことになる。`.git` はディレクトリのことも
    ファイルのこともある。worktree と submodule の中では後者。
    """
    directory = os.path.abspath(root)
    while True:
        if os.path.exists(os.path.join(directory, ".git")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return ""
        directory = parent


def read(top: str, timeout: float = TIMEOUT_SECONDS) -> tuple[list[Change], str]:
    """作業ツリーの未コミットの変更を返す。読めなければ理由を返す。

    読めないことは失敗として扱わない。実行後の監視は止める手段を持たないので、
    ここで何かを拒むことはできないし、拒めない以上、読めなかったときに
    できるのは「読めなかった」と記録することだけになる。
    """
    if not top:
        return [], REASON_NO_WORKTREE

    try:
        done = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                # -z は綴りをそのまま NUL 区切りで返す。既定の出力は空白や
                # 非 ASCII を含むパスを引用符で包んで自前の escape を掛けるので、
                # 読み戻す側がその escape を解く羽目になる。解き損ねたパスは
                # 保護領域のルールを外す。
                "-z",
                # 無視されていない未追跡ファイルは 1 件ずつ挙げる。既定では
                # ディレクトリ 1 つにまとめられ、その下の何が現れたかが消える。
                "--untracked-files=all",
                # 改名を追わせない。追わせると 1 件が 2 つのパスを持つ形になり、
                # 読み戻しが増える。追わなければ「消えた」と「現れた」の
                # 2 件になり、元に戻す手順はどちらにしても 2 つ要る。
                "--no-renames",
            ],
            cwd=top,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return [], REASON_NO_GIT
    except subprocess.TimeoutExpired:
        return [], REASON_TIMEOUT
    except OSError:
        return [], REASON_FAILED

    if done.returncode != 0:
        return [], REASON_FAILED

    return [c for c in (_parse(top, entry) for entry in done.stdout.split("\0")) if c], ""


def _parse(top: str, entry: str) -> Change | None:
    # "XY path" の形。X は索引側、Y は作業ツリー側の状態。
    if len(entry) < 4 or entry[2] != " ":
        return None
    status, path = entry[:2], entry[3:]
    index, worktree = status[0], status[1]

    if status == "??":
        kind, staged = KIND_NEW, False
    elif index == "A":
        kind, staged = KIND_NEW, True
    elif "D" in (index, worktree):
        kind, staged = KIND_GONE, index != " "
    else:
        kind, staged = KIND_CHANGED, index != " "

    return Change(
        kind=kind,
        path=path,
        full=_full(top, path),
        status=status,
        staged=staged,
    )


def _full(top: str, path: str) -> str:
    """git の綴りを、行き着く先が 1 つに決まる絶対パスに直す。

    cli.full_path と同じことを、同じ理由でやっている。消えたファイルは
    解けないので、絶対パスにして `..` を畳むところまでで止める。
    """
    joined = os.path.join(top, path)
    try:
        return os.path.realpath(joined)
    except OSError:
        return os.path.normpath(os.path.abspath(joined))


def undo(change: Change) -> str:
    """この 1 件を元に戻すコマンド。人とエージェントが読む側。

    1 件に 1 つだけ返す。数えられる手順でないと、受け取った側は自分で
    組み立て直すことになり、そこで対象が増えたり減ったりする。
    """
    quoted = f'"{change.path}"'
    if change.kind != KIND_NEW:
        return f"git restore --staged --worktree -- {quoted}"
    if change.staged:
        return f"git rm -f -- {quoted}"
    return f"git clean -f -- {quoted}"


def restore(top: str, change: Change, aside: str, timeout: float = TIMEOUT_SECONDS) -> str:
    """この 1 件を実際に元に戻す。戻せたら空文字、駄目なら理由を返す。

    現れたファイルは消さずに退避する。設計は削除と書いているが、消してしまうと
    「保護領域を汚した実行」と「保護領域に間違って出力しただけの、中身は
    要るもの」を、戻す側が見分けられないまま片方を選ぶことになる。取り返しの
    付かない操作を止めるための道具が、自分だけは取り返しの付かない操作を
    するのはおかしい。退避先は報告に載せるので、要るものだったなら拾い直せる。
    """
    if change.kind != KIND_NEW:
        return _git(top, ["restore", "--staged", "--worktree", "--", change.path], timeout)

    if change.staged:
        # 索引から落としてから動かす。先に動かすと索引に消えたファイルへの
        # 追加が残り、次の status が「消えた」を新しい変更として持ち出す。
        failed = _git(top, ["rm", "--cached", "--", change.path], timeout)
        if failed:
            return failed

    source = os.path.join(top, change.path)
    target = os.path.join(aside, change.path.replace("/", os.sep))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(source, target)
    except OSError as exc:
        return f"{exc}"
    return ""


def restore_committed(top: str, path: str, timeout: float = TIMEOUT_SECONDS) -> str:
    """名指しした 1 つのパスを、コミット済みの内容へ戻す。
    戻せたら空文字、駄目なら理由を返す。

    Change を経由しない口を分けてあるのは、呼ぶ側の出発点が違うから。
    上の restore は「git が変更として返したもの」を戻すが、こちらは
    「控えが無いので git に頼るしかないもの」を戻す。後者には Change が無い。
    git が追っていないパスなら失敗して戻り、呼び手はそれを報告に載せる。
    黙って成功したことにはしない。控えも git も無い場所は、戻せない場所なので。
    """
    return _git(top, ["restore", "--staged", "--worktree", "--", path], timeout)


def _git(top: str, args: list[str], timeout: float) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=top,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{exc}"
    if done.returncode != 0:
        return " ".join(done.stderr.split()) or f"git {args[0]} が失敗した"
    return ""
