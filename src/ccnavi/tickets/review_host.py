"""レビューのホスト側の形。sh が取ってきた結果（`--result`）と、投稿の目印と、リモートの種類。

sh との契約。値を変えるなら ccnavi.md 9.10 と sh を同時に直す。

ここはネットワークに出ない。`.ccnavi/scripts/ccnavi-review.sh` がホストから取ってきた
JSON を読む形（`Result`）と、機構自身の投稿に付ける目印、origin の URL から
GitHub か GitLab かを見分ける読み方だけを置く。review から分けた。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime

from ..infra import fsio

# 投稿に付けるマーカー。機構自身の投稿を、確認のときに除くため。
MARKER_REQUEST = "<!-- ccnavi:request "
MARKER_COMMENT = "<!-- ccnavi:comment -->"
MARKER_DECIDE = "<!-- ccnavi:decide -->"
MARKER_PREFIX = "<!-- ccnavi:"

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


@dataclass
class MergeRequest:
    number: int = 0
    url: str = ""


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
        """読んだ JSON の値から組む。Chrome は sh と同じ形の結果を組んで渡す。"""
        if not isinstance(data, dict):
            return cls(error="結果の最上位が辞書ではない")
        if data.get("error"):
            return cls(error=str(data["error"]))
        mr = data.get("mr")
        found = None
        if isinstance(mr, dict) and mr.get("number"):
            found = MergeRequest(int(mr.get("number") or 0), str(mr.get("url") or ""))
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


# ---- リモートに要る道具の有無。exe は使わないが、--lint が言う。

# ホストにはポートが付く（`localhost:8929`）。ポートを捨てると、手元や社内に立てた
# GitLab を GitHub と見分ける判定まで誤る。ssh の `git@host:group/proj` の
# `:` はパスの区切りなので、数字だけのときにポートと見なす。
# `https://oauth2:token@host/` のユーザ情報は読み飛ばす。sh と同じく、authority の
# 最後の `@` までをユーザ情報と見る（git がそう切る。トークンに `@` が入る形がある）。
# IPv6 は `[::1]` の形。sh が読めない表記（`ssh://user@host/`、大文字の scheme）は
# ここでも読めない扱いにして、--lint と sh の言うことを揃える。
_REMOTE = re.compile(
    r"^(?:https?://|ssh://git@|git@)(?:[^/]*@)?"
    r"(?P<host>\[[^\]/]+\]|[^/:@]+)(?::\d+)?[/:]+(?P<path>.+?)(?:\.git)?/?$"
)


def remote_kind(url: str) -> str:
    """origin の URL から、GitHub か GitLab か。読めない表記なら空。"""
    m = _REMOTE.match(url)
    if m is None:
        return ""
    host = m.group("host")
    return "github" if host == "github.com" else "gitlab"


def transport_problem(url: str) -> str:
    """sh がリモートを読み書きできる形になっているか。なっていなければ、その説明。

    sh と同じ順で探す。`gh` / `glab` があればそれ、無ければ `curl` とトークン。
    どちらも無ければ、レビューの依頼と確認は動かない。
    """
    kind = remote_kind(url)
    if not kind:
        return f"origin ({url}) が GitHub でも GitLab でもない"
    if shutil.which("jq") is None:
        return "jq が無い。結果の JSON を組み立てられない"
    cli = "gh" if kind == "github" else "glab"
    if shutil.which(cli) is not None:
        return ""
    token = GITHUB_TOKEN if kind == "github" else GITLAB_TOKEN
    if shutil.which("curl") is None:
        return (
            f"{cli} も curl も無い。MCP などでユーザがリモートを読む形にするか、"
            "どちらかを入れてください"
        )
    if not os.environ.get(token, ""):
        return f"{cli} が無く、curl に付ける {token} も無い"
    return ""


def _unresolved(
    threads: list[Thread], accepted: set[str], host: str = "", poster: str = ""
) -> list[Thread]:
    """まだ解決されていない指摘。

    付いた時刻では絞らない。依頼より後のものだけを数えると、
    指摘が残ったまま「子をもう 1 本足して承認してもらい、依頼をやり直す」だけで
    前回の指摘が数から消える。ユーザが解決も受け入れもしていないのに通る形になる。

    数えないのは 2 つだけ。機構自身が置いた投稿と、ユーザが「未解決のまま進める」と
    受け入れたもの。受け入れた分を数え続けると、その親が二度と通らなくなる。

    機構自身の投稿と見るのは、GitLab から取得した結果で、本文が目印で始まり、
    書いたのが依頼を投稿したアカウント（`poster`）のときだけ。
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
        and t.url not in accepted
        and t.id not in accepted
    ]


def _ccnavi_post(t: Thread, host: str, poster: str) -> bool:
    """ccnavi 自身が投稿したスレッドか（`_unresolved`）。"""
    mine = host == "gitlab" and bool(poster) and t.author == poster
    return mine and t.body.startswith(MARKER_PREFIX)


def _epoch(text: str) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
