"""レビューのホスト側の形。sh が取ってきた結果（`--result`）と、投稿の目印と、リモートの種類。

sh との契約。値を変えるなら設計書と sh を同時に直す。

ここはネットワークに出ない。`.ccnavi/scripts/ccnavi-review.sh` がホストから取ってきた
JSON を読む形（`Result`）と、機構自身の投稿に付ける目印、origin の URL から
GitHub か GitLab かを見分ける読み方だけを置く。review から分けた。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from ..infra import fsio

# 投稿に付けるマーカー。機構自身の投稿を、確認のときに除くため。
MARKER_REQUEST = "<!-- ccnavi:request "
MARKER_COMMENT = "<!-- ccnavi:comment -->"
MARKER_DECIDE = "<!-- ccnavi:decide -->"
# 受け入れた指摘のスレッドに残す返信の目印。返信を残したあと、sh がそのスレッドを解決する。
MARKER_ACCEPT = "<!-- ccnavi:accept -->"
MARKER_PREFIX = "<!-- ccnavi:"
# issue の本文に入れる隠しコメントの印。`op` は指摘ごとに決まる操作ID。同じ印の issue を検索して、
# 作り直しで重複しないようにする。
MARKER_ISSUE = "<!-- ccnavi:issue op={op} -->"

GITHUB_TOKEN = "GITHUB_TOKEN"
GITLAB_TOKEN = "GITLAB_TOKEN"


MARKER_READY = "<!-- ccnavi:ready -->"
MARKER_CLOSE_EARLY = "<!-- ccnavi:close-early -->"


@dataclass
class Thread:
    id: str = ""
    resolved: bool = False
    url: str = ""
    path: str = ""
    line: int = 0
    body: str = ""
    created_at: str = ""
    # 最初のコメントを書いたアカウント。GitLab から取得した結果だけが持つ
    # （ccnavi の投稿を見分けるため）
    author: str = ""
    # 最後のコメントの id・時刻・作成者。再提起（受け入れたあとに付いたコメント）を
    # 見分けるために取る。
    # 返信をすべて取るわけではない（全文の範囲は最初のコメント）。取れなければ空。
    last_comment_id: str = ""
    last_comment_at: str = ""
    last_comment_author: str = ""
    # スレッドの note が全部 ccnavi の目印で始まるか（ccnavi の投稿だけのスレッドか）。GitLab から
    # 取得した結果だけが持つ。JSON の `true` / `false` だけを受け、それ以外（`"true"`・`1` など）と
    # 欄が無いときは None（目印と書き手で見る従来の判定に戻る）。
    # ユーザが ccnavi の依頼コメントに返信したスレッドは False（代表の note は返信）。
    own: bool | None = None


@dataclass
class Review:
    state: str = ""
    url: str = ""
    submitted_at: str = ""
    # author はレビュアーの識別。同じユーザの最新のレビューだけを数えるために要る。
    # GitHub は過去の全レビューを返すので、後で approve しても古い変更要求が残る。
    author: str = ""


# 変更要求の状態。GitHub の CHANGES_REQUESTED と、GitLab の requested_changes をこの値にまとめる。
CHANGES_REQUESTED = "CHANGES_REQUESTED"
DISMISSED = "DISMISSED"
APPROVED = "APPROVED"
# ユーザごとの最新を選ぶ対象。コメントだけのレビュー（COMMENTED）と、書きかけ（PENDING）は判断を
# 変えないので飛ばす。飛ばさないと、変更要求の後に同じユーザがコメントだけのレビューを出すと
# 変更要求が消える。
# 変更要求は同じユーザの Approve か dismiss まで残る
_DECIDING = (APPROVED, CHANGES_REQUESTED, DISMISSED)


def effective(reviews: list[Review]) -> list[Review]:
    """レビュアーごとに、判断を示した最新の 1 件。取り下げられたものは無い扱い。

    判断を示したレビューは Approve・変更要求・取り下げ（dismiss）だけ。コメントだけのレビューや
    書きかけは数えない（前は数えたので、変更要求の後に同じユーザがコメントするだけで変更要求が消えた）。
    """
    latest: dict[str, Review] = {}
    for r in reviews:
        if r.state.upper() not in _DECIDING:
            continue
        key = r.author or r.url or id(r)
        current = latest.get(key)
        if current is None or _epoch(r.submitted_at) >= _epoch(current.submitted_at):
            latest[key] = r
    return [r for r in latest.values() if r.state.upper() != DISMISSED]


# 変更要求の案内（ホストごと）。判定（`effective`・`CHANGES_REQUESTED`）はホストを問わず同じで、
# 案内だけ出し分ける。ホストは取得した結果の `host`
# （sh の分類。Chrome 拡張はユーザが明示した種類を含む）。
CHANGES_GITLAB = (
    "変更を依頼（Request changes）が出ています。解除はレビュアーの承認（Approve）で行います。"
    "作成者によるレビューの再依頼（Re-request review）で状態が戻るかは未検証です。"
    "再依頼した場合も、ccnavi で状態を取り直して確かめます"
)
CHANGES_GITHUB = (
    "変更要求（Changes requested）が出ています。"
    "解除はレビュアーの Approve か、レビューの dismiss で行います"
)


def changes_requested_note(host: str) -> str:
    """変更要求が出ているときの案内の 1 文。解除のしかたはホストで違う（GitLab は Approve だけ）。

    GitLab 以外（GitHub・見本の `fixture` など）は GitHub の案内。
    """
    return CHANGES_GITLAB if host == "gitlab" else CHANGES_GITHUB


@dataclass
class MergeRequest:
    number: int = 0
    url: str = ""
    # Draft のままか。sh が取得した結果だけが持つ（分からなければ None）。`ready` の打ち直しが、
    # Draft を外し損ねたかを見分けるのに使う。
    draft: bool | None = None


@dataclass
class Result:
    """sh がリモートから取得した結果。`--result <json>` で渡る。

    形は 1 つ。`host` と `mr{number,url}` は常に要る。`confirm` と `--reviewed` は
    `threads[]` と `reviews[]`、`requested` は投稿の `url` と `created_at` を見る。
    """

    host: str = ""
    mr: MergeRequest | None = None
    threads: list[Thread] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    url: str = ""
    created_at: str = ""
    # 投稿したアカウント（`requested` の結果だけ。ccnavi の投稿を見分けるため依頼の記録に残す）
    author: str = ""
    error: str = ""

    @classmethod
    def load(cls, path: str) -> Result:
        data, failed = fsio.read_json(path)
        if isinstance(failed, OSError):
            return cls(error=f"結果を読めない ({failed})")
        if failed is not None:
            return cls(error=f"結果が JSON として読めない ({failed})")
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: object) -> Result:
        """読んだ JSON の値から組む。Chrome は sh と同じ形の結果を組んで渡す。

        処理フロー:
        1. 最上位がマップでなければ、error を入れて返す。`error` の欄が在れば、その文を入れて返す。
        2. `mr` がマップで `number` が在れば MergeRequest にする。`draft` は bool のときだけ使い、
        それ以外は None にする。
        3. `threads`・`reviews` の項目を、マップのものだけ読んで Thread・Review にする。
        文字列・整数・真偽は型に合わせて整える。
        4. host・url・created_at・author を整えて返す。
        """
        if not isinstance(data, dict):
            return cls(error="結果の最上位がマップではない")
        if data.get("error"):
            return cls(error=str(data["error"]))
        mr = data.get("mr")
        found = None
        if isinstance(mr, dict) and mr.get("number"):
            draft = mr.get("draft")
            found = MergeRequest(
                int(mr.get("number") or 0),
                str(mr.get("url") or ""),
                draft if isinstance(draft, bool) else None,
            )
        return cls(
            host=str(data.get("host") or ""),
            mr=found,
            threads=[
                Thread(
                    id=str(t.get("id") or ""),
                    resolved=bool(t.get("resolved")),
                    url=str(t.get("url") or ""),
                    path=str(t.get("path") or ""),
                    line=int(t.get("line") or 0),
                    body=str(t.get("body") or ""),
                    created_at=str(t.get("created_at") or ""),
                    author=str(t.get("author") or ""),
                    last_comment_id=str(t.get("last_id") or ""),
                    last_comment_at=str(t.get("last_at") or ""),
                    last_comment_author=str(t.get("last_author") or ""),
                    own=t["own"] if isinstance(t.get("own"), bool) else None,
                )
                for t in data.get("threads") or []
                if isinstance(t, dict)
            ],
            reviews=[
                Review(
                    str(r.get("state") or ""),
                    str(r.get("url") or ""),
                    str(r.get("submitted_at") or ""),
                    str(r.get("author") or ""),
                )
                for r in data.get("reviews") or []
                if isinstance(r, dict)
            ],
            url=str(data.get("url") or ""),
            created_at=str(data.get("created_at") or ""),
            author=str(data.get("author") or ""),
        )


# ---- 受け入れの計画。exe が書き、sh が読んで、返信と解決をホストに投稿する。
#
# 実行ファイルはネットワークに出ないので、受け入れたスレッドへの返信と解決は sh がする。
# 置く前（マーカーを置く前）に済ませ、失敗したら受け入れは未完了として止める。
# 形は `{"version": 1, "digest": <見せた指摘のダイジェスト。close-early は空>,
#        "threads": [{"id": <スレッドの id>, "key": <キー>, "reply": <返信の本文>}]}`。
# `id` は GitHub ならレビューのスレッドの node id、GitLab なら discussion の id。
# sh は `id` と `reply` だけを読む（`digest` と `key` は実行ファイルが置く側で読む）。
ACCEPT_PLAN_VERSION = 1
# スレッドの id に使える字。GitHub の node id（`PRRT_kw…`）と GitLab の discussion の id（16 進）の
# どちらも収まる。sh も同じ字でしか API の経路や GraphQL の変数に使わない。
ACCEPT_ID = re.compile(r"[A-Za-z0-9_=-]+")


def accept_reply(why: str, issue: bool = False, issue_url: str = "") -> str:
    """受け入れたスレッドに残す返信の本文。`why` は受け入れる場面（1 行）。

    `issue_url` を渡すと、issue の下書きを置く旨の代わりに、その issue の URL を書く
    （issue を先に作る経路。URL は呼び手が作ったあとに埋めるので、枠の文字列を渡してもよい）。
    """
    lines = [MARKER_ACCEPT, f"ccnavi: {why}ので、この指摘は対応せず、受け入れて進める。"]
    if issue and issue_url:
        lines.append(f"別の issue に回した: {issue_url}。このスレッドは解決済みにする。")
    elif issue:
        lines.append("別の issue に回す（issue の下書きを置く）。このスレッドは解決済みにする。")
    else:
        lines.append("このスレッドは解決済みにする。")
    return "\n".join(lines) + "\n"


def accept_plan_problem(threads: list[Thread]) -> str:
    """返信できない指摘があれば、その説明。

    スレッドの id が無い（または API の経路に使えない字を含む）と、ホストに返信も解決もできない。
    """
    for t in threads:
        if not t.id or not ACCEPT_ID.fullmatch(t.id):
            return f"スレッドの id が無いか読めず、返信と解決ができない（{t.url or '不明'}）"
    return ""


def write_accept_plan(
    path: str, items: list[tuple[Thread, str]], digest: str = "", op: str = ""
) -> str:
    """計画を書く。`items` は (スレッド, 返信の本文)。書けなければ理由を返す。

    `op` は操作記録の操作ID。sh が送信の段階を記録するときと、
    置く呼び出しが同じ記録を読むときのキー。
    """
    body = {
        "version": ACCEPT_PLAN_VERSION,
        "digest": digest,
        "op": op,
        "threads": [{"id": t.id, "key": t.url or t.id, "reply": reply} for t, reply in items],
    }
    return fsio.write_text(path, json.dumps(body, ensure_ascii=False) + "\n", newline="\n")


@dataclass
class AcceptPlan:
    """書いた計画を読み戻したもの。"""

    digest: str = ""
    ids: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    op: str = ""


def read_accept_plan(path: str) -> AcceptPlan | None:
    """書いた計画。読めなければ None（計画の段を通っていない）。"""
    data = fsio.read_dict(path)
    if not data or data.get("version") != ACCEPT_PLAN_VERSION:
        return None
    rows = [t for t in data.get("threads") or [] if isinstance(t, dict)]
    return AcceptPlan(
        digest=str(data.get("digest") or ""),
        op=str(data.get("op") or ""),
        ids=[str(t.get("id") or "") for t in rows],
        keys=[str(t.get("key") or "") for t in rows],
    )


# ---- リモートに要る道具の有無。exe は使わないが、--lint が言う。

# ホストにはポートが付く（`localhost:8929`）。ポートを破棄すると、手元や社内に立てた
# GitLab を GitHub と見分ける判定まで誤る。ssh の `git@host:group/proj` の
# `:` はパスの区切りなので、数字だけのときにポートと見なす。
# `https://oauth2:token@host/` のユーザ情報は読み飛ばす。sh と同じく、authority の
# 最後の `@` までをユーザ情報と見る（git がそう切る。トークンに `@` が入る形がある）。
# IPv6 は `[::1]` の形。sh が読めない表記（`ssh://user@host/`）はここでも読めない扱いにして、
# --lint と sh の言うことを揃える。scheme の大文字小文字は区別しない（`HTTPS://` も読む）。
_REMOTE = re.compile(
    r"^(?:(?i:https?)://|(?i:ssh)://git@|git@)(?:[^/]*@)?"
    r"(?P<host>\[[^\]/]+\]|[^/:@]+)(?::(?P<port>[0-9]+))?[/:]+(?P<path>.+?)(?:\.git)?/?$"
)

GHE_DOMAIN = "ghe.com"


@dataclass(frozen=True)
class HostClass:
    """origin の URL から決めた、ホストの分類と接続先。`kind` が空なら読めない表記。

    分類に使うホスト（`host`）は、origin から取り出したホストを小文字にしてポートを除いたもの。
    照合は `.` の区切りで行う（`evilgithub.com` や `github.com.evil` は GitHub にならない）。

    - GitHub: `github.com`、`ghe.com` とそのサブドメイン（`*.ghe.com`）。REST は github.com が
      `https://api.github.com`、GHE.com が `https://api.<ホスト>`。GraphQL はその根の `/graphql`。
      GHE.com の origin にポートが付いていると API の URL を組めないので `problem` を言う。
      GHE.com の API の URL の規則は、実物では未検証
    - GitLab: 上のどれにもヒットしないもの全部（`gitlab.com`・`*.aslead.cloud`・セルフホスト・
      未知のホスト）。接続先は origin の scheme・ホスト・ポートをそのまま保つ

    sh の `ccnavi_host_parse`（ccnavi-common-host.sh）と Chrome 拡張の `hostclass.ts` が同じ規則で、
    `tests/fixtures/host-kinds.json` の同じ表で突き合わせる。
    """

    kind: str = ""
    host: str = ""
    api: str = ""
    graphql: str = ""
    path: str = ""
    problem: str = ""


def is_ghe_host(host: str) -> bool:
    """小文字のホスト名が `ghe.com` か、そのサブドメインか（`.` の区切りで照合する）。"""
    return host == GHE_DOMAIN or (
        host.endswith("." + GHE_DOMAIN) and len(host) > len(GHE_DOMAIN) + 1
    )


def classify(url: str) -> HostClass:
    """origin の URL を GitHub か GitLab かに分け、接続先を決める。読めない表記なら `kind` が空。"""
    m = _REMOTE.match(url)
    if m is None:
        return HostClass()
    raw = m.group("host")
    port = m.group("port") or ""
    host = raw.lower()
    path = m.group("path")
    if host == "github.com":
        return HostClass(
            "github", host, "https://api.github.com", "https://api.github.com/graphql", path
        )
    if is_ghe_host(host):
        if port:
            return HostClass(
                "github",
                host,
                path=path,
                problem=(
                    f"GHE.com のホスト {host} にポート :{port} が付いているので、"
                    f"API の URL（https://api.{host}）を組めない"
                ),
            )
        api = f"https://api.{host}"
        return HostClass("github", host, api, api + "/graphql", path)
    scheme = "http" if url[:7].lower() == "http://" else "https"
    api = f"{scheme}://{raw}{':' + port if port else ''}/api/v4"
    return HostClass("gitlab", host, api, "", path)


def remote_kind(url: str) -> str:
    """origin の URL から、GitHub か GitLab か。読めない表記なら空。"""
    return classify(url).kind


def transport_problem(url: str) -> str:
    """sh がリモートを読み書きできる形になっているか。なっていなければ、その説明。

    sh と同じ順で探す。`gh` / `glab` があればそれ、無ければ `curl` とトークン。
    どちらも無ければ、レビューの依頼と確認は動かない。
    """
    found = classify(url)
    kind = found.kind
    if not kind:
        # URL に埋めた資格情報（`user:token@`）は出力に残さない
        shown = re.sub(r"//[^/@]*@", "//<伏せた>@", url)
        return f"origin ({shown}) が GitHub でも GitLab でもない"
    if found.problem:
        return found.problem
    if shutil.which("jq") is None:
        return "jqが見つかりません。結果の JSON を作成できません"
    cli = "gh" if kind == "github" else "glab"
    if shutil.which(cli) is not None:
        return ""
    token = GITHUB_TOKEN if kind == "github" else GITLAB_TOKEN
    if shutil.which("curl") is None:
        return (
            f"{cli} も curl もありません。MCP などでユーザがリモートを読む形にするか、"
            "どちらかを入れてください"
        )
    if not os.environ.get(token, ""):
        return f"{cli} が無く、curl に付ける {token} もありません。"
    return ""


def thread_key(t: Thread) -> str:
    """選択をスレッドに対応づけるキー。URL を先に使い、無ければスレッドの id。"""
    return t.url or t.id


def thread_version(t: Thread) -> str:
    """指摘の版。SHA-256(キー + 改行 + 正規化した最初のコメントの本文) の先頭 32 桁。

    正規化するのは改行コード（CRLF・CR を LF に）と Unicode の NFC だけ。本文が変わるか、キーが
    変わると版が変わる。見せた指摘と、適用の直前に取り直した指摘、受け入れ台帳の項目が同じかを、
    この版で照合する。
    """
    body = unicodedata.normalize("NFC", t.body.replace("\r\n", "\n").replace("\r", "\n"))
    return hashlib.sha256(f"{thread_key(t)}\n{body}".encode()).hexdigest()[:32]


def _accepted_by_ledger(t: Thread, ledger: list[dict]) -> bool:
    """受け入れ台帳（`accepted.json`）が受け入れ済みとする指摘か。

    キーと版が台帳の項目と同じで、受け入れたときの最後のコメントから変わっていないとき（受け入れた
    後に誰かがコメントを付けた指摘は、再提起として数える）。キーか最後のコメントの id が取れていない
    指摘は受け入れ済みにしない（隠さない側に倒す）。
    """
    key, version = thread_key(t), thread_version(t)
    if not key or not t.last_comment_id:
        # キーか最後のコメントが分からない指摘は、再提起を見分けられないので受け入れ済みにしない
        return False
    for entry in ledger:
        if entry.get("key") != key or entry.get("version") != version:
            continue
        if str(entry.get("last_comment_id") or "") == t.last_comment_id:
            return True
    return False


def _unresolved(
    threads: list[Thread],
    host: str = "",
    poster: str = "",
    ledger: list[dict] | None = None,
) -> list[Thread]:
    """まだ解決されていない指摘。

    付いた時刻では絞らない。依頼より後のものだけを数えると、
    指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで
    前回の指摘が数から消える。ユーザが解決も受け入れもしていないのに通る形になる。

    数えないのは機構自身が置いた投稿だけ。「受け入れて進める」と選んだ指摘は、
    返信を残してホスト側で解決済みにする（`MARKER_ACCEPT`）。
    解決済みなので数えず、受け入れたあとで誰かが開き直せば、未解決として数える。

    機構自身の投稿と見るのは、GitLab から取得した結果で、書いたのが依頼を投稿したアカウント
    （`poster`）で、本文が目印で始まるときだけ。スレッドの note が分かる結果（`own`）では、
    note が全部目印で始まるときだけ（ユーザが依頼コメントに返信したスレッドは指摘として数える）。
    GitHub の依頼は MR のコメントでスレッドにならないので、
    目印で始まるスレッドはユーザが書いたものとして数える。
    投稿者が分からなければ見分けずに数える（誰でも目印を書けるので、
    本文だけで除くと未解決を隠せる）。
    """
    return [
        t
        for t in threads
        if not t.resolved
        and not _ccnavi_post(t, host, poster)
        and not (ledger and _accepted_by_ledger(t, ledger))
    ]


def _ccnavi_post(t: Thread, host: str, poster: str) -> bool:
    """ccnavi 自身が投稿したスレッドか（`_unresolved`）。

    `own` が真偽値で取れていれば、それで決める（偽は書き手が同じでも数える）。取れていなければ
    （None）、代表の note の本文と書き手で見る。
    """
    mine = host == "gitlab" and bool(poster) and t.author == poster
    if t.own is not None:
        return mine and t.own
    return mine and t.body.startswith(MARKER_PREFIX)


def epoch(text: str) -> float:
    """ホストの時刻（ISO 8601）をエポック秒に。読めなければ 0。"""
    return _epoch(text)


def _epoch(text: str) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
