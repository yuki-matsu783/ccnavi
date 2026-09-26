"""子チケットのフロー（作業の手順のグラフ）。設計 9.3.1・9.12、ADR-0085。

子チケット 1 本につき 1 本、担当のサブエージェントが作業中に読む手順書を置ける。
置き場は**承認済みの領域**の `<承認済みチケットの置き場>/flows/<子>.yml`
（既定 `.ccnavi/approved/flows/<子>.yml`）に固定で、チケットの欄では指さない。
置き場を持つツリーはチケットと同じ（承認済みチケットが在るツリー。プロジェクトの
チケットならそのプロジェクトのツリー）で、チケットと同じ git に乗る。

承認済みの領域は組み込みの守り（`builtin-guard-project-home`）がエージェントの
Write / Edit / NotebookEdit を止め、綴りの出るシェルからの書き込みも組み込みが止める。
だから「フローは人が書く」は運用ではなく判定で守られる（行き先を追えないシェルの書き込みは
止まらないので、着手のあとの書き換えは知らせる。下の「着手のあとの書き換え」）。人はボードのフロー編集画面で
書き、承認済みチケットと同じ運び方（`ccnavi-push-approved.sh`）でコミットする。
実行後の監視は承認済みの領域を範囲の外として咎めず、frontmatter の無いファイルは
副命令の書き込みとして外す（`post._script_writes`）。だから人が保存したフローが
エージェントの範囲外の変更として咎められることもない。

## 形

YAML の 1 文書で、最上位はキーと値の並び。ボードのフロー編集画面が書き、ここが読む。

    id, name, description?, version
    nodes:          [node, ...]
    connections:    [connection, ...]
    subAgentFlows?: [{id, name, nodes, connections}, ...]
    node       = {id, type, name, position: {x, y}, data: {...}}
    connection = {id, from, to, fromPort, toPort, condition?}

読むのは `yaml.safe_load`（ルールや設定と同じ読み手）に、別名（`*名前`）を拒む守りを足したもの
（`_Loader`）。別名は同じ部分木を何度でも指せるので、入れ子にすると小さなファイルが辿る量で
膨らむ（billion laughs）。手順書に別名は要らないので、量で切らずに別名ごと読まない。

ノードの種類（`type`）のうち、ここが中身を読むのは `start` `end` `prompt` `subAgent`
`askUserQuestion` `ifElse` `switch` `branch` `skill` `mcp` `subAgentFlow` `codex`
`branchSession` `group`。知らない種類は落とさず、種類の名前と `name` だけで並べる。

## 壊れたフローで止まらない

フローは人が書くデータで、形は保証されない。読む・並べるのどこでも例外を外に出さない。
読めなければ 1 行の知らせに落とし、`SubagentStart` の残り（子の一覧と範囲）はそのまま渡す。
大きさにも上限を置く。ファイルは `FILE_LIMIT` まで、1 ノードの項目は `ITEM_LIMIT` まで、
1 本の子の文は `CHILD_TEXT_LIMIT` まで。シンボリックリンク（ファイルそのものか、ツリーのルートから
そこまでの途中）は読まない。承認済みの領域の外を指していれば、エージェントが書ける中身を
人の手順書として渡すことになる。ふつうのファイルでないもの（名前付きパイプは開くと固まる）と
ハードリンク（外の名前から書き換えられる）も読まない（`read_bytes`）。

形の誤り（最上位がキーと値の並びでない、`nodes` が無い、ノードに `id` が無い・重なる、
`connections` が並びでない）も読めない理由として 1 行で言う（`shape_problem`）。
`ccnavi --lint --flow <パス>` は同じ読み手・同じ検査（`load`）でファイルを確かめ、読めなければ
error で言う。ボードのフロー編集画面は、開くときと保存の前に編集中の本文を一時ファイルに書いて
これに掛ける（ADR-0035。正しいかの答えはここ 1 か所）。

読むのは権威のツリー（承認済みチケットが在るツリー）の版だけ。子のワークツリーの写しは読まない。

## 着手のあとの書き換え

ロックと承認済みの領域の守りが止めるのは Write / Edit と、綴りの出るシェルの書き込みまで。
行き先を追えないシェルの書き込みは止まらない。着手のときにフローの指紋を
`phases/<親>/<子>.flow.json` に控え（`record_digest`）、SubagentStart と SubagentStop が
いまの指紋と比べて、違えば人とメインに知らせる（`changed_notice`。止めない）。

## 承認とロック

フローは承認の対象ではない（承認の指紋にも入らない）。中身は着手の前と終わった後なら
書き換えられる。着手中（`started_at` があり、`completed_at` も `cancelled_at` も無い）は、
読んでいる手順が作業の途中で変わらないよう、実行前の判定が Write / Edit / NotebookEdit を
止める（`lock_hit`）。エージェントの書き込みは承認済みの領域の守りでも止まるが、ロックは
その守りを切った設定でも効き、止めた理由を名指しする。ボードも着手中は保存しない。
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import stat
import unicodedata
from collections import deque

import yaml

from . import fsio, settings, tree
from . import ticket as ticket_mod

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
# サブエージェントに並べる手順の上限。多ければファイルを読ませる。
RENDER_LIMIT = 40
# 1 行に載せる文の長さの上限。
TEXT_LIMIT = 120
# 1 ノードに並べる選択肢・分岐・次の上限。
ITEM_LIMIT = 10
# 子 1 本の手順の文の上限（文字）と、SubagentStart 全体でフローに使う上限。
CHILD_TEXT_LIMIT = 4000
TOTAL_TEXT_LIMIT = 12000

# 止まってメインに返すノード。サブエージェントには利用者に聞く道具が無い。
ASK = "askUserQuestion"
# 入れ子のサブエージェントを起こすノード。
SPAWN = ("subAgent", "subAgentFlow")
# 出口を項目ごとに持つ種類と、項目の欄。
BRANCH_KEYS = {"ifElse": "branches", "switch": "branches", "branch": "branches", ASK: "options"}

# フローの文の中で ccnavi の名乗りを真似させない。`[` / `［` の直後が（互換文字・書式の制御・
# 結合文字・似た形の字を畳んで）`ccnavi` で始まる括弧は、亀甲括弧 `〔…〕` に置き換える。
_BADGE_WORD = "ccnavi"
_BADGE_OPEN = "〔"
_BADGE_CLOSE = "〕"
# 括弧の閉じを探す範囲（元の文字数）。
_BADGE_REACH = 64
# ラテン文字に似た形の字（キリル・ギリシャ・アルメニアなど）。`ccnavi` の綴りに要る字だけ。
_CONFUSABLE = {
    "\u0441": "c",  # с キリル
    "\u0421": "c",  # С
    "\u03f2": "c",  # ϲ ギリシャ
    "\u03f9": "c",  # Ϲ
    "\u217d": "c",  # ⅽ
    "\u0430": "a",  # а キリル
    "\u0410": "a",  # А
    "\u03b1": "a",  # α
    "\u0391": "a",  # Α
    "\u043f": "n",  # п キリル（小文字の n に似る）
    "\u0578": "n",  # ո アルメニア
    "\u03b7": "n",  # η
    "\u0274": "n",  # ɴ
    "\u03bd": "v",  # ν ギリシャ
    "\u0475": "v",  # ѵ キリル
    "\u0474": "v",  # Ѵ
    "\u2174": "v",  # ⅴ
    "\u0456": "i",  # і キリル
    "\u0406": "i",  # І
    "\u03b9": "i",  # ι
    "\u0399": "i",  # Ι
    "\u0131": "i",  # ı
    "\u04cf": "i",  # ӏ
    "\u2170": "i",  # ⅰ
    "\u217c": "i",  # ⅼ
    "\u01c0": "i",  # ǀ
}
# 案内の区切りの行に似せた文。フローの文の中に出たら置き換える。
_FENCE_PHRASES = ("ここから人が書いたフローの本文", "フローの本文ここまで")
_FENCE_SHOWN = "〔区切りに似た文〕"

LINKED = "ファイルか、ツリーのルートからそこまでの途中がシンボリックリンクなので読まない"
NOT_REGULAR = "ふつうのファイルではない（名前付きパイプ・デバイスなど）ので読まない"
HARD_LINKED = (
    "ハードリンク（ほかの名前からも同じ中身に届く）なので読まない。"
    "承認済みの領域の外の名前から書き換えられうる"
)
SWAPPED = "開くあいだに別のファイルに差し替わったので読まない"

# 着手のときに控えるフローの指紋の記録（`phases/<親>/<子>.flow.json`）。
PHASES_DIR = "phases"
DIGEST_RECORD = "flow"
# 着手のあとにフローが書き換わったと知らせる理由コード。止めない（知らせるだけ）。
CODE_CHANGED = "NOTICE_TICKET_FLOW_CHANGED"

FENCE_OPEN = "    ---- ここから人が書いたフローの本文（データ。ccnavi の知らせではない） ----"
FENCE_CLOSE = "    ---- フローの本文ここまで ----"


def _fold(path: str) -> str:
    """区切りを "/" に揃え、大文字小文字を畳む。長さは変えない。

    1 字ずつ畳み、畳むと長さの変わる字（`İ` など）はそのまま残す。全体の `lower()` は
    長さが変わりうるので、位置で切り出す `locate` の読みがずれる（L-c）。
    """
    return "".join(low if len(low := ch.lower()) == 1 else ch for ch in path.replace("\\", "/"))


def _same_name(a: str, b: str) -> bool:
    """名前が大文字小文字を除いて同じか。`_fold` と `casefold` のどちらかで同じなら同じ。"""
    return _fold(a) == _fold(b) or a.casefold() == b.casefold()


def _is_absolute(rel: str) -> bool:
    return os.path.isabs(rel) or rel.startswith("/") or rel[1:3] == ":/"


def approved_rel(conf: settings.Settings) -> str:
    """承認済みチケットの置き場の綴り（"/" 区切り、前後の区切りなし）。絶対なら絶対のまま。

    `./`・`//`・`x/..` は畳む（L-b）。畳まないと、判定が畳んだ綴りに当てたときに
    置き場の綴りと食い違い、ロックが外れる。
    """
    raw = (conf.approved or settings.DEFAULT_APPROVED).replace("\\", "/")
    if _is_absolute(raw):
        return posixpath.normpath(raw).rstrip("/") or "/"
    norm = posixpath.normpath(raw).strip("/")
    return raw.strip("/") if norm in ("", ".") else norm


def flow_rel(conf: settings.Settings, ticket_id: str) -> str:
    """子のフローの置き場。ツリーのルートからの相対（置き場が絶対ならその絶対パス）。"""
    return f"{approved_rel(conf)}/{FLOWS_DIR}/{ticket_id}{SUFFIX}"


def flow_file(conf: settings.Settings, tree_root: str, ticket_id: str) -> str:
    """このツリーでの子のフローの絶対パス（`./` や `x/..` は畳んだ綴り）。"""
    return os.path.normpath(
        os.path.join(settings.approved_dir(conf, tree_root), FLOWS_DIR, f"{ticket_id}{SUFFIX}")
    )


def linked(tree_root: str, path: str) -> bool:
    """tree_root から path までの途中（path 自身を含む）に、シンボリックリンクがあるか。

    `configsync._linked` と同じ読み方。綴りのままさかのぼり、実体が tree_root に着いたところで
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


def resolve(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> tuple[str, str, bool]:
    """フローのファイルの絶対パスと、それを持つツリーのルートと、在るかどうか。

    読むのは権威のツリー（承認済みチケットが在るツリー）の版だけ。子のワークツリーの版は
    読まない。子のワークツリーはエージェントが作業する場所で、そこの写しはシェルの書き込み
    （行き先を追えない形）で書き換えられうる（M-2）。
    リンクでも「在る」とする（読むかどうかは `load` が決める。黙って別の版へ移らない）。
    """
    base = child.tree_root or root
    path = flow_file(conf, base, child.ticket)
    return path, base, os.path.lexists(path)


def info(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> dict | None:
    """ボード（`--explain --json`）に出すフローの欄。子でなければ None。

    閉じた子（終わった・取り消した）でフローが無ければ None。閉じた子にフローを作っても
    読まれる場面が無い。ボードは欄が無ければ「フローを作る」を出さない。フローが在る
    閉じた子は欄を返す（人が見返せる）。

    `tree` はファイルを持つツリーのルート。ボードはそこからファイルまでの途中にリンクが
    あれば書かない。`linked` はその途中にリンクがあるか（在るときだけ見る）。
    """
    if not child.is_child:
        return None
    path, base, exists = resolve(conf, root, child)
    if not exists and child.state in (ticket_mod.DONE, ticket_mod.CANCELLED):
        return None
    return {
        "path": path,
        "rel": flow_rel(conf, child.ticket),
        "tree": base,
        "exists": exists,
        "linked": exists and linked(base, path),
        "locked": child.in_progress and child.state == ticket_mod.DOING,
    }


# ---- ロック


def locate(conf: settings.Settings, root: str, path: str) -> tuple[str, str | None] | None:
    """このパスが子のフローの置き場なら（ファイルの名前, そのツリーのプロジェクト）。違えば None。

    名前は `flows/` の下の残り（`<子>.yml`）。プロジェクトは置き場を持つツリーのもの
    （ワークスペースなら空、ワークスペースの外なら None）。綴りは解いたものでも解く前の
    ものでもよい。大文字小文字は範囲の照合と同じく区別しない。

    名前は Windows で同じファイルに届く綴りを畳む。末尾の `.` と空白、`:` から後ろ
    （`::$DATA` などの代替データストリーム）を落とす（止める向きだけ）。8.3 形式の短い
    名前（子の名前が 8 字を超えるときの `I0001-~1.YML` など）は畳めない。解いた綴り
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
        # `..` を落とした残りの綴りで当て、どのプロジェクトの子でも止める（止める向き）。
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
    copies: list[ticket_mod.Ticket], project: str | None, name: str
) -> ticket_mod.Ticket | None:
    """この書き込みを止める、着手中の子。無ければ None。

    `copies` はどのツリーの写しも並べたもの（識別子で 1 本に畳む前）。どれか 1 本でも
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
    conf: settings.Settings, root: str, copies: list[ticket_mod.Ticket], path: str
) -> ticket_mod.Ticket | None:
    """書き込み先が、着手中の子のフローとハードリンクで同じ中身なら、その子。無ければ None。

    ハードリンクは綴りに置き場が出ないので `locate` では当たらない（M-1）。書き込み先が
    在って、名前が 2 つ以上あるときだけ、着手中の子のフロー（どのツリーの写しの置き場も）と
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


def locked_message(conf: settings.Settings, child: ticket_mod.Ticket, path: str) -> str:
    """ロックで止めたときの文面。"""
    ticket = clean(child.ticket)
    return "\n".join(
        [
            f"[ccnavi] {CODE_LOCKED} (source: {clean(child.path)})",
            f"ticket: {ticket} ({clean(child.title)}), started {clean(child.started_at)}",
            f"path: {clean(path)}",
            f"子チケット {ticket} は着手中なので、そのフロー"
            f"（`{flow_rel(conf, child.ticket)}`）は書き換えられません。"
            "担当のサブエージェントが読んでいる手順が作業の途中で変わるのを防ぐためです。"
            "フローは人がボードのフロー編集画面で書くもので、エージェントは書きません。",
            f"ロックは {ticket} が `ccnavi-ticket.sh finish` で終わるか "
            "`cancel` で取り消されると外れます。手順を直したいなら、終わってから"
            "人に書き換えてもらうか、次の子チケットのフローに書いてもらってください。",
        ]
    )


# ---- 読む


def read_bytes(path: str, tree_root: str = "") -> tuple[bytes | None, str]:
    """フローのファイルの中身（バイト）。読まないなら (None, 理由)。例外は外に出さない。

    読むのはふつうのファイル（`S_ISREG`）で、名前が 1 つ（ハードリンクでない）ものだけ。
    名前付きパイプを開くと書き手が来るまで戻らず、SubagentStart が固まる（H-1）。
    ハードリンクは承認済みの領域の外の名前から書き換えられる（M-1）。
    `lstat` で確かめてから `O_NONBLOCK | O_NOFOLLOW` で開き、開いたものを `fstat` で
    もう一度確かめる（ふつうのファイルで、`lstat` と同じ inode）。確かめてから開くまでに
    差し替えられても、開いたものが違えば読まない。Windows には `O_NONBLOCK` も
    `O_NOFOLLOW` も無いので、確かめ直しだけが効く。
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
        return None, f"読めない ({clean(exc.strerror or type(exc).__name__)})"
    except Exception as exc:  # noqa: BLE001  壊れたデータで SubagentStart を落とさない
        return None, f"読めない ({type(exc).__name__})"


class _AliasRefused(yaml.YAMLError):
    """フローに別名（`*名前`）があった。"""


class _Loader(yaml.SafeLoader):
    """`yaml.safe_load` の読み手に、別名を拒む守りを足したもの。

    別名は同じ部分木を何度でも指せる。入れ子にすると、小さなファイルでも辿る量が指数で
    膨らむ（billion laughs）。自分を指す別名は循環する値になる。手順書に別名は要らないので、
    出てきた時点で読むのをやめる（量で切ると、上限の手前まで辿る手間は残る）。
    """

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise _AliasRefused("別名")
        return super().compose_node(parent, index)


ALIASED = "YAML の別名（`*名前`）があるので読まない。同じ部分木を何度も辿らせて膨らませられる"


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
    try:
        # _Loader は SafeLoader に別名の守りを足したもの（任意の型は作らない）。
        data = yaml.load(raw.decode("utf-8-sig"), Loader=_Loader)
    except _AliasRefused:
        return None, ALIASED
    except OSError as exc:
        return None, f"読めない ({clean(exc.strerror or type(exc).__name__)})"
    except RecursionError:
        return None, "入れ子が深すぎて YAML として読めない"
    except yaml.YAMLError as exc:
        return None, f"YAML として読めない ({_line(_yaml_problem(exc))})"
    except (ValueError, TypeError) as exc:
        return None, f"YAML として読めない ({_line(exc)})"
    except Exception as exc:  # noqa: BLE001  壊れたデータで SubagentStart を落とさない
        return None, f"読めない ({type(exc).__name__})"
    why = shape_problem(data)
    if why:
        return None, why
    return data, ""


def shape_problem(data) -> str:
    """読めた中身の形の誤り（最初の 1 つ）。無ければ空。例外は外に出さない。

    SubagentStart の読み（`load`）と `--lint --flow` が同じここを通る。見るのは手順として
    並べる土台だけ。最上位がキーと値の並び、`nodes` がキーと値の並びの並びで、どれも空でない
    文字列の `id` を持ち、`id` が重ならない。`connections` は在れば、キーと値の並びの並び。
    `id` が無い・重なるノードは並べるときに落ちるので、黙って手順が欠けないよう読まない側に倒す。
    """
    if not isinstance(data, dict):
        return "最上位がキーと値の並びではない"
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return "`nodes` の並びが無い"
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            return f"nodes[{index}] がキーと値の並びではない"
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            return f"nodes[{index}] に文字列の id が無い"
        if node_id in seen:
            return f"ノードの id が重なっている（{_line(node_id)}）"
        seen.add(node_id)
    if "connections" in data:
        connections = data.get("connections")
        if not isinstance(connections, list):
            return "`connections` が並びではない"
        for index, connection in enumerate(connections):
            if not isinstance(connection, dict):
                return f"connections[{index}] がキーと値の並びではない"
    return ""


# ---- 着手のあとの書き換えを知らせる


def fingerprint(path: str, tree_root: str = "") -> str:
    """フローのファイルの指紋。無ければ `absent`、読めれば `sha256:<16 進>`、読まないなら
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


def digest_record_path(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> str:
    """着手のときに控えた指紋の記録の置き場。フローと同じツリーの `phases/<親>/<子>.flow.json`。"""
    # 形は `approval.child_record_path` と同じ。flow は approval より下の段なので読まず、形を写す
    # （`test_flow_hardening` が突き合わせる）。
    approved = settings.approved_dir(conf, child.tree_root or root)
    return os.path.join(approved, PHASES_DIR, child.parent, f"{child.ticket}.{DIGEST_RECORD}.json")


def record_digest(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> tuple[str, str]:
    """着手のときのフローの指紋を控える。(書いた記録のパス, 書けなかった理由)。

    置き場は子の記録（`.risk.json` など）と同じ `phases/<親>/` で、承認済みの領域にあるので
    エージェントは書けず、親のブランチに乗って他の機械へ届く。フローが無くても控える
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
    return target, clean(failed)


def _describe(mark: str) -> str:
    if mark == "absent":
        return "無い"
    if mark.startswith("sha256:"):
        return mark[: len("sha256:") + 12]
    return clean(mark.partition(":")[2]) or "読めない"


def changed_notice(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> str:
    """着手中の子のフローが、着手のときに控えた指紋から変わっていれば、その知らせ。無ければ空。

    知らせるだけで止めない（締める向き）。ロックと承認済みの領域の守りは Write / Edit と、
    綴りの出るシェルの書き込みを止めるが、行き先を追えないシェルの書き込みは止まらない
    （M-2）。控えが無い（この仕組みより前に着手した）子には何も言わない。例外は外に出さない。
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
            f"{clean(child.ticket)} のフロー {clean(path)} が、着手のとき（{_describe(before)}）と"
            f"違う（いま {_describe(now)}）。担当のサブエージェントが読んだ手順と、人が渡した"
            "手順が食い違っているかもしれない。誰が書き換えたかを人が確かめる"
            "（ロックは Write / Edit を止めるが、シェルから行き先を追えない形で書くと止まらない）"
        )
    except Exception:  # noqa: BLE001  知らせのために hook を落とさない
        return ""


# ---- 並べる


def clean(text) -> str:
    """文脈に載せる値から、改行と制御文字（書式の制御も）を除く。"""
    shown = str(text if text is not None else "")
    return "".join(
        " " if ch in "\r\n\t\v\f\x85  " else ch
        for ch in shown
        if ch in "\r\n\t\v\f\x85  " or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def _text(value) -> str:
    """文字列か数だけを文にする。並びや辞書は中身を辿らない（深い入れ子で落ちない）。"""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        # 整数の値の小数（`1.0`）は整数の綴りにする。ボード（JavaScript の `String`）と揃える。
        return str(int(value))
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _line(value) -> str:
    """1 行に畳んで切る。ccnavi の名乗りは真似させない。"""
    if isinstance(value, Exception):
        value = str(value)
    text = value if isinstance(value, str) else _text(value)
    shown = " ".join(clean(text[: TEXT_LIMIT * 8]).split())
    shown = _neutral(shown)
    if len(shown) > TEXT_LIMIT:
        shown = shown[:TEXT_LIMIT] + "…"
    return shown


_IGNORED = ("Mn", "Mc", "Me", "Cf", "Cc", "Zs")


def _skeleton(text: str) -> tuple[str, list[int]]:
    """見た目で比べるための綴りと、その 1 字ずつの元の位置。

    互換分解（NFKD。全角の `［` は `[`、`ⅽ` は `c`）し、結合文字・書式の制御・制御文字・
    空白を落とし、似た形の字（`_CONFUSABLE`）をラテン文字に寄せ、大文字小文字を畳む。
    """
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for part in unicodedata.normalize("NFKD", ch):
            if part.isspace() or unicodedata.category(part) in _IGNORED:
                continue
            for folded in _CONFUSABLE.get(part, part).casefold():
                chars.append(_CONFUSABLE.get(folded, folded))
                origin.append(i)
    return "".join(chars), origin


def _neutral(text: str) -> str:
    """フローの文が ccnavi の知らせや案内の区切りに見えないようにする（L-a）。

    - `[` / `［`（互換文字も）の直後が `ccnavi` で始まる括弧は、開きと、その先の最初の閉じ
      （`]` / `］`）を `〔` `〕` に置き換える。`[ccnavi dry-run]`・`[CCNAVI]`・幅の無い字や
      結合文字を挟んだもの・キリル文字の `с` で綴ったものも同じ
    - 案内の区切りの行の文（`_FENCE_PHRASES`）は `_FENCE_SHOWN` に置き換える
    """
    skeleton, origin = _skeleton(text)
    replace: dict[int, str] = {}
    word = _BADGE_WORD
    at = skeleton.find("[")
    while at >= 0:
        if skeleton.startswith(word, at + 1):
            start = origin[at]
            replace[start] = _BADGE_OPEN
            close = skeleton.find("]", at + 1)
            if close >= 0 and origin[close] - start <= _BADGE_REACH:
                replace[origin[close]] = _BADGE_CLOSE
        at = skeleton.find("[", at + 1)
    for phrase in _FENCE_PHRASES:
        needle = _skeleton(phrase)[0]
        at = skeleton.find(needle)
        while at >= 0:
            first, last = origin[at], origin[at + len(needle) - 1]
            replace[first] = _FENCE_SHOWN
            for i in range(first + 1, last + 1):
                replace[i] = ""
            at = skeleton.find(needle, at + len(needle))
    if not replace:
        return text
    return "".join(replace.get(i, ch) for i, ch in enumerate(text))


def impersonates(text: str) -> bool:
    """文に ccnavi の名乗りか案内の区切りに見える箇所が残っているか。テストと確かめ用。"""
    skeleton, _ = _skeleton(text)
    if any(_skeleton(p)[0] in skeleton for p in _FENCE_PHRASES):
        return True
    return f"[{_BADGE_WORD}" in skeleton


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _capped(parts: list[str], total: int) -> list[str]:
    """並びを ITEM_LIMIT で切り、残りの数を添える。"""
    if total > len(parts):
        return parts + [f"…ほか {total - len(parts)} 件"]
    return parts


def _labels(items, key: str) -> list[str]:
    entries = [i for i in _list(items) if isinstance(i, dict)]
    return _capped([_line(i.get(key)) for i in entries[:ITEM_LIMIT]], len(entries))


def _summary(node: dict, flows: dict) -> str:
    """ノード 1 つの中身を 1 行で。知らない種類は空。"""
    kind = _text(node.get("type"))
    data = _dict(node.get("data"))
    if kind == "prompt":
        return _line(data.get("prompt"))
    if kind == "subAgent":
        head = data.get("description") or data.get("agentDefinition")
        prompt = _line(data.get("prompt"))
        text = _line(head)
        if prompt:
            text += f" / プロンプト: {prompt}"
        if _line(data.get("builtInType")):
            text += f" / 種類: {_line(data.get('builtInType'))}"
        return text
    if kind == ASK:
        options = " | ".join(_labels(data.get("options"), "label"))
        multi = "（複数選択）" if data.get("multiSelect") is True else ""
        return f"問い: {_line(data.get('questionText'))} 選択肢{multi}: {options}"
    if kind in ("ifElse", "switch", "branch"):
        branches = [b for b in _list(data.get("branches")) if isinstance(b, dict)]
        parts = [
            f"{_line(b.get('label'))}={_line(b.get('condition'))}" for b in branches[:ITEM_LIMIT]
        ]
        parts = _capped(parts, len(branches))
        target = _line(data.get("evaluationTarget"))
        return (f"{target}: " if target else "") + " | ".join(parts)
    if kind == "skill":
        return f"スキル {_line(data.get('name'))}: {_line(data.get('description'))}"
    if kind == "mcp":
        tool = _line(data.get("toolName"))
        return f"MCP {_line(data.get('serverId'))}" + (f" / {tool}" if tool else "")
    if kind == "subAgentFlow":
        ref = flows.get(_text(data.get("subAgentFlowId")))
        name = _line(ref.get("name")) if isinstance(ref, dict) else ""
        return (_line(data.get("label")) or name) + (f"（サブフロー {name}）" if name else "")
    if kind == "codex":
        return f"Codex: {_line(data.get('prompt'))}"
    if kind in ("group", "branchSession", "start", "end"):
        return _line(data.get("label") or data.get("workDescription"))
    return ""


def _branch_index(port: str) -> int | None:
    """`branch-<番号>` の番号。番号は ASCII の数字だけ。違う綴りなら None。"""
    head, _, digits = port.rpartition("-")
    if head != "branch" or not digits or not (digits.isascii() and digits.isdigit()):
        return None
    if len(digits) > 6:
        return None
    return int(digits)


def _port_key(connection: dict) -> tuple:
    """出口の並べ順。`branch-<番号>` は番号の順、それ以外は綴りの順で後ろ。"""
    port = _text(connection.get("fromPort"))
    index = _branch_index(port)
    return (0, index, "") if index is not None else (1, 0, port)


def _port_label(node: dict, port: str) -> str:
    """分岐の出口の名前。

    出口が項目の `id` とちょうど同じか、`branch-<番号>` の番号が項目の位置なら、その `label`。
    部分一致は採らない（`b` が `branch-1` に当たる）。項目は種類で決まる欄（分岐は `branches`、
    問いは `options`）の辞書だけを数える。ボードの線の言葉（`flow-doc.ts` の
    `connectionLabel`）と同じ読み方。
    """
    key = BRANCH_KEYS.get(_text(node.get("type")))
    if not port or key is None:
        return ""
    items = [i for i in _list(_dict(node.get("data")).get(key)) if isinstance(i, dict)]
    for item in items:
        if _text(item.get("id")) and _text(item.get("id")) == port:
            return _line(item.get("label"))
    index = _branch_index(port)
    if index is not None and index < len(items):
        return _line(items[index].get("label"))
    return ""


def _node_id(node: dict) -> str:
    return _text(node.get("id"))


def _order(ids: list[str], kinds: dict[str, str], out: dict[str, list[dict]]) -> list[str]:
    """並べる順。`start` から辿り、辿れなかったものは後ろに元の順で足す。O(ノード + 線)。"""
    known = set(ids)
    starts = [i for i in ids if kinds.get(i) == "start"]
    seen: dict[str, None] = {}
    queue = deque(starts or ids[:1])
    while queue:
        current = queue.popleft()
        if current in seen or current not in known:
            continue
        seen[current] = None
        for c in out.get(current, []):
            queue.append(_text(c.get("to")))
    return list(seen) + [i for i in ids if i not in seen]


def render(
    data: dict, limit: int = RENDER_LIMIT, text_limit: int = CHILD_TEXT_LIMIT
) -> tuple[list[str], set[str]]:
    """フローを順に並べた行と、出てきたノードの種類の集合。例外は外に出さない。

    YAML を読まなくても手順が追えるよう、1 ノード 1 行で `<番号>. [<種類>] <名前>: <中身>`
    と次の番号を並べる。知らない種類は種類の名前と `name` だけ。ノードは `limit` 件まで、
    文は `text_limit` 文字まで。
    """
    try:
        return _render(data, limit, text_limit)
    except Exception:  # noqa: BLE001  壊れたデータで SubagentStart を落とさない
        return ["（フローを並べられない。ファイルを直接読んで判断する）"], set()


def _render(data, limit: int, text_limit: int) -> tuple[list[str], set[str]]:
    data = _dict(data)
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for n in _list(data.get("nodes")):
        if isinstance(n, dict) and _node_id(n) and _node_id(n) not in seen_ids:
            seen_ids.add(_node_id(n))
            nodes.append(n)
    connections = [c for c in _list(data.get("connections")) if isinstance(c, dict)]
    flows = {
        _text(f.get("id")): f
        for f in _list(data.get("subAgentFlows"))
        if isinstance(f, dict) and _text(f.get("id"))
    }
    by_id = {_node_id(n): n for n in nodes}
    ids = list(by_id)
    kinds_by = {i: _text(n.get("type")) for i, n in by_id.items()}
    out: dict[str, list[dict]] = {}
    for c in connections:
        out.setdefault(_text(c.get("from")), []).append(c)
    for edges in out.values():
        edges.sort(key=_port_key)
    order = _order(ids, kinds_by, out)
    number = {nid: i + 1 for i, nid in enumerate(order)}
    kinds = set(kinds_by.values())
    lines: list[str] = []
    used = 0
    shown = 0
    for nid in order[:limit]:
        node = by_id[nid]
        kind = _line(kinds_by[nid]) or "?"
        name = _line(node.get("name")) or _line(nid)
        body = _summary(node, flows)
        text = f"{number[nid]}. [{kind}] {name}" + (f": {body}" if body else "")
        edges = [c for c in out.get(nid, []) if _text(c.get("to")) in number]
        nexts = []
        for c in edges[:ITEM_LIMIT]:
            label = _line(c.get("condition")) or _port_label(node, _text(c.get("fromPort")))
            nexts.append(f"{number[_text(c.get('to'))]}" + (f"（{label}）" if label else ""))
        nexts = _capped(nexts, len(edges))
        if nexts:
            text += " → " + ", ".join(nexts)
        # 組み立てた行でも見る。種類の名前が `ccnavi…` だと、こちらの `[<種類>]` が名乗りになる。
        text = _neutral(text)
        if used + len(text) > text_limit and lines:
            break
        lines.append(text)
        used += len(text)
        shown += 1
    if len(order) > shown:
        lines.append(f"…ほか {len(order) - shown} 件。続きはファイルを読む")
    return lines, kinds


def briefing(
    conf: settings.Settings,
    root: str,
    child: ticket_mod.Ticket,
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
        return [f"    フロー: {clean(path)}"]
    lock = (
        "着手中なので、終わるまで書き換えられない（ロック）。着手のあとに書き換わったら"
        "ccnavi が知らせる。"
        if child.in_progress
        else "着手すると、終わるまで書き換えられなくなる（ロック）。"
    )
    lines = [
        f"    フロー: {clean(path)}（人がボードで書いた {clean(child.ticket)} の手順。"
        "承認済みの領域にあり、人が持つもの。エージェントは編集しない）。作業の前にこのファイルを"
        "読み、その順に進める。文脈が要約されて見失ったら、このパスを読み直す。" + lock
    ]
    data, why = load(path, base)
    if data is None:
        lines.append(f"    フローを読めない: {why}。人に確かめる")
        return lines
    room = max(0, min(budget, CHILD_TEXT_LIMIT))
    if room == 0:
        lines.append("    手順は SubagentStart の文の上限に達したので並べない。ファイルを読む")
        return lines
    steps, kinds = render(data, text_limit=room)
    lines.append(FENCE_OPEN)
    lines.extend(f"      {s}" for s in steps)
    lines.append(FENCE_CLOSE)
    worktree = clean(tree.worktree_path(root, child.ticket))
    ticket, scope = clean(child.ticket), clean(scope) or "(空)"
    if ASK in kinds:
        lines.append(
            f"    {ASK} のノード: サブエージェントは利用者に聞けない"
            "（AskUserQuestion は渡されない）。そのノードで手を止め、問いと選択肢を添えて"
            "メインに返す。メインが利用者に聞き、答えを持って同じサブエージェントを再開させる。"
        )
    if kinds & set(SPAWN):
        lines.append(
            "    subAgent / subAgentFlow のノード: Agent ツールがあれば入れ子の"
            "サブエージェントとして起動する。そのプロンプトには必ず、担当の子チケット "
            f"{ticket}、ワークツリー {worktree}、範囲 {scope} を書き、"
            "他の子のワークツリーには触れないことを書く。Agent ツールが無ければ（入れ子の上限）、"
            "そのノードで手を止め、起動してほしいエージェントの種類・プロンプト・担当の子チケットの"
            "ワークツリーと範囲を添えてメインに返す。メインがそのとおり起動し、結果を持って"
            "同じサブエージェントを再開させる。"
        )
    return lines
