"""ワークツリーで実際に何が変わったかを git に聞く。

## なぜ実物を見るか

実行前チェックはツール呼び出しの引数しか見ていない。引数に現れない書き込みは
そこで止められずに通る。ビルドが出力先を持っている、許可されたスクリプトが内部で
open する、想定していないシェル構文で書く。どれも「何が起きるか」を引数から
言い適用する限り追いつけないので、実行後に「何が起きたか」を見る側が要る。

## なぜここだけ外部プロセスを起こすか

配布物の条件は「判定の間に外部プロセスを起こさない」。それは実行前チェックに
該当する条件で、あそこはツール呼び出しを止めている最中であり、遅れがそのまま
エージェントの待ち時間になる。加えて期限に達した hook の呼び出しは止められずに通るので、
遅さがガードの穴になってしまう。

実行後は止めていない。すでに走ったものについて後から言うだけなので、遅れは
待ち時間にしかならず、読めなければ何も言わないだけで、穴も開かない。そのうえ
「ワークツリーの今の状態」を自前で持つには、保護領域にヒットしうるファイルを
毎回歩いてハッシュを取り続けることになり、それは git がすでに持っている情報である。
持っている側に聞く。

聞く相手はローカルの git だけで、ネットワークへは出ない。

## 何が見えないか

git の管理から外れたファイルは見えない。`.gitignore` に入っているもの、
ワークツリーの外にあるもの。ccnavi 自身の記録と状態がここに入るので、
自分の書き込みを自分の違反として報告することはない。代わりに、無視されている
場所に置かれた保護領域は最初から見えない。
この限界は docs/41_guide/hooks.md の「見えないもの」に書いてある。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from . import fsio, gitcmd

# 変更の種類。元に戻す手順がこの 3 つで分かれるので、この 3 つにしてある。
KIND_NEW = "new"  # 無かったものが現れた
KIND_CHANGED = "changed"  # 中身が変わった
KIND_GONE = "gone"  # 追跡されていたものが消えた

# 4 つめは戻す手順を持たない。ターンの間にコミットに入ったもので、`git status` には
# もう出ない。戻すには revert か履歴の書き換えが要り、どちらもラッパースクリプトが
# 拒む（ユーザの判断で行う操作）。
# 報告のためだけに在る種類。
KIND_COMMITTED = "committed"

# ユーザへ見せる表記。記録（`record.paths`）には上の英語のまま残し、報告の文面だけ日本語にする。
KIND_LABELS = {
    KIND_NEW: "新規",
    KIND_CHANGED: "変更",
    KIND_GONE: "削除",
    KIND_COMMITTED: "コミット済み",
}

# git に与える時間。実行前チェックに張る期限より短くしてある。
# 大きなリポジトリでは status も待たされるが、待たせるくらいなら何も言わない。
TIMEOUT_SECONDS = 2.0

# index を書き換える git（restore と rm --cached）に与える時間。読む側より長く待つ。
# これらは `.git/index.lock` を必ず取り、期限で殺すとその lock が残って、以後の
# add や commit が止まる。1 回が hook の期限（.claude/settings.json の 10 秒）に収まる長さにする。
WRITE_TIMEOUT_SECONDS = 5.0

# 読めなかった理由。記録に入れるので、実行後チェックが動いていない期間を後から数えられる。
REASON_NO_WORKTREE = "not-a-git-worktree"
REASON_NO_GIT = "git-not-found"
REASON_FAILED = "git-failed"
REASON_TIMEOUT = "git-timed-out"


@dataclass
class Change:
    """ワークツリーの変更 1 件。"""

    kind: str = ""
    # path は git が返したパス。ワークツリーのルートからの相対で、区切りは "/"。
    # ユーザに見せる側と、元に戻す手順で git に渡す側は、こちらを使う。
    path: str = ""
    # full は行き着く先まで解いた絶対パス。ルールを適用するのはこちら。
    # 実行前チェックがファイルのパスを解いてから適用するのと同じ理由で、
    # 表記を変えただけでルールを外せないようにする。
    full: str = ""
    # status は git の 2 文字。索引側とワークツリー側。報告にそのまま載せる。
    status: str = ""
    staged: bool = False

    def key(self) -> str:
        """同じ変更を同じものとして数えるための名前。

        種類まで含める。中身を変えられたファイルが次に消されたら、それは
        すでに知っている変更ではなく新しい出来事なので、もう一度言う。
        """
        return f"{self.kind} {self.path}"


def top_level(root: str) -> str:
    """root から上へ辿ってワークツリーのルートを探す。見つからなければ空文字。

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
    """ワークツリーの未コミットの変更を返す。読めなければ理由を返す。

    実行後チェックは止める手段を持たないので、読めなかったときに
    できるのは「読めなかった」と記録することだけになる。
    """
    if not top:
        return [], REASON_NO_WORKTREE

    done = gitcmd.run(
        top,
        [
            "status",
            "--porcelain",
            # -z はパスをそのまま NUL 区切りで返す。既定の出力は空白や
            # 非 ASCII を含むパスを引用符で囲んで自前の escape を掛けるので、
            # 読み戻す側がその escape を解く羽目になる。解き損ねたパスには
            # 保護領域のルールがヒットしない。
            "-z",
            # 無視されていない未追跡ファイルは 1 件ずつ挙げる。既定では
            # ディレクトリ 1 つにまとめられ、その下の何が現れたかが分からなくなる。
            "--untracked-files=all",
            # 改名を追わせない。追わせると 1 件が 2 つのパスを持つ形になり、
            # 読み戻しが増える。追わなければ「消えた」と「現れた」の
            # 2 件になり、元に戻す手順はどちらにしても 2 つ要る。
            "--no-renames",
        ],
        timeout,
    )
    if done.missing:
        return [], REASON_NO_GIT
    if done.timed_out:
        return [], REASON_TIMEOUT
    if not done.ok:
        return [], REASON_FAILED

    return [c for c in (_parse(top, entry) for entry in done.out.split("\0")) if c], ""


def head(top: str, timeout: float = TIMEOUT_SECONDS) -> str:
    """いまの HEAD。読めなければ空文字。

    ターンの始まりに記録して、終わりに「このターンで何がコミットに入ったか」を
    数えるための基準にする（post.at_prompt / post.at_stop）。
    """
    if not top:
        return ""
    done = gitcmd.run(top, ["rev-parse", "HEAD"], timeout)
    return done.out.strip() if done.ok else ""


def fork_base(top: str, others: list[str], timeout: float = TIMEOUT_SECONDS) -> str:
    """`others`（ターンの始まりに記録した別のツリーの HEAD）から、このツリーが分かれた点。

    ターンの途中で切ったワークツリーは、ターンの始まりの HEAD を持たない。切った元の
    コミットが記録済みの HEAD の祖先にあたるので、merge-base がその代わりになる。
    どれとも共通の祖先が無い、読めない、ときは空文字（数えられない側へ倒す）。
    """
    if not top:
        return ""
    for other in others:
        done = gitcmd.run(top, ["merge-base", "HEAD", other], timeout)
        if done.ok and done.out.strip():
            return done.out.strip().splitlines()[0]
    return ""


def child_base(
    child_tree: str, parent_tree: str, timeout: float = TIMEOUT_SECONDS, child_head: str = "HEAD"
) -> tuple[str, str]:
    """子のワークツリーで、子の仕事の始まり。`(コミット, 読めなかった理由)`。

    親のワークツリーの HEAD（取り込み直後の親の先頭）と、子の HEAD の共通の祖先。
    子の範囲の検査・実績のリスクの計測・finish の合流の前の検査は、ここから子の先頭までを見る。
    着手のときの基準点（`base_sha`）から数えないのは、衝突を解くために子へ親を取り込んだとき、
    親に入っているほかの子の変更が、この子の変更に混ざるから。親を取り込んだ子は、取り込んだ
    親の先頭が始まりになる。既に親へ合流した子が続きを積んだときは、合流した先頭が始まりになる。
    `child_head` を渡すと、子の HEAD の代わりにその sha から求める（先頭を 1 回だけ読んで使う側）。
    """
    if not child_tree or not parent_tree:
        return "", "子か親のワークツリーが決まらない"
    parent = head(parent_tree, timeout)
    if not parent:
        return "", f"親のワークツリー（{parent_tree}）の HEAD を読めない"
    done = gitcmd.run(child_tree, ["merge-base", child_head or "HEAD", parent], timeout)
    found = done.out.strip().splitlines()[0] if done.ok and done.out.strip() else ""
    if not found:
        return "", "子のブランチと親のブランチの共通の祖先を読めない"
    return found, ""


def branch_names(top: str, timeout: float = TIMEOUT_SECONDS) -> set[str]:
    """手元のブランチと origin のブランチの名前。

    `refs/heads/` と `refs/remotes/origin/` を外した表記で返す。

    新規の提案の親のブランチ名が既にあるかを確かめるのに使う。refname は
    `core.quotePath` の引用を受けないので、日本語の名前もそのまま返る。読めなければ空。
    `origin/HEAD`（デフォルトブランチを指す別名）は数えない。
    """
    if not top or not os.path.isdir(top):
        return set()
    done = gitcmd.run(
        top,
        ["for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/origin/"],
        timeout,
    )
    if not done.ok:
        return set()
    names: set[str] = set()
    for line in done.out.splitlines():
        for prefix in ("refs/heads/", "refs/remotes/origin/"):
            if line.startswith(prefix):
                name = line[len(prefix) :]
                if name and not (prefix == "refs/remotes/origin/" and name == "HEAD"):
                    names.add(name)
                break
    return names


def committed(top: str, base: str, timeout: float = TIMEOUT_SECONDS) -> tuple[list[Change], str]:
    """`base..HEAD` でコミットに入ったパス。読めなければ理由を返す。

    未コミットしか見ない `read` の穴を、ターンの終わりだけ埋める。保護領域を
    汚してからコミットすると `git status` から消えるので、`read` だけでは
    「何も起きなかった」と同じ見た目になる。

    数えるのはこのツリーが積んだコミットだけ（`--first-parent --no-merges`）。
    二点の差分（`base..HEAD`）にすると、統合先を取り込んだマージが持ち込んだ
    コミットまで「このターンでコミットに入った」ことになる。ワークツリーを切って
    作業し、`merge <統合先>` を打ってから統合先へ取り込むのがこのリポジトリの手順なので、
    それを打つたびに、ユーザが統合先で直した保護領域が毎回報告に並ぶ。

    `--no-renames` と `--ignore-submodules=none` は phase_scope.scope_findings と同じ理由。
    改名を 1 行にまとめられると移動元が消え、submodule の進みは `.gitmodules` の
    `ignore = all` で丸ごと消える。どちらも差分から行が消える経路になる。
    """
    if not top or not base:
        return [], REASON_NO_WORKTREE if not top else ""
    done = gitcmd.run(
        top,
        [
            "log",
            "--first-parent",
            "--no-merges",
            "--format=",
            "--name-only",
            "--no-renames",
            "--ignore-submodules=none",
            "-z",
            f"{base}..HEAD",
        ],
        timeout,
    )
    if done.missing:
        return [], REASON_NO_GIT
    if done.timed_out:
        return [], REASON_TIMEOUT
    if not done.ok:
        return [], REASON_FAILED
    # 同じパスが複数のコミットに現れるのでまとめる。報告は「何が変わったか」で、
    # 何回変わったかではない。順序は git が返した順のまま。
    seen: set[str] = set()
    changes = []
    for path in done.out.split("\0"):
        if not path or path in seen:
            continue
        seen.add(path)
        changes.append(
            Change(kind=KIND_COMMITTED, path=path, full=_full(top, path), status="", staged=True)
        )
    return changes, ""


def _parse(top: str, entry: str) -> Change | None:
    # "XY path" の形。X は索引側、Y はワークツリー側の状態。
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
    """git が返したパスを、行き着く先が 1 つに決まる絶対パスに直す。

    実行前チェックと同じ関数（`fsio.full_path`）を通す。同じ場所が 2 通りのパスで
    ヒットすると、実行前に通った書き込みが実行後に報告される（あるいはその逆）。
    """
    return fsio.full_path(path, top)


def undo(change: Change, git: str | None = None) -> str:
    """この 1 件を元に戻すコマンド。ユーザとエージェントが読む側。

    1 件に 1 つだけ返す。数えられる手順でないと、受け取った側は自分で
    組み立て直すことになり、そこで対象が増えたり減ったりする。

    git はラッパーの打ち方（`settings.git_wrapper_command`）。ラッパーが無いワークスペースでは
    生の git も止まるので、手順は返さず空を返す（呼ぶ側がユーザに頼む文にする）。ラッパーは
    `rm -f` と `clean` を通さないので、現れたファイルは索引から外す `restore --staged` と
    シェルの `rm` で消す。
    """
    if change.kind == KIND_COMMITTED:
        # コミット済みには 1 つに決まる手順が無い。空を返し、呼ぶ側が
        # 「戻す手順」の行そのものを省く。間違った手順を 1 行書くより、
        # 書かないほうがよい（revert も reset もラッパースクリプトが拒む）。
        return ""
    if git is None:
        return ""
    quoted = f'"{change.path}"'
    if change.kind != KIND_NEW:
        return f"{git} restore --staged --worktree -- {quoted}"
    if change.staged:
        return f"{git} restore --staged -- {quoted} && rm -- {quoted}"
    return f"rm -- {quoted}"


def restore(top: str, change: Change, aside: str, timeout: float = WRITE_TIMEOUT_SECONDS) -> str:
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
        # 索引から外してから動かす。先に動かすと索引に消えたファイルへの
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


def restore_committed(top: str, path: str, timeout: float = WRITE_TIMEOUT_SECONDS) -> str:
    """名指しした 1 つのパスを、コミット済みの内容へ戻す。
    戻せたら空文字、駄目なら理由を返す。

    Change を経由しない関数を分けてあるのは、呼ぶ側の出発点が違うから。
    上の restore は「git が変更として返したもの」を戻すが、こちらは
    「バックアップが無いので git に頼るしかないもの」を戻す。後者には Change が無い。
    git が追っていないパスなら失敗して戻り、呼び手はそれを報告に載せる。
    何も言わずに成功したことにはしない。バックアップも git も無い場所は、戻せない場所なので。
    """
    return _git(top, ["restore", "--staged", "--worktree", "--", path], timeout)


def committed_text(
    top: str, path: str, timeout: float = TIMEOUT_SECONDS
) -> tuple[str | None, bool]:
    """HEAD に入っているその 1 本の中身と、読めたかどうか。

    返すのは `(中身, 読めた)`。HEAD にそのパスが無ければ `(None, True)`。「無い」は
    読めた答えなので、読めたものとして返す。読めなかったとき（git を起こせない・期限に達した・
    HEAD そのものが無いとき）は `(None, False)`。

    呼ぶ側は「読めなかった」を「変わっていない」として扱ってはいけない。そう扱うと、
    git を 2 秒止めるだけで、突き合わせを前提にした除外が全部通る。

    無いことの確かめに `ls-tree` をもう 1 回起こすのは、`show` の失敗が「HEAD に無い」
    なのか「HEAD が無い」なのかを、文面を読まずに分けるため。git の文面は言語設定で
    変わるので、そこで分けると機械によって答えが変わる。起こす回数が増えるのは、
    新しく現れたファイルがチケットの置き場にあった回だけ。
    """
    done = gitcmd.run(top, ["show", f"HEAD:{path}"], timeout)
    if done.ok:
        return done.out, True
    if done.failure:
        return None, False
    listed = gitcmd.run(top, ["ls-tree", "-z", "HEAD", "--", path], timeout)
    if not listed.ok:
        return None, False
    return (None, True) if not listed.out.strip("\0").strip() else (None, False)


def _git(top: str, args: list[str], timeout: float) -> str:
    done = gitcmd.run(top, args, timeout)
    if done.failure:
        return done.failure
    if done.code != 0:
        return " ".join(done.err.split()) or f"git {args[0]} が失敗した"
    return ""
