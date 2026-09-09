"""レビューの依頼と確認。`ccnavi review request|check|note` と `ccnavi --reviewed`。

## ゲートを開けるのはリモートの実物

ゲート（phase.py）を開けるのは、マージリクエストの未解決スレッドが 0 であること。
親が `.claude/scripts/ccnavi-review.sh` から呼ぶが、スクリプトはリモートの実物しか
見ないので、打たせてもゲートは緩まない。

## 依頼と確認の 2 段

`request` は前提を全部確かめてから依頼コメントを投稿し、依頼時の HEAD と時刻を
印に置く。`check` は依頼の後を見る。依頼の時刻より後のスレッドとレビューだけを数え、
機構自身の投稿は除く。「依頼した時点」が記録に無いと、見るべきスレッドの範囲を
決められない。

## 変更要求は人の端末でも通せない

未解決スレッドは人が `--reviewed --accept-unresolved` で受け入れて進めるが、
変更要求（changes requested）のレビューが立っている間は印を置かない。
「このままではマージしない」の意思表示を、別の人が端末から上書きする形は残さない。

## 時刻はエポック秒で比べる

ホストは UTC の `Z`、手元はオフセット付き。文字列のまま比べると依頼直後の指摘が
「依頼より前」に落ちる。参考にした運用が実測で踏んだ穴。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import TextIO

from . import approval, phase, settings, tree
from . import ticket as ticket_mod

# 投稿に付ける印。機構自身の投稿を、確認のときに除くため。
MARKER_REQUEST = "<!-- ccnavi:request "
MARKER_NOTE = "<!-- ccnavi:note -->"
MARKER_ACCEPT = "<!-- ccnavi:accept -->"
MARKER_PREFIX = "<!-- ccnavi:"

# テストと外部委任のための代役。JSON ファイルを指す。
FIXTURE_ENV = "CCNAVI_REVIEW_FIXTURE"

GITHUB_TOKEN = "GITHUB_TOKEN"
GITLAB_TOKEN = "GITLAB_TOKEN"

TIMEOUT_SECONDS = 15.0

# 出力に出す前に伏せる形。トークンらしい語。
_SECRET = re.compile(r"(ghp_|gho_|github_pat_|glpat-)[A-Za-z0-9_-]+|Bearer\s+\S+")


@dataclass
class Thread:
    id: str = ""
    resolved: bool = False
    url: str = ""
    path: str = ""
    line: int = 0
    body: str = ""
    created_at: str = ""


@dataclass
class Review:
    state: str = ""
    url: str = ""
    submitted_at: str = ""


@dataclass
class MergeRequest:
    number: int = 0
    url: str = ""
    host: str = ""


class Host:
    """リモートの読み書き。実装は 3 つ（GitHub / GitLab / 代役）。"""

    name = ""

    def find(self, branch: str) -> MergeRequest | None:
        raise NotImplementedError

    def threads(self, mr: MergeRequest) -> list[Thread]:
        raise NotImplementedError

    def reviews(self, mr: MergeRequest) -> list[Review]:
        raise NotImplementedError

    def comment(self, mr: MergeRequest, body: str) -> str:
        raise NotImplementedError


@dataclass
class Fixture(Host):
    """JSON ファイルを相手にする代役。投稿はファイルに書き戻す。"""

    path: str
    name: str = "fixture"
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def find(self, branch: str) -> MergeRequest | None:
        mr = self.data.get("mr")
        if not isinstance(mr, dict) or mr.get("branch") not in (None, branch):
            return None
        return MergeRequest(int(mr.get("number") or 1), str(mr.get("url") or ""), self.name)

    def threads(self, mr: MergeRequest) -> list[Thread]:
        return [
            Thread(
                id=str(t.get("id") or ""),
                resolved=bool(t.get("resolved")),
                url=str(t.get("url") or ""),
                path=str(t.get("path") or ""),
                line=int(t.get("line") or 0),
                body=str(t.get("body") or ""),
                created_at=str(t.get("created_at") or ""),
            )
            for t in self.data.get("threads", [])
            if isinstance(t, dict)
        ]

    def reviews(self, mr: MergeRequest) -> list[Review]:
        return [
            Review(
                str(r.get("state") or ""), str(r.get("url") or ""), str(r.get("submitted_at") or "")
            )
            for r in self.data.get("reviews", [])
            if isinstance(r, dict)
        ]

    def comment(self, mr: MergeRequest, body: str) -> str:
        posted = self.data.setdefault("comments", [])
        url = f"{mr.url}#note-{len(posted) + 1}"
        posted.append({"body": body, "url": url, "created_at": approval.now()})
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        return url


@dataclass
class GitHub(Host):
    owner: str
    repo: str
    token: str
    name: str = "github"
    api: str = "https://api.github.com"

    def find(self, branch: str) -> MergeRequest | None:
        query = urllib.parse.urlencode({"head": f"{self.owner}:{branch}", "state": "open"})
        data = self._get(f"/repos/{self.owner}/{self.repo}/pulls?{query}")
        if not isinstance(data, list) or not data:
            return None
        pr = data[0]
        return MergeRequest(int(pr["number"]), str(pr.get("html_url") or ""), self.name)

    def threads(self, mr: MergeRequest) -> list[Thread]:
        query = {
            "query": (
                "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
                "pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved "
                "comments(first:1){nodes{url path line body createdAt}}}}}}}"
            ),
            "variables": {"o": self.owner, "r": self.repo, "n": mr.number},
        }
        data = self._post("/graphql", query)
        nodes = []
        try:
            nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        except (KeyError, TypeError):
            return []
        threads = []
        for node in nodes:
            first = (node.get("comments") or {}).get("nodes") or [{}]
            c = first[0] if first else {}
            threads.append(
                Thread(
                    id=str(node.get("id") or ""),
                    resolved=bool(node.get("isResolved")),
                    url=str(c.get("url") or ""),
                    path=str(c.get("path") or ""),
                    line=int(c.get("line") or 0),
                    body=str(c.get("body") or ""),
                    created_at=str(c.get("createdAt") or ""),
                )
            )
        return threads

    def reviews(self, mr: MergeRequest) -> list[Review]:
        data = self._get(f"/repos/{self.owner}/{self.repo}/pulls/{mr.number}/reviews?per_page=100")
        if not isinstance(data, list):
            return []
        return [
            Review(
                str(r.get("state") or ""),
                str(r.get("html_url") or ""),
                str(r.get("submitted_at") or ""),
            )
            for r in data
            if isinstance(r, dict)
        ]

    def comment(self, mr: MergeRequest, body: str) -> str:
        data = self._post(
            f"/repos/{self.owner}/{self.repo}/issues/{mr.number}/comments", {"body": body}
        )
        return str(data.get("html_url") or "") if isinstance(data, dict) else ""

    def _get(self, path: str):
        return _http("GET", self.api + path, self._headers(), None)

    def _post(self, path: str, payload: dict):
        return _http("POST", self.api + path, self._headers(), payload)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}


@dataclass
class GitLab(Host):
    base: str
    project: str
    token: str
    name: str = "gitlab"

    def find(self, branch: str) -> MergeRequest | None:
        query = urllib.parse.urlencode({"source_branch": branch, "state": "opened"})
        data = self._get(f"/merge_requests?{query}")
        if not isinstance(data, list) or not data:
            return None
        mr = data[0]
        return MergeRequest(int(mr["iid"]), str(mr.get("web_url") or ""), self.name)

    def threads(self, mr: MergeRequest) -> list[Thread]:
        data = self._get(f"/merge_requests/{mr.number}/discussions?per_page=100")
        if not isinstance(data, list):
            return []
        threads = []
        for d in data:
            notes = d.get("notes") or []
            if not notes or not notes[0].get("resolvable"):
                continue
            n = notes[0]
            pos = n.get("position") or {}
            threads.append(
                Thread(
                    id=str(d.get("id") or ""),
                    resolved=all(bool(x.get("resolved")) for x in notes if x.get("resolvable")),
                    url=f"{mr.url}#note_{n.get('id')}",
                    path=str(pos.get("new_path") or ""),
                    line=int(pos.get("new_line") or 0),
                    body=str(n.get("body") or ""),
                    created_at=str(n.get("created_at") or ""),
                )
            )
        return threads

    def reviews(self, mr: MergeRequest) -> list[Review]:
        # GitLab に「変更要求」の状態は無い。
        return []

    def comment(self, mr: MergeRequest, body: str) -> str:
        data = self._post(f"/merge_requests/{mr.number}/notes", {"body": body})
        return f"{mr.url}#note_{data.get('id')}" if isinstance(data, dict) else ""

    def _get(self, path: str):
        return _http("GET", self._url(path), self._headers(), None)

    def _post(self, path: str, payload: dict):
        return _http("POST", self._url(path), self._headers(), payload)

    def _url(self, path: str) -> str:
        return f"{self.base}/api/v4/projects/{urllib.parse.quote(self.project, safe='')}{path}"

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token}


def host_for(stderr: TextIO, tree_root: str) -> Host | None:
    """この作業ツリーのリモートに合う相手。分からなければ None。"""
    fixture = os.environ.get(FIXTURE_ENV, "")
    if fixture:
        return Fixture(fixture)
    rc, url = _git(tree_root, ["remote", "get-url", "origin"])
    if rc != 0 or not url.strip():
        stderr.write("ccnavi: origin のリモートが無い\n")
        return None
    return host_from_url(stderr, url.strip())


def host_from_url(stderr: TextIO, url: str) -> Host | None:
    m = re.match(r"^(?:https?://|git@|ssh://git@)([^/:]+)[/:]+(.+?)(?:\.git)?/?$", url)
    if m is None:
        stderr.write(f"ccnavi: リモートの綴りを読めない: {url}\n")
        return None
    hostname, path = m.group(1), m.group(2)
    if hostname == "github.com":
        token = os.environ.get(GITHUB_TOKEN, "")
        if not token:
            stderr.write(f"ccnavi: {GITHUB_TOKEN} が無い\n")
            return None
        owner, _, repo = path.partition("/")
        return GitHub(owner, repo, token)
    token = os.environ.get(GITLAB_TOKEN, "")
    if not token:
        stderr.write(f"ccnavi: {GITLAB_TOKEN} が無い（{hostname} を GitLab として扱う）\n")
        return None
    return GitLab(f"https://{hostname}", path, token)


def request(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    body_file: str,
) -> int:
    """前提を全部確かめてから依頼を投稿し、依頼の印を置く。"""
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return 1
    ph = _phase(root, conf, parent, phase_no)
    if ph is None:
        stderr.write(f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子が無い\n")
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    unmet: list[str] = []
    if not ph.ended:
        unmet.append("フェーズが終わっていない（todo/ か doing/ に子が残っている）")
    for child in ph.tickets:
        if ph.states.get(child.ticket) != ticket_mod.DONE:
            continue
        child_tree = tree.worktree_path(root, child.ticket)
        if not os.path.isdir(child_tree):
            continue
        rc, sha = _git(child_tree, ["rev-parse", "HEAD"])
        if rc != 0:
            continue
        rc, _ = _git(tree_root, ["merge-base", "--is-ancestor", sha.strip(), "HEAD"])
        if rc != 0:
            unmet.append(f"子 {child.ticket} のブランチが親に取り込まれていない")
    rc, status = _git(tree_root, ["status", "--porcelain"])
    if rc != 0 or status.strip():
        unmet.append("親の作業ツリーに未コミットの変更がある")
    branch = _branch(tree_root)
    if not branch:
        unmet.append("親ブランチの名前を読めない")
    else:
        rc, ahead = _git(tree_root, ["rev-list", f"origin/{branch}..HEAD"])
        if rc != 0 or ahead.strip():
            unmet.append("親ブランチの HEAD が push されていない")
    try:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()
    except OSError as exc:
        unmet.append(f"依頼文を読めない ({exc})")
        body = ""
    if not body.strip():
        unmet.append("依頼文が空")
    if approval.MARK_REQUESTED in ph.marks:
        unmet.append(f"フェーズ {phase_no} は依頼済み")
    host = host_for(stderr, tree_root)
    mr = host.find(branch) if host is not None and branch else None
    if mr is None:
        unmet.append("親ブランチに対応するマージリクエストが無い")
    if unmet:
        stderr.write(f"ccnavi: 依頼の前提が {len(unmet)} 件満たされていない\n")
        for line in unmet:
            stderr.write(f"  - {line}\n")
        return 1
    assert host is not None and mr is not None
    rc, head = _git(tree_root, ["rev-parse", "HEAD"])
    marker = f"{MARKER_REQUEST}{parent.ticket}:{phase_no} -->\n"
    try:
        url = host.comment(mr, marker + body)
    except RuntimeError as exc:
        stderr.write(f"ccnavi: 投稿できない: {_redact(str(exc))}\n")
        return 1
    failed = approval.write_mark(
        conf.approved,
        parent.ticket,
        phase_no,
        approval.MARK_REQUESTED,
        {"head": head.strip(), "mr": mr.number, "url": url, "host": host.name},
    )
    if failed:
        stderr.write(f"ccnavi: 印を置けない: {failed}\n")
        return 1
    stdout.write(f"OK: レビューを依頼した（{mr.url}）。ターンを終えて利用者を待つこと\n")
    return 0


def check(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, cwd: str, phase_no: int
) -> int:
    """依頼の後を見る。通れば印を置いてゲートが開く。"""
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return 1
    ph = _phase(root, conf, parent, phase_no)
    if ph is None:
        stderr.write(f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子が無い\n")
        return 1
    requested = ph.marks.get(approval.MARK_REQUESTED)
    if requested is None:
        stderr.write("ccnavi: 依頼の記録が無い。先に request すること\n")
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    host = host_for(stderr, tree_root)
    branch = _branch(tree_root)
    mr = host.find(branch) if host is not None else None
    if mr is None:
        stderr.write("ccnavi: マージリクエストを引けない\n")
        return 1
    since = _epoch(str(requested.get("at") or ""))
    try:
        threads = host.threads(mr)
        reviews = host.reviews(mr)
    except RuntimeError as exc:
        stderr.write(f"ccnavi: リモートを読めない: {_redact(str(exc))}\n")
        return 1
    changes = [
        r
        for r in reviews
        if r.state.upper() == "CHANGES_REQUESTED" and _after(r.submitted_at, since)
    ]
    if changes:
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。--accept-unresolved でも通せない。"
            "レビュアーの approve / dismiss を待つこと\n"
        )
        for r in changes:
            stderr.write(f"  - {r.url}\n")
        return 1
    unresolved = [
        t
        for t in threads
        if not t.resolved and not t.body.startswith(MARKER_PREFIX) and _after(t.created_at, since)
    ]
    if unresolved:
        stderr.write(f"ccnavi: 未解決のスレッドが {len(unresolved)} 件残っている\n")
        for t in unresolved:
            stderr.write(f"  - {t.url} {t.path}:{t.line} {_first_line(t.body)}\n")
        stderr.write(
            "解決してもらって再実行するか、利用者が端末で "
            f"'ccnavi --reviewed {phase_no} --accept-unresolved' を打つ\n"
        )
        return 1
    failed = approval.write_mark(
        conf.approved,
        parent.ticket,
        phase_no,
        approval.MARK_REVIEWED,
        {"mr": mr.number, "accepted": []},
    )
    if failed:
        stderr.write(f"ccnavi: 印を置けない: {failed}\n")
        return 1
    stdout.write(f"OK: フェーズ {phase_no} はレビュー済み。ゲートが開いた\n")
    return 0


def note(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, cwd: str, body_file: str
) -> int:
    """チャットで受けた承認・判断を MR の通常コメントに写す。状態は変えない。"""
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    try:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()
    except OSError as exc:
        stderr.write(f"ccnavi: 本文を読めない ({exc})\n")
        return 1
    if not body.strip():
        stderr.write("ccnavi: 本文が空\n")
        return 1
    host = host_for(stderr, tree_root)
    mr = host.find(_branch(tree_root)) if host is not None else None
    if mr is None:
        stderr.write("ccnavi: マージリクエストを引けない\n")
        return 1
    try:
        url = host.comment(mr, MARKER_NOTE + "\n" + body)
    except RuntimeError as exc:
        stderr.write(f"ccnavi: 投稿できない: {_redact(str(exc))}\n")
        return 1
    stdout.write(f"OK: 記録した（{url}）\n")
    return 0


def reviewed(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    accept_unresolved: bool,
) -> int:
    """人が端末で打つ。未解決を見せてから y/N。変更要求は通せない。"""
    parent = _parent(stderr, root, conf, cwd)
    if parent is None:
        return 1
    ph = _phase(root, conf, parent, phase_no)
    if ph is None:
        stderr.write(f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子が無い\n")
        return 1
    requested = ph.marks.get(approval.MARK_REQUESTED)
    if requested is None:
        stderr.write("ccnavi: 依頼の記録が無い。先に request すること\n")
        return 1
    if not accept_unresolved:
        stderr.write(
            "ccnavi: 未解決を受け入れるなら --accept-unresolved を付ける。"
            "受け入れないなら 'ccnavi review check' で足りる\n"
        )
        return 1
    tree_root = tree.worktree_path(root, parent.ticket)
    host = host_for(stderr, tree_root)
    mr = host.find(_branch(tree_root)) if host is not None else None
    if mr is None:
        stderr.write("ccnavi: マージリクエストを引けない\n")
        return 1
    since = _epoch(str(requested.get("at") or ""))
    threads = host.threads(mr)
    reviews = host.reviews(mr)
    if any(
        r.state.upper() == "CHANGES_REQUESTED" and _after(r.submitted_at, since) for r in reviews
    ):
        stderr.write(
            "ccnavi: 変更要求のレビューが立っている。端末からも通せない。"
            "レビュアーの approve / dismiss を待つこと\n"
        )
        return 1
    unresolved = [
        t
        for t in threads
        if not t.resolved and not t.body.startswith(MARKER_PREFIX) and _after(t.created_at, since)
    ]
    stdout.write(
        f"フェーズ {phase_no}（親 {parent.ticket}）の未解決スレッド: {len(unresolved)} 件\n"
    )
    for t in unresolved:
        stdout.write(f"  - {t.url} {t.path}:{t.line} {_first_line(t.body)}\n")
    stdout.write("これらを残したまま次のフェーズへ進めてよいなら y、やめるならそれ以外: ")
    stdout.flush()
    if approval._read(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 受け入れなかった\n")
        return 1
    accepted = [t.url or t.id for t in unresolved]
    if accepted:
        try:
            host.comment(
                mr,
                MARKER_ACCEPT
                + "\n未解決のまま次のフェーズへ進める:\n"
                + "\n".join(f"- {a}" for a in accepted),
            )
        except RuntimeError as exc:
            stderr.write(f"ccnavi: 受け入れのコメントを投稿できない: {_redact(str(exc))}\n")
    failed = approval.write_mark(
        conf.approved,
        parent.ticket,
        phase_no,
        approval.MARK_REVIEWED,
        {"mr": mr.number, "accepted": accepted},
    )
    if failed:
        stderr.write(f"ccnavi: 印を置けない: {failed}\n")
        return 1
    stdout.write(
        f"OK: フェーズ {phase_no} はレビュー済み（未解決 {len(accepted)} 件を受け入れた）\n"
    )
    return 0


def _parent(
    stderr: TextIO, root: str, conf: settings.Settings, cwd: str
) -> ticket_mod.Ticket | None:
    parent = phase.parent_for_cwd(root, conf, cwd)
    if parent is None:
        stderr.write("ccnavi: ここは親チケットの作業ツリーではない（cwd から親を引けない）\n")
    return parent


def _phase(
    root: str, conf: settings.Settings, parent: ticket_mod.Ticket, number: int
) -> phase.Phase | None:
    for ph in phase.phases_of(root, conf, parent.ticket):
        if ph.number == number:
            return ph
    return None


def _branch(tree_root: str) -> str:
    rc, out = _git(tree_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 else ""


def _git(cwd: str, args: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return done.returncode, done.stdout


def _epoch(text: str) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _after(text: str, since: float) -> bool:
    """依頼より後か。読めない時刻は「後」に倒す。落とすより余分に見せるほうが安い。"""
    stamp = _epoch(text)
    return since == 0.0 or stamp == 0.0 or stamp >= since


def _first_line(body: str) -> str:
    line = body.strip().splitlines()[0] if body.strip() else ""
    return line[:120]


def _redact(text: str) -> str:
    return _SECRET.sub("[伏せた]", text)


def _http(method: str, url: str, headers: dict[str, str], payload: dict | None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={**headers, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
            raw = res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"{exc} {url}") from exc
    try:
        return json.loads(raw) if raw else {}
    except ValueError as exc:
        raise RuntimeError(f"JSON として読めない {url}") from exc
