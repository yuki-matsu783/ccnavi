"""write-and-push で sh が聞くこと。

状態を書く操作を「ロック → 途中の操作の確認 → write-and-push の外の変更の見分けとコミット →
取り込み →
未送信の確かめ → 書く → コミット → push」の 1 操作にするのは sh（`ccnavi-common-write-and-push.sh`
の
`ccnavi_wap_*`）。実行ファイルはネットワークに出ず、コミットもしない（判定と見分けだけを持つ）。
ここが答えるのは 2 つだけで、どちらも 1 行 1 項目（`<キー> <値>`。sh は jq を使わない）。

- `ccnavi write-and-push family <識別子>`: その識別子の親子のチケットと、write-and-push の対象かと、
親のブランチ名
  （`branch`。親のワークツリーの HEAD から読む。変換した識別子がフォルダ名と合うときだけ）
  - 対象は、置き場のパスが相対で、親のワークツリーがあり、origin があり、HEAD の確認に通る
    親子のチケット。マージリクエストで見るフェーズの有無は問わない
  - 止める理由（親のワークツリーが無い・HEAD の確認に通らないなど）のある
    親子のチケットは `target stop`。sh は何も書かずに止める
  - origin の無い親のワークツリーは対象外（ローカルの git の設定だけを読む）
- `ccnavi write-and-push sort <親> [<版>]`: 親のワークツリーの置き場（`.ccnavi/approved/`・
  `wip/proposals/review/`）の変更を次の (b)・(c)・(d) に分ける
  - 版が無ければ未コミットの変更（取り込みの前にコミットする分）、版があれば `<版>..HEAD` で
    コミットに入った変更（取り込みの後に未送信を確かめる分）
  - (b) ccnavi が書いたと内容で分かるもの。hook のフェーズの終わりの告知が置く、その親子のチケットの
    `phases/<親>/<N>.pending`・`.skipped` だけ。`reviewed` は入れない。
    もう 1 つは `ready` の退避（archive.py）が消したもの。ready のマーカー
    （`logs/archive/<リポジトリ>/ready/<親>.json`。このツリーから移したファイルの一覧）に載っている削除で、消えた中身が
    退避したコピーと同じものだけ
  - (c) ユーザがコミットするもの（ユーザの判断）。write-and-push はコミットせずに止める。
    ユーザの判断が一緒に書く
    移動（review/ から done/、doing/ から done/）とマーカーの消去もここ
  - (d) 見分けられないもの。write-and-push は止める
  - `keep` は record-risk が書いた `<子>.judge.json`（その子の `finish` の write-and-push
  がコミットする。
    未コミットのときだけ）
  - `skip` は書きかけの一時ファイル。分類にも数えず、コミットの対象にもしない

git はローカルの読み取りだけ（`status`・`diff`・`ls-tree`・`cat-file`・`config`）。遅延取得は
止める（`gitcmd`）。

`sort` は、分けの理由を言えるときは `why <パス> <理由>` を分けの行の後ろに出す。
"""

from __future__ import annotations

import json
import os
import re
from typing import TextIO

from ...infra import fsio, gitcmd, settings, tree
from ..model import archive, standing, ticket_ids, ticket_model
from . import marks as approval_marks

# 答えの頭の行。sh はこれが無ければ「実行ファイルが write-and-push に答えなかった」と読んで止める。
HEAD = "write-and-push 1"

TARGET_YES = "yes"
TARGET_NO = "no"
TARGET_STOP = "stop"

KIND_B = "b"
KIND_C = "c"
KIND_D = "d"
KIND_KEEP = "keep"
KIND_SKIP = "skip"

# hook の告知が置くマーカーの欄（phase.announce）。これ以外の欄があれば (b) にしない。
_HOOK_MARK_FIELDS = frozenset({"at", "review", "source", "deferred_to", "tickets"})
_HOOK_MARKS = (approval_marks.MARK_PENDING, approval_marks.MARK_SKIPPED)
# 書きかけの一時ファイル（fsio の `.<名前>.<一意>.part.*`、フローの保存の `flows/.*.tmp`、
# configsync の `*.ccnavi-sync`）。
_TEMP = re.compile(r"(^|/)\.[^/]*\.part(\.[^/]*)?$|(^|/)flows/\.[^/]*\.tmp$|\.ccnavi-sync$")
# ユーザの判断が書くもの。フローの本文、reviewed・close-early のマーカー、
# ユーザの承認で置かれた承認済みチケット。
_HUMAN_MARK_NAMES = (
    f"{approval_marks.PARENT_MARK_CLOSE_EARLY}.json",
    f"{approval_marks.LEDGER_NAME}.json",  # 受け入れ台帳（reviewed と一緒に書かれる）
)
_TIMEOUT = 20.0


def family_of(ident: str) -> str:
    """識別子が属する親子のチケットの親。

    子の形（`<親>-<2 桁>-<2 桁>`）なら右から 2 段を剥がした親、そうでなければ自身。
    """
    matched = ticket_ids.child_pattern().match(ident)
    return matched.group("parent") if matched else ident


def _relative_places(conf: settings.Settings) -> tuple[str, str] | None:
    """置き場のパス（承認済み、レビュー待ち）。どちらかが絶対パスなら None。

    絶対パスの置き場はブランチに乗らないので、write-and-push の対象にしない。
    """
    approved = fsio.slashed(conf.approved or settings.DEFAULT_APPROVED).strip("/")
    tickets = fsio.slashed(conf.tickets or settings.DEFAULT_TICKETS).rstrip("/")
    raw = (conf.approved or "", conf.tickets or "")
    if any(os.path.isabs(v) or re.match(r"^[A-Za-z]:", v) for v in raw if v):
        return None
    return approved, f"{tickets.strip('/')}/{ticket_model.REVIEW}"


def target(conf: settings.Settings, root: str, parent: str) -> tuple[str, str, standing.Standing]:
    """write-and-push の対象か（`TARGET_YES` / `TARGET_NO` / `TARGET_STOP`）と、
    その理由と立ち位置。"""
    st = standing.standing_any(conf, root, parent)
    if _relative_places(conf) is None:
        return TARGET_NO, "置き場のパスが絶対パス（write-and-push と Chrome の対象外）", st
    if st.stop or st.home is None:
        return TARGET_STOP, st.stop or "親のワークツリーが決まらない", st
    origin = gitcmd.run(st.home.root, ["config", "--get", "remote.origin.url"], _TIMEOUT)
    if not origin.ok or not origin.out.strip():
        return TARGET_NO, "origin がありません（今の手元の動きのまま）", st
    if any(ch in st.home.root for ch in "\r\n"):
        return TARGET_STOP, "親のワークツリーのパスに改行がある", st
    return TARGET_YES, "", st


def family(stdout: TextIO, conf: settings.Settings, root: str, ident: str) -> int:
    """`ccnavi write-and-push family <識別子>`。親子のチケットと、write-and-push の対象か。"""
    parent = family_of(ident)
    lines = [("family", parent)]
    places = _relative_places(conf)
    verdict, why, st = target(conf, root, parent)
    lines.append(("repo", st.repo))
    # 親のブランチ名。sh は識別子からブランチ名を組み立てず、これを使う
    # （ref・fetch・push・ls-remote）。名前は親のワークツリー
    # （`.claude/worktrees/<識別子>`）の HEAD から読み、変換した識別子がフォルダ名と合うときだけ
    # `branch` の行を出す。読めない・合わないときは `branch_refused` で理由を言う（sh はその親子の
    # チケットを動かさずに止める）。
    fams = standing.Families(conf, root)
    branch = st.branch or fams.branch_any(parent)
    refused = _branch_refusal(parent, branch)
    if refused:
        lines.append(("branch_refused", refused))
        if verdict == TARGET_YES:
            verdict, why = TARGET_STOP, refused
    else:
        lines.append(("branch", branch))
    if places is not None:
        lines += [("approved", places[0]), ("review", places[1])]
    lines.append(("target", verdict))
    if why:
        lines.append(("why", why))
    if verdict == TARGET_STOP:
        for hint in standing.guidance(root, st):
            lines.append(("hint", hint))
    stdout.write(HEAD + "\n")
    for key, value in lines:
        stdout.write(f"{key} {' '.join(str(value).split())}\n")
    # パスは空白をまとめずにそのまま出す（改行を含むパスは target で止めてある）。
    if verdict == TARGET_YES and st.home is not None:
        stdout.write(f"tree {st.home.root}\n")
    return 0


def _branch_refusal(parent: str, branch: str) -> str:
    """HEAD から読んだ親のブランチ名を使えない理由（使えれば空）。"""
    if not branch:
        return (
            f"親のワークツリー（.claude/worktrees/{parent}）の HEAD から親のブランチ名を読めない"
            f"（ブランチ名の `/` を `-` に置き換えたものが {parent} になるブランチに居ること）"
        )
    why = ticket_ids.branch_problem(branch)
    return f"親のブランチ {branch} は使えない（{why}）" if why else ""


def sort(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    parent: str,
    since: str = "",
) -> int:
    """`ccnavi write-and-push sort <親> [<版>]`。置き場の変更を (b)・(c)・(d) に分けて 1 行 1
    つ。"""
    places = _relative_places(conf)
    st = standing.standing_any(conf, root, parent)
    if places is None or st.home is None:
        stderr.write(
            f"ccnavi: write-and-push sort: 親子のチケット {parent} の親のワークツリーが決まらない\n"
        )
        return 1
    tree_root = st.home.root
    changed, why = _changed(tree_root, places, since)
    if why:
        stderr.write(f"ccnavi: write-and-push sort: {why}\n")
        return 1
    stdout.write(HEAD + "\n")
    archive_at = (root, tree.project_of_key(st.repo))
    for kind, rel, why in classify_all(tree_root, places, parent, changed, since, archive_at):
        stdout.write(f"{kind} {rel}\n")
        if why:
            stdout.write(f"why {rel} {' '.join(why.split())}\n")
    return 0


def _changed(tree_root: str, places: tuple[str, str], since: str) -> tuple[list[str], str]:
    """置き場の変更のパス（ツリーからの相対、"/" 区切り）。"""
    specs = [f":(literal){p}" for p in places]
    if since:
        done = gitcmd.run(
            tree_root,
            ["diff", "--name-only", "-z", "--no-renames", since, "HEAD", "--", *specs],
            _TIMEOUT,
            raw_paths=True,
        )
        if not done.ok:
            return [], f"{since}..HEAD の差分を読めない（{(done.err or done.failure).strip()}）"
        return sorted(p for p in done.out.split("\0") if p), ""
    done = gitcmd.run(
        tree_root,
        ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", "--", *specs],
        _TIMEOUT,
        raw_paths=True,
    )
    if not done.ok:
        return [], f"git の状態を読めない（{(done.err or done.failure).strip()}）"
    return sorted(entry[3:] for entry in done.out.split("\0") if len(entry) > 3), ""


def classify_all(
    tree_root: str,
    places: tuple[str, str],
    parent: str,
    changed: list[str],
    since: str = "",
    archive_at: tuple[str, str] | None = None,
) -> list[tuple[str, str, str]]:
    """変更のリストを分ける。答えは `(分け, パス, 理由)`。`since` が無ければ未コミット（今の中身と
    HEAD）、あれば HEAD と `since`。ユーザの判断が一緒に書く移動は、組の両側を見て (c) にする。

    `archive_at` は `(ワークスペースルート, プロジェクト)`。渡せば、ready がこのツリーから退避した
    削除を (b) にする（`archived_removal`）。None なら (b) にしない。
    """
    # 退避（ready）の判定と、ready の内容の読み込み。
    approved_rel, review_rel = places
    ready: set[str] = set()
    if archive_at is not None:
        # マーカーが効くのは、マーカーを書いたときの先頭と比べている版が同じ間だけ
        # （未コミットなら HEAD、未送信の確かめなら `since`）。
        head = archive.tree_head(tree_root, since or "HEAD")
        ready = archive.ready_files(archive_at[0], archive_at[1], tree_root, head)
    # 各ファイルの今の中身と、比べる先の中身を読む。
    states: dict[str, tuple[bytes | None, bytes | None, bool]] = {}
    for rel in changed:
        if since:
            now, readable = gitcmd.blob(tree_root, "HEAD", rel, _TIMEOUT)
            before, readable_before = gitcmd.blob(tree_root, since, rel, _TIMEOUT)
        else:
            now, readable = _read(os.path.join(tree_root, rel.replace("/", os.sep)))
            before, readable_before = gitcmd.blob(tree_root, "HEAD", rel, _TIMEOUT)
        states[rel] = (now, before, readable and readable_before)

    # 追加・削除は、組の両側の中身で決める。
    def added(rel: str) -> bool:
        now, before, ok = states.get(rel, (None, None, False))
        return ok and before is None and now is not None

    def removed(rel: str) -> bool:
        now, before, ok = states.get(rel, (None, None, False))
        return ok and before is not None and now is None

    # 種類ごとに分ける。上から適用して、最初にヒットしたものにする。
    out = []
    for rel in changed:
        now, before, ok = states[rel]
        if _TEMP.search(rel):
            out.append((KIND_SKIP, rel, ""))
            continue
        if not ok:
            out.append((KIND_D, rel, "中身を読めない"))
            continue
        if rel.startswith(review_rel + "/"):
            name = rel[len(review_rel) + 1 :]
            moved = removed(rel) and added(f"{approved_rel}/{ticket_model.DONE}/{name}")
            out.append((KIND_C, rel, "") if moved else (KIND_D, rel, ""))
            continue
        inside = rel[len(approved_rel) + 1 :] if rel.startswith(approved_rel + "/") else ""
        if not inside:
            out.append((KIND_D, rel, ""))
            continue
        parts = inside.split("/")
        if _hook_mark(parts, parent, now, before) or (
            archive_at is not None and archived_removal(inside, now, before, ready, *archive_at)
        ):
            out.append((KIND_B, rel, ""))
        elif not since and _judge_record(parts):
            out.append((KIND_KEEP, rel, ""))
        elif _human(parts, now, before, approved_rel, review_rel, added, removed):
            out.append((KIND_C, rel, ""))
        else:
            out.append((KIND_D, rel, ""))
    return out


def _read(path: str) -> tuple[bytes | None, bool]:
    try:
        if os.path.islink(path):
            return None, False
        with open(path, "rb") as f:
            return f.read(), True
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False


_NUMBER = re.compile(r"[0-9]+")


def _hook_mark(parts: list[str], parent: str, now: bytes | None, before: bytes | None) -> bool:
    """その親子のチケットの `phases/<親>/<N>.(pending|skipped)` で、変更前は無く、
    中身が hook の欄だけ。
    """
    if len(parts) != 3 or parts[0] != approval_marks.PHASES_DIR or parts[1] != parent:
        return False
    if before is not None or now is None:
        return False
    number, _, kind = parts[2].partition(".")
    if not _NUMBER.fullmatch(number) or kind not in _HOOK_MARKS:
        return False
    data = _json(now)
    if not isinstance(data, dict) or not isinstance(data.get("at"), str):
        return False
    return set(data) <= _HOOK_MARK_FIELDS


def archived_removal(
    rel: str,
    now: bytes | None,
    before: bytes | None,
    ready: set[str],
    root: str,
    project: str,
) -> bool:
    """`ready` の退避が消したものか（archive.py）。`rel` は承認済みの領域からの相対。

    ready のマーカー（`archive.ready_files`。そのツリーから ready が移したファイルの一覧）に
    載っていて、
    消えた中身が手元の退避したコピーと同じときだけ。
    ready のマーカーは `logs/archive/` に置かれ、記録の保護がエージェントの書き込みを止める。
    退避の置き場の途中（`logs` を含む）にリンクがあればコピーを読まない。
    """
    if now is not None or before is None or rel not in ready or not archive.in_ready_places(rel):
        return False
    return archive.holds(rel, archive.archived_bytes(root, project, rel), before)


def _judge_record(parts: list[str]) -> bool:
    """`phases/<親>/<子>.judge.json`（record-risk の記録）。"""
    return (
        len(parts) == 3
        and parts[0] == approval_marks.PHASES_DIR
        and parts[2].endswith(f".{approval_marks.CHILD_RECORD_JUDGE}.json")
    )


def _human(parts, now, before, approved_rel, review_rel, added, removed) -> bool:
    """ユーザの判断が書くものの形。形だけで見る（(c) も (d) も write-and-push は止める）。

    フローの本文、ユーザの承認で置かれた承認済みチケット、
    reviewed・(b) でない skipped・close-early・設定を見た上書きの記録、
    ユーザの判断が消したマーカー、ユーザの判断が一緒に書く移動（review/ から done/、
    doing/ から done/）。
    """
    rel = f"{approved_rel}/{'/'.join(parts)}"
    if len(parts) == 2 and parts[0] == "flows" and parts[1].endswith((".yml", ".yaml")):
        return True
    if len(parts) == 2 and parts[0] == ticket_model.DOING:
        if added(rel):
            return True  # ユーザの承認で置かれた承認済みチケット
        # 親を早めに閉じたとき（close-early）の取り消しで done/ へ動いた
        return removed(rel) and added(f"{approved_rel}/{ticket_model.DONE}/{parts[1]}")
    if len(parts) == 2 and parts[0] == ticket_model.DONE and added(rel):
        # ユーザのレビュー（review/ から）か早めに閉じたときの取り消し（doing/ から）で動いた先
        return removed(f"{review_rel}/{parts[1]}") or removed(
            f"{approved_rel}/{ticket_model.DOING}/{parts[1]}"
        )
    if len(parts) == 3 and parts[0] == approval_marks.PHASES_DIR:
        name = parts[2]
        if name in _HUMAN_MARK_NAMES:
            return True
        number, _, kind = name.partition(".")
        if _NUMBER.fullmatch(number) and kind in approval_marks.MARKS and removed(rel):
            return True  # ユーザの承認が消したマーカー（子が足された）
        if _NUMBER.fullmatch(number) and kind in (
            approval_marks.MARK_REVIEWED,
            approval_marks.MARK_SKIPPED,
        ):
            return True
    return False


def _json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
