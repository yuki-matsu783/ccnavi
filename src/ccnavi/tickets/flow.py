"""子チケットのフロー（作業の手順のグラフ）。着手中は書き換えを止める。

書かれた文字列の整え方は `flow_text`、形の検査は `flow_shape`、文への描き方は `flow_render` に
分けてある。どれも flow を読まない。

子チケット 1 本につき 1 本、担当のサブエージェントが作業中に読む手順書を置ける。
置き場は承認済みの領域の `<承認済みチケットの置き場>/flows/<子>.yml`
（既定 `.ccnavi/approved/flows/<子>.yml`）に固定で、チケットの欄では指さない。
置き場を持つツリーはチケットと同じ（承認済みチケットが在るツリー。プロジェクトの
チケットならそのプロジェクトのツリー）で、チケットと同じ git に乗る。

承認済みの領域は組み込みの保護（`builtin-guard-project-home`）がエージェントの
Write / Edit / NotebookEdit を止め、パスの出るシェルからの書き込みも組み込みが止める。
だから「フローはユーザが書く」は運用ではなく判定で守られる（行き先を追えないシェルの書き込みは
止まらないので、着手のあとの書き換えは知らせる。下の「着手のあとの書き換え」）。ユーザはボードのフロー編集画面で
書き、承認済みチケットと同じく承認の push（`ccnavi-push-approved.sh`）でコミットする。
実行後チェックは承認済みの領域を範囲の外として報告せず、frontmatter の無いファイルは
副命令の書き込みとして外す（`post_findings._script_writes`）。だからユーザが保存したフローが
エージェントの範囲外の変更として報告されることもない。

## 下書き

エージェントは、頼まれたときに子のフローの下書きを提案の置き場の `flows/<子>.yml`
（既定 `wip/proposals/flows/<子>.yml`）に書ける。置くツリーはフローと同じ。提案の置き場は
範囲の外なので、判定も組み込みの保護も変えずに書ける。下書きには効力が無い。`SubagentStart` の案内
（`briefing`）も着手のハッシュ（`fingerprint`）も読まず、承認のダイジェストにも入らない。ユーザがボードの
フロー編集画面で差分を読んで取り込み、`flows/<子>.yml`（承認済みの領域）に保存したものだけが効く。
ここが持つのは置き場のパス（`draft_rel`）と、ボードへ渡す有無（`info` の `draft`）だけ。

## 形

YAML の 1 文書で、最上位はマッピング。ボードのフロー編集画面が書き、ここが読む。

    id, name, description?, version
    nodes:          [node, ...]
    connections:    [connection, ...]
    subAgentFlows?: [{id, name, nodes, connections}, ...]
    node       = {id, type, name, position: {x, y}, data: {...}}
    connection = {id, from, to, fromPort, toPort, condition?}

読むのは `yaml.safe_load`（ルールや設定と同じ読み手）に、別名（`*名前`）を拒む処理を足したもの
（`_Loader`）。別名は同じ部分木を何度でも指せるので、入れ子にすると小さなファイルが辿る量で
膨らむ（billion laughs）。手順書に別名は要らないので、量で切らずに別名ごと読まない。

ノードの種類（`type`）のうち、ここが中身を読むのは `start` `end` `prompt` `subAgent`
`askUserQuestion` `ifElse` `switch` `loop` `branch` `skill` `mcp` `subAgentFlow` `codex`
`branchSession`。知らない種類は省かず、種類の名前と `name` だけで並べる。
`group` は図の上の囲み（ボードの枠）で手順ではないので並べない（中のノードは `parentId` が
あっても、ほかのノードと同じに並べる）。

## 破損したフローで止まらない

フローはユーザが書くデータで、形は保証されない。読む・並べるのどこでも例外を外に出さない。
読めなければ 1 行の知らせにして、`SubagentStart` の残り（子の一覧と範囲）はそのまま渡す。
大きさにも上限を置く。ファイルは `FILE_LIMIT` まで、1 ノードの項目は `ITEM_LIMIT` まで、
1 本の子の文は `CHILD_TEXT_LIMIT` まで。シンボリックリンク（ファイルそのものか、ツリーのルートから
そこまでの途中）は読まない。承認済みの領域の外を指していれば、エージェントが書ける中身を
ユーザの手順書として渡すことになる。ふつうのファイルでないもの（名前付きパイプは開くと応答しなくなる）と
ハードリンク（外の名前から書き換えられる）も読まない（`read_bytes`）。

形の誤り（最上位がマッピングでない、`nodes` が無い、ノードに `id` が無い・重なる、
`connections` がリストでない）も読めない理由として 1 行で言う（`flow_shape.shape_problem`）。
`ccnavi --lint --flow <パス>` は同じ読み手・同じ検査（`load`）でファイルを確かめ、読めなければ
error で言う。ボードのフロー編集画面は、開くときと保存の前に編集中の本文を一時ファイルに書いて
これに掛ける（拡張は判定を自分で出さない。正しいかの答えはここ 1 か所）。`--json` なら
読めた中身も載せ（`as_json`）、画面は自分の読み（YAML 1.2）と見比べて、値の意味が食い違えば
開かない・保存しない。
読めたフローの線の構造（`flow_shape.structure_problems`）と名前の表記
（`flow_shape.name_problems`）は warn で足し、読むのは止めない。

読むのは正とするツリー（承認済みチケットが在るツリー）の版だけ。子のワークツリー上の版は読まない。

## 着手のあとの書き換え

ロックと承認済みの領域の保護が止めるのは Write / Edit と、パスの出るシェルの書き込みまで。
行き先を追えないシェルの書き込みは止まらない。着手のときにフローのハッシュを
`phases/<親>/<子>.flow.json` に記録し（`record_digest`）、SubagentStart と SubagentStop が
いまのハッシュと比べて、違えばユーザとメインに知らせる（`changed_notice`。止めない）。

## 承認とロック

フローは承認の対象ではない（承認のダイジェストにも入らない）。中身は着手の前と終わった後なら
書き換えられる。着手中（`started_at` があり、`completed_at` も `cancelled_at` も無い）は、
読んでいる手順が作業の途中で変わらないよう、実行前チェックが Write / Edit / NotebookEdit を
止める（`lock_hit`）。エージェントの書き込みは承認済みの領域の保護でも止まるが、ロックは
その保護を切った設定でも当てはまり、止めた理由を名指しする。ボードも着手中は保存しない。
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import math
import os
import posixpath
import stat

import yaml

from ..infra import fsio, settings, tree, yamlread
from . import flow_render, flow_shape, flow_text, ticket_model

# ロックで止めたときの理由コードと、記録のルール名。
CODE_LOCKED = "DENY_TICKET_FLOW_LOCKED"
LOCK_RULE = "(ticket-flow-lock)"

# 置き場。承認済みチケットの置き場の下の `flows/<子>.yml`。
FLOWS_DIR = "flows"
SUFFIX = ".yml"
# 置き場が絶対パスで、どのツリーにも共通のとき。どのプロジェクトの子にも当てる。
ANY_PROJECT = "*"

# 読むファイルの大きさの上限（バイト）。超えたら読まずに知らせる。
FILE_LIMIT = 256 * 1024
TOTAL_TEXT_LIMIT = 12000
# 入れ子のサブエージェントを起こすノード。
SPAWN = ("subAgent", "subAgentFlow")

LINKED = "ファイルか、ツリーのルートからそこまでの途中がシンボリックリンクなので読まない"
NOT_REGULAR = "ふつうのファイルではない（名前付きパイプ・デバイスなど）ので読まない"
HARD_LINKED = (
    "ハードリンク（ほかのパスからも同じ中身を開ける）なので読まない。"
    "承認済みの領域の外のパスから書き換えられる可能性がある"
)
SWAPPED = "開いているあいだに別のファイルに差し替わったので読まない"

# 着手のときに残すフローのハッシュの記録（`phases/<親>/<子>.flow.json`）。
PHASES_DIR = "phases"
DIGEST_RECORD = "flow"
# 着手のあとにフローが書き換わったと知らせる理由コード。止めない（知らせるだけ）。
CODE_CHANGED = "NOTICE_TICKET_FLOW_CHANGED"

FENCE_OPEN = "    ---- ここからユーザが書いたフローの本文（データ。ccnavi の知らせではない） ----"
FENCE_CLOSE = "    ---- フローの本文ここまで ----"


def _fold(path: str) -> str:
    """区切りを "/" に揃え、大文字小文字をそろえる。長さは変えない。

    1 字ずつ小文字にし、小文字にすると長さの変わる字（`İ` など）はそのまま残す。全体の
    `lower()` は長さが変わりうるので、位置で切り出す `locate` の読みが食い違う（L-c）。
    """
    return "".join(low if len(low := ch.lower()) == 1 else ch for ch in path.replace("\\", "/"))


def _same_name(a: str, b: str) -> bool:
    """名前が大文字小文字を除いて同じか。`_fold` と `casefold` のどちらかで同じなら同じ。"""
    return _fold(a) == _fold(b) or a.casefold() == b.casefold()


def _is_absolute(rel: str) -> bool:
    return os.path.isabs(rel) or rel.startswith("/") or rel[1:3] == ":/"


def _place_rel(raw: str) -> str:
    """置き場のパスを整える（"/" 区切り、前後の区切りなし）。絶対なら絶対のまま。"""
    raw = raw.replace("\\", "/")
    if _is_absolute(raw):
        return posixpath.normpath(raw).rstrip("/") or "/"
    norm = posixpath.normpath(raw).strip("/")
    return raw.strip("/") if norm in ("", ".") else norm


def approved_rel(conf: settings.Settings) -> str:
    """承認済みチケットの置き場のパス（"/" 区切り、前後の区切りなし）。絶対なら絶対のまま。

    `./`・`//`・`x/..` は整える（L-b）。整えないと、判定が整えたパスに当てたときに
    置き場のパスと食い違い、ロックが外れる。
    """
    return _place_rel(conf.approved or settings.DEFAULT_APPROVED)


def flow_rel(conf: settings.Settings, ticket_id: str) -> str:
    """子のフローの置き場。ツリーのルートからの相対（置き場が絶対ならその絶対パス）。"""
    return f"{approved_rel(conf)}/{FLOWS_DIR}/{ticket_id}{SUFFIX}"


def flow_file(conf: settings.Settings, tree_root: str, ticket_id: str) -> str:
    """このツリーでの子のフローの絶対パス（`./` や `x/..` は整えたパス）。"""
    return os.path.normpath(
        os.path.join(settings.approved_dir(conf, tree_root), FLOWS_DIR, f"{ticket_id}{SUFFIX}")
    )


def draft_rel(conf: settings.Settings, ticket_id: str) -> str:
    """子のフローの下書きの置き場。提案の置き場の `flows/<子>.yml`。

    承認済みの領域で `flows/` が `doing/` `done/` と並ぶのに揃え、提案の置き場でも
    `todo/` `review/` と並べる。提案の置き場は丸ごとチケットの範囲の外で、`flows/` は守る状態の
    置き場でも走査の対象でもないので、エージェントは判定を変えずに書ける。下書きに効力は無い
    （`briefing` も着手のハッシュも読まない）。有効になるのはユーザが取り込んで
    `flow_rel` に保存したものだけ。
    """
    return f"{_place_rel(conf.tickets or settings.DEFAULT_TICKETS)}/{FLOWS_DIR}/{ticket_id}{SUFFIX}"


def draft_file(conf: settings.Settings, tree_root: str, ticket_id: str) -> str:
    """このツリーでの子のフローの下書きの絶対パス。"""
    return os.path.normpath(os.path.join(tree_root, *draft_rel(conf, ticket_id).split("/")))


def linked(tree_root: str, path: str) -> bool:
    """tree_root から path までの途中（path 自身を含む）に、シンボリックリンクがあるか。

    `configsync._linked` と同じ読み方。パスのままさかのぼり、実体が tree_root に着いたところで
    止める。tree_root より上のリンク（macOS の `/tmp` など）は数えない。着けなければ
    （外を指している）リンクと同じに扱う。
    """
    try:
        base = os.path.normcase(os.path.realpath(tree_root))
        current = os.path.abspath(path)
        while os.path.normcase(os.path.realpath(current)) != base or os.path.islink(current):
            if os.path.islink(current):
                return True
            parent = os.path.dirname(current)
            if parent == current:
                return True
            current = parent
    except (OSError, ValueError):
        return True
    return False


def resolve(
    conf: settings.Settings, root: str, child: ticket_model.Ticket
) -> tuple[str, str, bool]:
    """フローのファイルの絶対パスと、それを持つツリーのルートと、在るかどうか。

    読むのは正とするツリー（承認済みチケットが在るツリー）の版だけ。子のワークツリーの版は
    読まない。子のワークツリーはエージェントが作業する場所で、そこにある版はシェルの書き込み
    （行き先を追えない形）で書き換えられうる（M-2）。
    リンクでも「在る」とする（読むかどうかは `load` が決める。気づかないうちに別の版へ移ることは
    しない）。
    """
    base = child.tree_root or root
    path = flow_file(conf, base, child.ticket)
    return path, base, os.path.lexists(path)


def info(conf: settings.Settings, root: str, child: ticket_model.Ticket) -> dict | None:
    """ボード（`--explain --json`）に出すフローの欄。子でなければ None。

    閉じた子（終わった・取り消した）でフローが無ければ None。閉じた子にフローを作っても
    読まれる場面が無い。ボードは欄が無ければ「フローを作る」を出さない。フローが在る
    閉じた子は欄を返す（ユーザが見返せる）。

    `tree` はファイルを持つツリーのルート。ボードはそこからファイルまでの途中にリンクが
    あれば書かない。`linked` はその途中にリンクがあるか（在るときだけ見る）。

    `draft` はエージェントが書く下書きの `{path, rel, exists, linked}`。置くツリーは
    フローと同じ（`tree`）。ボードはパスを組まずにここを読む。下書きの中身はここでも読まない。
    """
    if not child.is_child:
        return None
    path, base, exists = resolve(conf, root, child)
    if not exists and child.state in (ticket_model.DONE, ticket_model.CANCELLED):
        return None
    draft = draft_file(conf, base, child.ticket)
    draft_exists = os.path.lexists(draft)
    return {
        "path": path,
        "rel": flow_rel(conf, child.ticket),
        "tree": base,
        "exists": exists,
        "linked": exists and linked(base, path),
        "locked": child.in_progress and child.state == ticket_model.DOING,
        "draft": {
            "path": draft,
            "rel": draft_rel(conf, child.ticket),
            "exists": draft_exists,
            "linked": draft_exists and linked(base, draft),
        },
    }


# ---- ロック


def locate(conf: settings.Settings, root: str, path: str) -> tuple[str, str | None] | None:
    """このパスが子のフローの置き場なら（ファイルの名前, そのツリーのプロジェクト）。違えば None。

    名前は `flows/` の下の残り（`<子>.yml`）。プロジェクトは置き場を持つツリーのもの
    （ワークスペースなら空、ワークスペースの外なら None）。パスは解いたものでも解く前の
    ものでもよい。大文字小文字は範囲の照合と同じく区別しない。

    名前は Windows で同じファイルを指す表記をまとめる。末尾の `.` と空白、`:` から後ろ
    （`::$DATA` などの代替データストリーム）を除く（止める向きだけ）。8.3 形式の短い
    名前（子の名前が 8 字を超えるときの `I0001-~1.YML` など）はまとめられない。解いたパス
    （`full`）が長い名前に戻すのに任せる。
    """
    if not path:
        return None
    norm = os.path.normpath(path)
    here = _fold(norm)
    rel = approved_rel(conf)
    absolute = _is_absolute(rel)
    shared = absolute
    if not absolute and (rel == ".." or rel.startswith("../")):
        # ツリーの外（`../shared/approved`）を指す置き場。ツリーをまたいで 1 か所になりうるので、
        # `..` を除いた残りのパスで当て、どのプロジェクトの子でも止める（止める向き）。
        rel = "/".join(p for p in rel.split("/") if p != "..")
        shared = True
    marker = _fold(rel if absolute else f"/{rel}" if rel else "") + f"/{FLOWS_DIR}/"
    at = here.rfind(marker)
    if at < 0:
        return None
    name = here[at + len(marker) :].split(":", 1)[0].rstrip(". ")
    if not name:
        return None
    if shared:
        # 置き場がどのツリーにも共通の 1 か所。どのプロジェクトの子でも当てる（止める向き）。
        return name, ANY_PROJECT
    owner = tree.tree_of(root, norm[:at] or os.sep, conf.projects)
    return name, (owner.project if owner is not None else None)


def lock_hit(
    copies: list[ticket_model.Ticket], project: str | None, name: str
) -> ticket_model.Ticket | None:
    """この書き込みを止める、着手中の子。無ければ None。

    `copies` はどのツリー上のチケットも並べたもの（識別子で 1 本にまとめる前）。どれか 1 本でも
    着手中なら止める（止める向きだけ）。`project` は置き場を持つツリーのプロジェクト
    （ワークスペースなら空）。
    ワークスペースルート・プロジェクト・どのワークツリーでも、同じ子の置き場なら止める。
    """
    if project is None:
        return None
    for child in copies:
        if (
            child.is_child
            and child.in_progress
            and project in (child.project, ANY_PROJECT)
            and _same_name(f"{child.ticket}{SUFFIX}", name)
        ):
            return child
    return None


def hard_linked(path: str) -> bool:
    """在るふつうのファイルで、名前が 2 つ以上あるか。無い・読めないなら偽。"""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return False
    return stat.S_ISREG(st.st_mode) and st.st_nlink > 1


def inode_hit(
    conf: settings.Settings, root: str, copies: list[ticket_model.Ticket], path: str
) -> ticket_model.Ticket | None:
    """書き込み先が、着手中の子のフローとハードリンクで同じ中身なら、その子。無ければ None。

    ハードリンクはパスに置き場が出ないので `locate` では当たらない（M-1）。書き込み先が
    在って、名前が 2 つ以上あるときだけ、着手中の子のフロー（どのツリー上のチケットの置き場も）と
    (デバイス, inode) を比べる。止める向きだけ。ふつうのファイル（名前が 1 つ）には何も読まない。
    """
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    if st.st_nlink < 2 or not stat.S_ISREG(st.st_mode) or not st.st_ino:
        return None
    for child in copies:
        if not (child.is_child and child.in_progress):
            continue
        for base in dict.fromkeys(b for b in (child.tree_root, root) if b):
            try:
                other = os.stat(flow_file(conf, base, child.ticket))
            except (OSError, ValueError):
                continue
            if (other.st_dev, other.st_ino) == (st.st_dev, st.st_ino):
                return child
    return None


def locked_message(conf: settings.Settings, child: ticket_model.Ticket, path: str) -> str:
    """ロックで止めたときの文面。"""
    ticket = flow_text.clean(child.ticket)
    return "\n".join(
        [
            f"[ccnavi] {CODE_LOCKED} (source: {flow_text.clean(child.path)})",
            f"ticket: {ticket} ({flow_text.clean(child.title)}), "
            f"started {flow_text.clean(child.started_at)}",
            f"path: {flow_text.clean(path)}",
            f"子チケット {ticket} は着手中なので、そのフロー"
            f"（`{flow_rel(conf, child.ticket)}`）は書き換えられません。"
            "担当のサブエージェントが読んでいる手順が作業の途中で変わるのを防ぐためです。"
            "フローはユーザがボードのフロー編集画面で書くもので、エージェントは書きません。",
            f"ロックは {ticket} が `ccnavi-ticket.sh finish` で終わるか "
            "`cancel` で取り消されると外れます。手順を直したいなら、終わってから"
            "ユーザに書き換えてもらうか、次の子チケットのフローに書いてもらってください。",
        ]
    )


# ---- 読む


def read_bytes(path: str, tree_root: str = "") -> tuple[bytes | None, str]:
    """フローのファイルの中身（バイト）。読まないなら (None, 理由)。例外は外に出さない。

    読むのはふつうのファイル（`S_ISREG`）で、名前が 1 つ（ハードリンクでない）ものだけ。
    名前付きパイプを開くと書き手が来るまで戻らず、SubagentStart が応答しなくなる（H-1）。
    ハードリンクは承認済みの領域の外の名前から書き換えられる（M-1）。
    `lstat` で確かめてから `O_NONBLOCK | O_NOFOLLOW` で開き、開いたものを `fstat` で
    もう一度確かめる（ふつうのファイルで、`lstat` と同じ inode）。確かめてから開くまでに
    差し替えられても、開いたものが違えば読まない。Windows には `O_NONBLOCK` も
    `O_NOFOLLOW` も無いので、確かめ直しだけが役に立つ。
    """
    try:
        if tree_root and linked(tree_root, path):
            return None, LINKED
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode):
            return None, LINKED
        if not stat.S_ISREG(before.st_mode):
            return None, NOT_REGULAR
        if before.st_nlink > 1:
            return None, HARD_LINKED
        if before.st_size > FILE_LIMIT:
            return None, f"大きすぎるので読まない（{before.st_size} バイト、上限 {FILE_LIMIT}）"
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0)
        )
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            after = os.fstat(f.fileno())
            if not stat.S_ISREG(after.st_mode):
                return None, NOT_REGULAR
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                return None, SWAPPED
            if after.st_nlink > 1:
                return None, HARD_LINKED
            raw = f.read(FILE_LIMIT + 1)
        if len(raw) > FILE_LIMIT:
            return None, f"大きすぎるので読まない（上限 {FILE_LIMIT} バイト）"
        return raw, ""
    except OSError as exc:
        return None, f"読めない ({flow_text.clean(exc.strerror or type(exc).__name__)})"
    except Exception as exc:  # noqa: BLE001  破損したデータで SubagentStart を止めない
        return None, f"読めない ({type(exc).__name__})"


class _AliasRefused(yaml.YAMLError):
    """フローに別名（`*名前`）があった。"""


class _Loader(yaml.SafeLoader):
    """`yaml.safe_load` の読み手に、別名を拒む処理を足したもの。

    別名は同じ部分木を何度でも指せる。入れ子にすると、小さなファイルでも辿る量が指数で
    膨らむ（billion laughs）。自分を指す別名は循環する値になる。手順書に別名は要らないので、
    出てきた時点で読むのをやめる（量で切ると、上限の手前まで辿る手間は残る）。
    """

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise _AliasRefused("別名")
        return super().compose_node(parent, index)


ALIASED = (
    "YAML の別名（`*名前`）があるので読まない。"
    "別名を使うと、同じ部分木を何度も辿らせて中身を膨らませられる"
)


def _yaml_problem(exc: yaml.YAMLError) -> str:
    """YAML の読めない理由を 1 行に。問題と、あれば位置（行・桁は 1 始まり）。"""
    problem = getattr(exc, "problem", None)
    mark = getattr(exc, "problem_mark", None)
    if not problem:
        return str(exc)
    if mark is not None:
        return f"{problem}、{mark.line + 1} 行 {mark.column + 1} 桁"
    return str(problem)


def load(path: str, tree_root: str = "") -> tuple[dict | None, str]:
    """フローを読む。(中身, 読めない理由)。例外は外に出さない。

    tree_root を渡せば、そこからファイルまでの途中にリンクがあるものは読まない。ファイルそのものが
    リンクなら、tree_root が無くても読まない。ふつうのファイルでないもの（名前付きパイプなど）と
    ハードリンクも読まない（`read_bytes`）。
    """
    raw, why = read_bytes(path, tree_root)
    if raw is None:
        return None, why
    return parse(raw)


def parse(raw: bytes) -> tuple[dict | None, str]:
    """フローの中身（バイト）を読む。(中身, 読めない理由)。例外は外に出さない。

    文字は UTF-8（先頭の BOM は 1 つ外す。`utf-8-sig`）。UTF-8 として不正であれば読まない
    （置き換え文字で埋めて読むと、不正な部分を省いた手順が渡る）。
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, f"{NOT_UTF8}（{exc.start + 1} バイト目）"
    try:
        # _Loader は SafeLoader に別名を拒む処理を足したもの（任意の型は作らない）。
        data = yaml.load(text, Loader=_Loader)
    except _AliasRefused:
        return None, ALIASED
    except OSError as exc:
        return None, f"読めない ({flow_text.clean(exc.strerror or type(exc).__name__)})"
    except RecursionError:
        return None, "入れ子が深すぎて YAML として読めない"
    except yaml.YAMLError as exc:
        return None, f"YAML として読めない ({flow_text._line(_yaml_problem(exc))})"
    except (ValueError, TypeError) as exc:
        return None, f"YAML として読めない ({flow_text._line(exc)})"
    except Exception as exc:  # noqa: BLE001  破損したデータで SubagentStart を止めない
        return None, f"読めない ({type(exc).__name__})"
    why = flow_shape.shape_problem(data)
    if why:
        return None, why
    return data, ""


NOT_UTF8 = "UTF-8 として読めない"

# `as_json` の目印の鍵。JSON にそのまま載らない値（下）をこの鍵を持つオブジェクトで表す。
JSON_MARK = "$ccnavi"
# JSON の数として拡張（JavaScript）が崩さずに読める整数の範囲
_SAFE_INT = 2**53 - 1


def as_json(value):
    """読めた中身を JSON に載せる形にする（`--lint --json --flow` の `flow.data`）。

    VS Code 拡張のフロー編集画面は、自分の YAML の読み手（1.2）が読んだ中身とこれを見比べ、
    食い違えば開かない・保存しない（読みの答えは実行ファイルが持つ）。
    文字列・真偽値・null・配列・文字列をキーとする辞書と、`±(2**53 - 1)` までの整数は
    そのまま載せる。ほかは `{"$ccnavi": <種類>, ...}` の目印にする。

    - 浮動小数は `{"$ccnavi": "float", "value": <数>}`
      （JSON では 1 と 1.0 の区別が消えるので包む）。
      有限でなければ `"text"` に `inf` / `-inf` / `nan`
    - 範囲の外の整数は `{"$ccnavi": "int", "text": <十進>}`
    - キーが文字列でない辞書と、鍵 `$ccnavi` を持つ辞書は
      `{"$ccnavi": "map", "items": [[キー, 値], ...]}`
    - 日付・日時・バイト列（`!!binary`）・集合（`!!set`）・組（`!!omap` / `!!pairs` の 1 件）・
      そのほかは
      `{"$ccnavi": "date" | "datetime" | "bytes" | "set" | "tuple" | "other", "text": <文字列>}`
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if -_SAFE_INT <= value <= _SAFE_INT:
            return value
        return {JSON_MARK: "int", "text": _digits(value)}
    if isinstance(value, float):
        if math.isfinite(value):
            return {JSON_MARK: "float", "value": value}
        text = "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
        return {JSON_MARK: "float", "text": text}
    if isinstance(value, list):
        return [as_json(item) for item in value]
    if isinstance(value, dict):
        if JSON_MARK not in value and all(isinstance(key, str) for key in value):
            return {key: as_json(item) for key, item in value.items()}
        return {JSON_MARK: "map", "items": [[as_json(k), as_json(v)] for k, v in value.items()]}
    if isinstance(value, datetime.datetime):
        return {JSON_MARK: "datetime", "text": value.isoformat()}
    if isinstance(value, datetime.date):
        return {JSON_MARK: "date", "text": value.isoformat()}
    if isinstance(value, bytes):
        return {JSON_MARK: "bytes", "text": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (set, frozenset)):
        return {JSON_MARK: "set", "text": flow_text._line(repr(sorted(map(repr, value))))}
    if isinstance(value, tuple):
        return {JSON_MARK: "tuple", "text": flow_text._line(repr(value))}
    return {JSON_MARK: "other", "text": type(value).__name__}


def _digits(value: int) -> str:
    try:
        return str(value)
    except ValueError:  # 桁の上限（sys.set_int_max_str_digits）を越える
        return "(桁が多すぎる)"


# ---- 選べるエージェントとスキルの名前（`--lint --json --flow` の `flow.candidates`）

# Claude Code の組み込みのサブエージェント。
BUILTIN_AGENTS = ("general-purpose", "Explore", "Plan")
# プロジェクトのサブエージェントとスキルの置き場（ワークスペースルートからの相対）。
AGENTS_DIR = os.path.join(".claude", "agents")
SKILLS_DIR = os.path.join(".claude", "skills")
SKILL_FILE = "SKILL.md"
# 読むファイルの数と、1 本から読む頭の大きさ（バイト）の上限。
CATALOG_LIMIT = 200
HEAD_LIMIT = 8 * 1024
SOURCE_BUILTIN = "builtin"
SOURCE_PROJECT = "project"


def _regular_file(path: str) -> bool:
    """リンクでない、ふつうのファイルか（辿らずに見る）。"""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


def _front_name(path: str) -> str:
    """Markdown の frontmatter の `name:` の値。無い・読めないなら空。リンクは辿らない。

    frontmatter（頭の `---` から次の `---` まで）だけを `yaml.safe_load` で読む（`>-` の折り返しや
    行末の注釈で名前が化けないように）。別名は拒む（`_Loader`）。`name` が文字列でなければ空。
    """
    if not _regular_file(path):
        return ""
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_LIMIT).decode("utf-8-sig", errors="replace")
    except (OSError, ValueError):
        return ""
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        body.append(line)
    else:
        return ""
    try:
        # _Loader は SafeLoader。組み立ての途中の素の例外も YAMLError（LoadError）で上がる。
        meta = yamlread.load("\n".join(body), _Loader)
    except yaml.YAMLError:
        return ""
    value = meta.get("name") if isinstance(meta, dict) else None
    return flow_text.clean(value).strip() if isinstance(value, str) else ""


def _entries(path: str, root: str) -> list[os.DirEntry]:
    """ディレクトリの中身（名前の順、`CATALOG_LIMIT` 件まで）。

    `root` からそのディレクトリまでの途中（`.claude` 自体を含む）にリンクがあれば読まない
    （`linked` と同じ考え方）。
    """
    try:
        if linked(root, path) or not os.path.isdir(path):
            return []
        with os.scandir(path) as it:
            found = sorted(it, key=lambda e: e.name)
    except (OSError, ValueError):
        return []
    return found[:CATALOG_LIMIT]


def catalog(root: str) -> dict[str, list[dict]]:
    """フローで選べるサブエージェントとスキルの名前。例外は外に出さない。

    `{"agents": [{name, source}], "skills": [{name, source}]}`。`source` は `builtin`（組み込み）
    か `project`（ワークスペースの `.claude/agents/*.md` と `.claude/skills/*/SKILL.md`）。
    名前は frontmatter の `name`、無ければファイル（スキルはディレクトリ）の名前。
    見るのはディレクトリの中だけで、ユーザのホーム（`~/.claude`）とプラグインのものは入らない。
    リンクは辿らない。
    """
    agents = [{"name": name, "source": SOURCE_BUILTIN} for name in BUILTIN_AGENTS]
    skills: list[dict] = []
    if root:
        for entry in _entries(os.path.join(root, AGENTS_DIR), root):
            # ふつうのファイルだけ（ディレクトリ・リンクは数えない）。
            # `.md` は大文字小文字を区別しない
            if not entry.name.lower().endswith(".md") or not _regular_file(entry.path):
                continue
            name = _front_name(entry.path) or entry.name[: -len(".md")]
            agents.append({"name": name, "source": SOURCE_PROJECT})
        for entry in _entries(os.path.join(root, SKILLS_DIR), root):
            try:
                if entry.is_symlink() or not entry.is_dir():
                    continue
            except OSError:
                continue
            path = os.path.join(entry.path, SKILL_FILE)
            if not _regular_file(path):
                continue
            skills.append({"name": _front_name(path) or entry.name, "source": SOURCE_PROJECT})
    return {"agents": _unique(agents), "skills": _unique(skills)}


def _unique(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    kept = []
    for item in items:
        if item["name"] and item["name"] not in seen:
            seen.add(item["name"])
            kept.append(item)
    return kept


# ---- 着手のあとの書き換えを知らせる


def fingerprint(path: str, tree_root: str = "") -> str:
    """フローのファイルのダイジェスト。無ければ `absent`、読めれば `sha256:<16 進>`、読まないなら
    `unreadable:<理由>`。例外は外に出さない。読み方は `load` と同じ（`read_bytes`）。"""
    try:
        if not os.path.lexists(path):
            return "absent"
    except (OSError, ValueError):
        return "unreadable:読めない"
    raw, why = read_bytes(path, tree_root)
    if raw is None:
        return f"unreadable:{why}"
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def digest_record_path(conf: settings.Settings, root: str, child: ticket_model.Ticket) -> str:
    """着手のときに保存したハッシュの記録の置き場。

    フローと同じツリーの `phases/<親>/<子>.flow.json`。
    """
    # 形は `approval_marks.child_record_path` と同じ。flow は approval_marks より下の段なので
    # 読まず、形だけを同じにする（`test_flow_hardening` が突き合わせる）。
    approved = settings.approved_dir(conf, child.tree_root or root)
    return os.path.join(approved, PHASES_DIR, child.parent, f"{child.ticket}.{DIGEST_RECORD}.json")


def record_digest(
    conf: settings.Settings, root: str, child: ticket_model.Ticket
) -> tuple[str, str]:
    """着手のときのフローのハッシュを記録する。(書いた記録のパス, 書けなかった理由)。

    置き場は子の記録（`.risk.json` など）と同じ `phases/<親>/` で、承認済みの領域にあるので
    エージェントは書けず、親のブランチに乗って他の機械へ届く。フローが無くても記録する
    （着手のあとに現れたことも知らせるため）。
    """
    path, base, _ = resolve(conf, root, child)
    data = {
        "fingerprint": fingerprint(path, base),
        "path": flow_rel(conf, child.ticket),
        "at": child.started_at,
    }
    target = digest_record_path(conf, root, child)
    failed = fsio.write_text(target, json.dumps(data, ensure_ascii=False, indent=1) + "\n", "\n")
    return target, flow_text.clean(failed)


def _describe(mark: str) -> str:
    if mark == "absent":
        return "無い"
    if mark.startswith("sha256:"):
        return mark[: len("sha256:") + 12]
    return flow_text.clean(mark.partition(":")[2]) or "読めない"


def changed_notice(conf: settings.Settings, root: str, child: ticket_model.Ticket) -> str:
    """着手中の子のフローが、着手のときに記録したハッシュから変わっていれば、その知らせ。無ければ空。

    知らせるだけで止めない（厳しくする向き）。ロックと承認済みの領域の保護は Write / Edit と、
    パスの出るシェルの書き込みを止めるが、行き先を追えないシェルの書き込みは止まらない
    （M-2）。記録が無い（この仕組みより前に着手した）子には何も言わない。例外は外に出さない。
    """
    try:
        if not (child.is_child and child.in_progress):
            return ""
        record = fsio.read_dict(digest_record_path(conf, root, child))
        if not record:
            return ""
        before = str(record.get("fingerprint") or "")
        path, base, _ = resolve(conf, root, child)
        now = fingerprint(path, base)
        if not before or before == now:
            return ""
        return (
            f"[ccnavi] {CODE_CHANGED}: 着手後にフローが書き換わった。子チケット "
            f"{flow_text.clean(child.ticket)} のフロー {flow_text.clean(path)} が、"
            f"着手のとき（{_describe(before)}）と"
            f"違う（いま {_describe(now)}）。担当のサブエージェントが読んだ手順と、ユーザが渡した"
            "手順が食い違っているかもしれない。誰が書き換えたかをユーザが確かめてください"
            "（ロックは Write / Edit を止めるが、シェルから行き先を追えない形で書くと止まらない）"
        )
    except Exception:  # noqa: BLE001  知らせのために hook を止めない
        return ""


def briefing(
    conf: settings.Settings,
    root: str,
    child: ticket_model.Ticket,
    scope: str,
    budget: int = TOTAL_TEXT_LIMIT,
    full: bool = True,
) -> list[str]:
    """SubagentStart でこの子について渡す行。フローが無ければ空。例外は外に出さない。

    サブエージェントの自動 compact で消えても辿り直せるよう、ファイルの絶対パスを毎回名指しする。
    手順の文は `budget` 文字（と子 1 本の上限）まで。

    `full` が偽なら、ファイルの 1 行だけを返す。起動の cwd が親のツリーで子を何本も並べるとき
    （入れ子の孫の起動もこれ）は、どの子の担当かが hook から決められない。そこで全部の子の
    手順を並べると、別の子の手順に従いうる（M-4）。手順を並べるのは cwd がその子の
    ワークツリーのときだけにする。
    """
    if not child.is_child:
        return []
    path, base, exists = resolve(conf, root, child)
    if not exists:
        return []
    if not full:
        return [f"    フロー: {flow_text.clean(path)}"]
    lock = (
        "着手中なので、終わるまで書き換えられない（ロック）。着手のあとに書き換わったら"
        " ccnavi が知らせる。"
        if child.in_progress
        else "着手すると、終わるまで書き換えられなくなる（ロック）。"
    )
    lines = [
        f"    フロー: {flow_text.clean(path)}"
        f"（ユーザがボードで書いた {flow_text.clean(child.ticket)} の手順。"
        "承認済みの領域にあり、ユーザが持つもの。エージェントは編集しない）。作業の前にこのファイルを"
        "読み、その順に進めてください。文脈が要約されて見失ったら、"
        "このパスを読み直してください。" + lock
    ]
    data, why = load(path, base)
    if data is None:
        lines.append(f"    フローを読めない: {why}。ユーザが確かめてください")
        return lines
    room = max(0, min(budget, flow_render.CHILD_TEXT_LIMIT))
    if room == 0:
        lines.append(
            "    手順は SubagentStart の文の上限に達したので並べない。ファイルを読んでください"
        )
        return lines
    steps, kinds = flow_render.render(data, text_limit=room)
    lines.append(FENCE_OPEN)
    lines.extend(f"      {s}" for s in steps)
    lines.append(FENCE_CLOSE)
    worktree = flow_text.clean(tree.worktree_path(root, child.ticket))
    ticket, scope = flow_text.clean(child.ticket), flow_text.clean(scope) or "(空)"
    if flow_shape.ASK in kinds:
        lines.append(
            f"    {flow_shape.ASK} のノード: サブエージェントはユーザに聞けない"
            "（AskUserQuestion は渡されない）。そのノードで手を止め、問いと選択肢を添えて"
            "メインに返してください。メインがユーザに聞き、答えを持って同じサブエージェントを再開させる。"
        )
    if flow_shape.LOOP in kinds:
        lines.append(
            f"    {flow_shape.LOOP} のノード: 条件が成り立つあいだ「繰り返す」側へ進み、"
            "成り立たなくなったか、繰り返した回数が最大に達したら「抜ける」側へ進んでください。"
            "回数は自分で数えてください。最大に達しても条件が成り立つままだったときは、"
            "最後の報告にそのことを書いてください。"
        )
    if kinds & set(SPAWN):
        lines.append(
            "    subAgent / subAgentFlow のノード: Agent ツールがあれば入れ子の"
            "サブエージェントとして起動してください。そのプロンプトには必ず、担当の子チケット "
            f"{ticket}、ワークツリー {worktree}、範囲 {scope} を書き、"
            "他の子のワークツリーには触れないことを書いてください。"
            "Agent ツールが無ければ（入れ子の上限）、"
            "そのノードで手を止め、起動してほしいエージェントの種類・プロンプト・担当の子チケットの"
            "ワークツリーと範囲を添えてメインに返してください。メインがそのとおり起動し、結果を持って"
            "同じサブエージェントを再開させる。"
        )
    return lines
