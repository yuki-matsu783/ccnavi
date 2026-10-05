"""子チケットのフロー（作業の手順のグラフ）。設計 9.3.1・9.12。着手中は書き換えを止める。

子チケット 1 本につき 1 本、担当のサブエージェントが作業中に読む手順書を置ける。
置き場は**承認済みの領域**の `<承認済みチケットの置き場>/flows/<子>.yml`
（既定 `.ccnavi/approved/flows/<子>.yml`）に固定で、チケットの欄では指さない。
置き場を持つツリーはチケットと同じ（承認済みチケットが在るツリー。プロジェクトの
チケットならそのプロジェクトのツリー）で、チケットと同じ git に乗る。

承認済みの領域は組み込みの保護（`builtin-guard-project-home`）がエージェントの
Write / Edit / NotebookEdit を止め、パスの出るシェルからの書き込みも組み込みが止める。
だから「フローはユーザが書く」は運用ではなく判定で守られる（行き先を追えないシェルの書き込みは
止まらないので、着手のあとの書き換えは知らせる。下の「着手のあとの書き換え」）。ユーザはボードのフロー編集画面で
書き、承認済みチケットと同じく承認の push（`ccnavi-push-approved.sh`）でコミットする。
実行後チェックは承認済みの領域を範囲の外として報告せず、frontmatter の無いファイルは
副命令の書き込みとして外す（`post._script_writes`）。だからユーザが保存したフローが
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
`askUserQuestion` `ifElse` `switch` `branch` `skill` `mcp` `subAgentFlow` `codex`
`branchSession`。知らない種類は落とさず、種類の名前と `name` だけで並べる。
`group` は図の上の囲み（ボードの枠）で手順ではないので並べない（中のノードは `parentId` が
あっても、ほかのノードと同じに並べる）。

## 壊れたフローで止まらない

フローはユーザが書くデータで、形は保証されない。読む・並べるのどこでも例外を外に出さない。
読めなければ 1 行の知らせにして、`SubagentStart` の残り（子の一覧と範囲）はそのまま渡す。
大きさにも上限を置く。ファイルは `FILE_LIMIT` まで、1 ノードの項目は `ITEM_LIMIT` まで、
1 本の子の文は `CHILD_TEXT_LIMIT` まで。シンボリックリンク（ファイルそのものか、ツリーのルートから
そこまでの途中）は読まない。承認済みの領域の外を指していれば、エージェントが書ける中身を
ユーザの手順書として渡すことになる。ふつうのファイルでないもの（名前付きパイプは開くと固まる）と
ハードリンク（外の名前から書き換えられる）も読まない（`read_bytes`）。

形の誤り（最上位がマッピングでない、`nodes` が無い、ノードに `id` が無い・重なる、
`connections` がリストでない）も読めない理由として 1 行で言う（`shape_problem`）。
`ccnavi --lint --flow <パス>` は同じ読み手・同じ検査（`load`）でファイルを確かめ、読めなければ
error で言う。ボードのフロー編集画面は、開くときと保存の前に編集中の本文を一時ファイルに書いて
これに掛ける（拡張は判定を自分で出さない。正しいかの答えはここ 1 か所）。`--json` なら
読めた中身も載せ（`as_json`）、画面は自分の読み（YAML 1.2）と見比べて、値の意味が食い違えば
開かない・保存しない。
読めたフローの線の構造（`structure_problems`）と名前の表記（`name_problems`）は warn で足し、
読むのは止めない。

読むのは本物とするツリー（承認済みチケットが在るツリー）の版だけ。子のワークツリー上の版は読まない。

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
import unicodedata
from collections import deque

import yaml

from ..infra import fsio, settings, tree, yamlread
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

# 止まってメインに返すノード。サブエージェントにはユーザに聞く道具が無い。
ASK = "askUserQuestion"
# 図の上の囲み（ボードの枠）。手順ではないので並べない
GROUP = "group"
# 入れ子のサブエージェントを起こすノード。
SPAWN = ("subAgent", "subAgentFlow")
# 出口を項目ごとに持つ種類と、項目の欄。
BRANCH_KEYS = {"ifElse": "branches", "switch": "branches", "branch": "branches", ASK: "options"}

# フローの文の中で ccnavi の接頭辞を真似させない。`[` / `［` の直後が（互換文字・書式の制御・
# 結合文字・似た形の字をそろえて）`ccnavi` で始まる括弧は、亀甲括弧 `〔…〕` に置き換える。
_BADGE_WORD = "ccnavi"
_BADGE_OPEN = "〔"
_BADGE_CLOSE = "〕"
# 括弧の閉じを探す範囲（元の文字数）。
_BADGE_REACH = 64
# ラテン文字に似た形の字（キリル・ギリシャ・アルメニアなど）。`ccnavi` の表記に要る字だけ。
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
# プロジェクトのスキルの目録（projskills.FENCE_OPEN / FENCE_CLOSE）の区切りも同じく置き換える。
_FENCE_PHRASES = (
    "ここからユーザが書いたフローの本文",
    "フローの本文ここまで",
    "ここからプロジェクトのスキルの目録",
    "目録ここまで",
)
_FENCE_SHOWN = "〔区切りに似た文〕"

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
    （`briefing` も着手のハッシュも読まない）。効くのはユーザが取り込んで `flow_rel` に保存した
    ものだけ。
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


def resolve(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> tuple[str, str, bool]:
    """フローのファイルの絶対パスと、それを持つツリーのルートと、在るかどうか。

    読むのは本物とするツリー（承認済みチケットが在るツリー）の版だけ。子のワークツリーの版は
    読まない。子のワークツリーはエージェントが作業する場所で、そこにある版はシェルの書き込み
    （行き先を追えない形）で書き換えられうる（M-2）。
    リンクでも「在る」とする（読むかどうかは `load` が決める。気づかないうちに別の版へ移ることは
    しない）。
    """
    base = child.tree_root or root
    path = flow_file(conf, base, child.ticket)
    return path, base, os.path.lexists(path)


def info(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> dict | None:
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
    if not exists and child.state in (ticket_mod.DONE, ticket_mod.CANCELLED):
        return None
    draft = draft_file(conf, base, child.ticket)
    draft_exists = os.path.lexists(draft)
    return {
        "path": path,
        "rel": flow_rel(conf, child.ticket),
        "tree": base,
        "exists": exists,
        "linked": exists and linked(base, path),
        "locked": child.in_progress and child.state == ticket_mod.DOING,
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
    （`::$DATA` などの代替データストリーム）を落とす（止める向きだけ）。8.3 形式の短い
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
        # `..` を落とした残りのパスで当て、どのプロジェクトの子でも止める（止める向き）。
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
    conf: settings.Settings, root: str, copies: list[ticket_mod.Ticket], path: str
) -> ticket_mod.Ticket | None:
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
    名前付きパイプを開くと書き手が来るまで戻らず、SubagentStart が固まる（H-1）。
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
        return None, f"読めない ({clean(exc.strerror or type(exc).__name__)})"
    except Exception as exc:  # noqa: BLE001  壊れたデータで SubagentStart を落とさない
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

    文字は UTF-8（先頭の BOM は 1 つ外す。`utf-8-sig`）。UTF-8 として壊れていれば読まない
    （置き換え文字で埋めて読むと、壊れた部分を落とした手順が渡る）。
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
        return {JSON_MARK: "set", "text": _line(repr(sorted(map(repr, value))))}
    if isinstance(value, tuple):
        return {JSON_MARK: "tuple", "text": _line(repr(value))}
    return {JSON_MARK: "other", "text": type(value).__name__}


def _digits(value: int) -> str:
    try:
        return str(value)
    except ValueError:  # 桁の上限（sys.set_int_max_str_digits）を越える
        return "(桁が多すぎる)"


def shape_problem(data) -> str:
    """読めた中身の形の誤り（最初の 1 つ）。無ければ空。例外は外に出さない。

    SubagentStart の読み（`load`）と `--lint --flow` が同じここを通る。見るのは手順として
    並べるのに要る形だけ。最上位がマッピング、`nodes` がマッピングのリストで、どれも空でない
    文字列の `id` を持ち、`id` が重ならない。`connections` は在れば、マッピングのリスト。
    `id` が無い・重なるノードは並べるときに落ちるので、気づかないうちに手順が欠けることのないよう、
    読まない扱いにする。
    """
    if not isinstance(data, dict):
        return "最上位がマッピングではない"
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return "`nodes` のリストが無い"
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            return f"nodes[{index}] がマッピングではない"
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            return f"nodes[{index}] に文字列の id が無い"
        if node_id in seen:
            return f"ノードの id が重なっている（{_line(node_id)}）"
        seen.add(node_id)
    if "connections" in data:
        connections = data.get("connections")
        if not isinstance(connections, list):
            return "`connections` がリストではない"
        for index, connection in enumerate(connections):
            if not isinstance(connection, dict):
                return f"connections[{index}] がマッピングではない"
    return ""


# ---- 線の構造と名前（`--lint --flow` の warn）
#
# 読めるか・形（`shape_problem`）の外にある、手順として怪しいところ。読むのは止めない（warn）。
# `SubagentStart` は見ない（並べ方は `render` のまま）。巡回は意図して書くことがあるので言わない。

# 並べない（手順でない）種類。構造の検査からも外す。
_NOT_STEP = (GROUP,)


def _step_nodes(data) -> list[dict]:
    """構造を見るノード。辞書で `id` を持ち、グループでないもの（重なった `id` は最初の 1 つ）。"""
    nodes: list[dict] = []
    seen: set[str] = set()
    for n in _list(_dict(data).get("nodes")):
        if not isinstance(n, dict) or _text(n.get("type")) in _NOT_STEP:
            continue
        nid = _node_id(n)
        if nid and nid not in seen:
            seen.add(nid)
            nodes.append(n)
    return nodes


def _named(node: dict) -> str:
    """苦情で名指しするノード。`<id>（<名前>）`。"""
    nid, name = _line(_node_id(node)), _line(node.get("name"))
    return f"{nid}（{name}）" if name and name != nid else nid


def _names(nodes: list[dict]) -> str:
    return ", ".join(_capped([_named(n) for n in nodes[:ITEM_LIMIT]], len(nodes)))


def _port_taken(node: dict, index: int, item: dict, ports: set[str]) -> bool:
    """項目（分岐・選択肢）の出口に線があるか。

    読み方は `_port_label` と同じ（項目の `id` か `branch-<番号>`）。
    """
    item_id = _text(item.get("id"))
    if item_id and item_id in ports:
        return True
    return any(_branch_index(port) == index for port in ports)


def structure_problems(data) -> list[str]:
    """線の構造の怪しいところ（1 件 1 行）。無ければ空。例外は外に出さない。

    見るのは、線の `from` / `to` が無いノードを指す、`start` から届かないノード、`start` に入る線、
    `end` から出る線、分岐・問いの出口に線が無い、`end` が無い、`start` が無い。グループは外す
    （手順ではない）。巡回は言わない。サブフロー（`subAgentFlows`）の中は見ない。

    出口は画面（`flow-doc.ts` の `portsOf`）と同じに読む。複数選択（`multiSelect: true`）の問いは
    選択肢ごとに出口を分けず、`output` の 1 本だけ。グループへ出る線も、出る側の出口は使っている
    （線は手順に数えないが、「出口に線が無い」とは言わない）。無いノードを指す線は
    `ITEM_LIMIT` 件まで言い、残りは数だけつける。
    """
    try:
        return _structure_problems(data)
    except Exception:  # noqa: BLE001  壊れたデータで lint を落とさない
        return ["線の構造を確かめられない（中身の型が崩れている）"]


def _structure_problems(data) -> list[str]:
    data = _dict(data)
    nodes = _step_nodes(data)
    by_id = {_node_id(n): n for n in nodes}
    groups = {
        _node_id(n)
        for n in _list(data.get("nodes"))
        if isinstance(n, dict) and _text(n.get("type")) in _NOT_STEP
    }
    kinds = {nid: _text(n.get("type")) for nid, n in by_id.items()}
    out: list[str] = []
    missing: list[str] = []
    edges: dict[str, list[str]] = {}
    ports: dict[str, set[str]] = {}
    for index, c in enumerate(_list(data.get("connections"))):
        if not isinstance(c, dict):
            continue
        cid = _line(c.get("id")) or f"connections[{index}]"
        src, dst = _text(c.get("from")), _text(c.get("to"))
        bad = False
        for end_name, ref in (("from", src), ("to", dst)):
            if ref in groups:
                continue
            if ref not in by_id:
                shown = _line(ref) or "(空)"
                missing.append(f"線 {cid} の {end_name} が無いノード（{shown}）を指している")
                bad = True
        if not bad and src in by_id:
            # グループへ出る線でも、出る側の出口は使っている
            ports.setdefault(src, set()).add(_text(c.get("fromPort")) or "output")
        if bad or src in groups or dst in groups:
            continue
        edges.setdefault(src, []).append(dst)
        if kinds.get(dst) == "start":
            out.append(f"線 {cid} が start（{_named(by_id[dst])}）に入っている")
        if kinds.get(src) == "end":
            out.append(f"線 {cid} が end（{_named(by_id[src])}）から出ている")
    out.extend(missing[:ITEM_LIMIT])
    if len(missing) > ITEM_LIMIT:
        out.append(f"無いノードを指す線は…ほか {len(missing) - ITEM_LIMIT} 件")
    starts = [nid for nid in by_id if kinds[nid] == "start"]
    if not starts:
        out.append("start が無い（どこから始めるかが決まらない）")
    if not any(kind == "end" for kind in kinds.values()):
        out.append("end が無い（どこで終わるかが決まらない）")
    if starts:
        reached: set[str] = set()
        queue = deque(starts)
        while queue:
            current = queue.popleft()
            if current in reached:
                continue
            reached.add(current)
            queue.extend(edges.get(current, []))
        lost = [n for nid, n in by_id.items() if nid not in reached]
        if lost:
            out.append(f"start から届かないノードがある: {_names(lost)}")
    for nid, node in by_id.items():
        key = BRANCH_KEYS.get(kinds[nid])
        if key is None:
            continue
        taken = ports.get(nid, set())
        if kinds[nid] == ASK and _dict(node.get("data")).get("multiSelect") is True:
            # 複数選択の問いは出口を分けない（`output` の 1 本。画面の `portsOf` と同じ）
            if "output" not in taken:
                out.append(f"問い {_named(node)} の出口に線が無い: output（複数選択）")
            continue
        items = [i for i in _list(_dict(node.get("data")).get(key)) if isinstance(i, dict)]
        empty = [
            _line(item.get("label")) or f"{index + 1} 番目"
            for index, item in enumerate(items)
            if not _port_taken(node, index, item, taken)
        ]
        if empty:
            what = "問い" if kinds[nid] == ASK else "分岐"
            shown = ", ".join(_capped(empty[:ITEM_LIMIT], len(empty)))
            out.append(f"{what} {_named(node)} の出口に線が無い: {shown}")
    return out


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
    return clean(value).strip() if isinstance(value, str) else ""


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


def _flow_nodes(data) -> list[dict]:
    """名前を確かめるノード。最上位とサブフローの中の両方。"""
    data = _dict(data)
    nodes = [n for n in _list(data.get("nodes")) if isinstance(n, dict)]
    for sub in _list(data.get("subAgentFlows")):
        nodes.extend(n for n in _list(_dict(sub).get("nodes")) if isinstance(n, dict))
    return nodes


def name_problems(data, cat: dict[str, list[dict]]) -> list[str]:
    """`subAgent` の種類（`builtInType`）と `skill` の名前（`name`）が候補に無いもの（1 件 1 行）。

    書き誤りを見つけるため。空の欄は言わない（書きかけ）。スキルの `:` を含む名前
    （プラグインのスキル）は、ディレクトリの中から確かめられないので言わない。大文字小文字だけが
    違えば、正しい表記をつける。例外は外に出さない。
    """
    try:
        return _name_problems(data, cat)
    except Exception:  # noqa: BLE001  壊れたデータで lint を落とさない
        return []


def _name_problems(data, cat: dict[str, list[dict]]) -> list[str]:
    agents = [i["name"] for i in cat.get("agents", [])]
    skills = [i["name"] for i in cat.get("skills", [])]
    out: list[str] = []
    for node in _flow_nodes(data):
        kind = _text(node.get("type"))
        info = _dict(node.get("data"))
        if kind == "subAgent":
            what, value, known = "サブエージェントの種類", _text(info.get("builtInType")), agents
        elif kind == "skill":
            what, value, known = "スキル", _text(info.get("name")), skills
            if ":" in value:
                continue
        else:
            continue
        value = value.strip()
        if not value or value in known:
            continue
        near = [k for k in known if k.casefold() == value.casefold()]
        hint = f"。大文字小文字が違う（{_line(near[0])}）" if near else ""
        out.append(
            f"ノード {_named(node)} の{what} {_line(value)} が候補に無い"
            "（組み込みと .claude/ の下に無い。書き誤りかもしれない。"
            f"ユーザ・プラグインのものなら気にしなくてよい）{hint}"
        )
    return out


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


def digest_record_path(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> str:
    """着手のときに保存したハッシュの記録の置き場。

    フローと同じツリーの `phases/<親>/<子>.flow.json`。
    """
    # 形は `approval_marks.child_record_path` と同じ。flow は approval_marks より下の段なので
    # 読まず、形だけを同じにする（`test_flow_hardening` が突き合わせる）。
    approved = settings.approved_dir(conf, child.tree_root or root)
    return os.path.join(approved, PHASES_DIR, child.parent, f"{child.ticket}.{DIGEST_RECORD}.json")


def record_digest(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> tuple[str, str]:
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
    return target, clean(failed)


def _describe(mark: str) -> str:
    if mark == "absent":
        return "無い"
    if mark.startswith("sha256:"):
        return mark[: len("sha256:") + 12]
    return clean(mark.partition(":")[2]) or "読めない"


def changed_notice(conf: settings.Settings, root: str, child: ticket_mod.Ticket) -> str:
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
            f"{clean(child.ticket)} のフロー {clean(path)} が、着手のとき（{_describe(before)}）と"
            f"違う（いま {_describe(now)}）。担当のサブエージェントが読んだ手順と、ユーザが渡した"
            "手順が食い違っているかもしれない。誰が書き換えたかをユーザが確かめてください"
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
    """文字列か数だけを文にする。リストや辞書は中身を辿らない（深い入れ子で落ちない）。"""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        # 整数の値の小数（`1.0`）は整数の表記にする。ボード（JavaScript の `String`）と揃える。
        return str(int(value))
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _line(value) -> str:
    """1 行にまとめて切る。ccnavi の接頭辞は真似させない。"""
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
    """見た目で比べるための表記と、その 1 字ずつの元の位置。

    互換分解（NFKD。全角の `［` は `[`、`ⅽ` は `c`）し、結合文字・書式の制御・制御文字・
    空白を落とし、似た形の字（`_CONFUSABLE`）をラテン文字に置き換え、大文字小文字をそろえる。
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
      結合文字を挟んだもの・キリル文字の `с` で書いたものも同じ
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
    """文に ccnavi の接頭辞か案内の区切りに見える箇所が残っているか。テストと確かめ用。"""
    skeleton, _ = _skeleton(text)
    if any(_skeleton(p)[0] in skeleton for p in _FENCE_PHRASES):
        return True
    return f"[{_BADGE_WORD}" in skeleton


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _capped(parts: list[str], total: int) -> list[str]:
    """リストを ITEM_LIMIT で切り、残りの数をつける。"""
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
        parts = [_line(data.get("description"))]
        if _line(data.get("prompt")):
            parts.append(f"プロンプト: {_line(data.get('prompt'))}")
        if _line(data.get("builtInType")):
            parts.append(f"種類: {_line(data.get('builtInType'))}")
        return " / ".join(p for p in parts if p)
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
    if kind in ("branchSession", "start", "end"):
        return _line(data.get("label"))
    return ""


def _branch_index(port: str) -> int | None:
    """`branch-<番号>` の番号。番号は ASCII の数字だけ。違う表記なら None。"""
    head, _, digits = port.rpartition("-")
    if head != "branch" or not digits or not (digits.isascii() and digits.isdigit()):
        return None
    if len(digits) > 6:
        return None
    return int(digits)


def _port_key(connection: dict) -> tuple:
    """出口の並べ順。`branch-<番号>` は番号の順、それ以外は文字列の順で後ろ。"""
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
        return ["（フローを並べられない。ファイルを直接読んで判断してください）"], set()


def _render(data, limit: int, text_limit: int) -> tuple[list[str], set[str]]:
    data = _dict(data)
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for n in _list(data.get("nodes")):
        # グループは図の上の囲みで手順ではない。並べない（線が繋がっていても辿らない）
        if isinstance(n, dict) and _text(n.get("type")) == GROUP:
            continue
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
        # 組み立てた行でも見る。種類の名前が `ccnavi…` だと、こちらの `[<種類>]` が接頭辞になる。
        text = _neutral(text)
        if used + len(text) > text_limit and lines:
            break
        lines.append(text)
        used += len(text)
        shown += 1
    if len(order) > shown:
        lines.append(f"…ほか {len(order) - shown} 件。続きはファイルを読んでください")
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
        " ccnavi が知らせる。"
        if child.in_progress
        else "着手すると、終わるまで書き換えられなくなる（ロック）。"
    )
    lines = [
        f"    フロー: {clean(path)}（ユーザがボードで書いた {clean(child.ticket)} の手順。"
        "承認済みの領域にあり、ユーザが持つもの。エージェントは編集しない）。作業の前にこのファイルを"
        "読み、その順に進めてください。文脈が要約されて見失ったら、"
        "このパスを読み直してください。" + lock
    ]
    data, why = load(path, base)
    if data is None:
        lines.append(f"    フローを読めない: {why}。ユーザが確かめてください")
        return lines
    room = max(0, min(budget, CHILD_TEXT_LIMIT))
    if room == 0:
        lines.append(
            "    手順は SubagentStart の文の上限に達したので並べない。ファイルを読んでください"
        )
        return lines
    steps, kinds = render(data, text_limit=room)
    lines.append(FENCE_OPEN)
    lines.extend(f"      {s}" for s in steps)
    lines.append(FENCE_CLOSE)
    worktree = clean(tree.worktree_path(root, child.ticket))
    ticket, scope = clean(child.ticket), clean(scope) or "(空)"
    if ASK in kinds:
        lines.append(
            f"    {ASK} のノード: サブエージェントはユーザに聞けない"
            "（AskUserQuestion は渡されない）。そのノードで手を止め、問いと選択肢を添えて"
            "メインに返してください。メインがユーザに聞き、答えを持って同じサブエージェントを再開させる。"
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
