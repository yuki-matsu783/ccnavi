"""issue・MR に紐づくブランチを探す。

入口は 2 つある。

- `prompt_refs`・`prompt_context`: UserPromptSubmit で、ユーザの依頼文から issue・MR の指定
  （`#152`、`issue 152`、`.../issues/152`、`!5`、`MR 5`、`.../pull/5` など）を見つけ、着手の前に
  紐づくブランチを探してユーザに確かめる指示を `additionalContext` で足す。指示を足すだけで、
  作業は止めない（ユーザの決定）
- `report`: `ccnavi-branches.sh` が呼ぶサブコマンド
  （`ccnavi branches <issue|mr> <番号> --result <json>`）。手元の候補（名前に番号を含む
  ブランチ・ワークツリー・`issue:` を持つチケット）を集め、sh がホストから取ってきた結果
  （`--result`）と合わせて 1 候補 1 行か JSON で出す

実行ファイルはネットワークに出ない（docs/claude/exe-boundary.md）。ホスト（MR の元ブランチ、issue を
参照している MR）は sh が読み、ここはその JSON を読むだけ。ホストを見ていないときは、そう言う。
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import fsio, gitcmd, pathcase, settings, tree
from ..policy import ctxfile
from . import approval, ticket_ids, ticket_model
from . import ticket as ticket_mod

ISSUE = "issue"
MR = "mr"
KINDS = (ISSUE, MR)

# 1 回の依頼から拾う指定の上限。貼り付けた一覧から何十本も拾って指示を長くしない。
MAX_REFS = 5
# 依頼文のうち読む長さ。貼り付けた長いログで hook の判定の期限を使い切らないように、頭だけを読む。
MAX_PROMPT = 20000
# 番号。0 で始まるもの（`#000`・`#012345` のような色や連番）は issue・MR の番号として読まない。
_NUM = r"([1-9][0-9]{0,8})(?![0-9A-Za-z_])"
# 本文の形（語のあとの番号・`#N`・`!N`）で、番号の直後に許す文字。行末・空白・ASCII の句読点
# （`_` は除く）・閉じ括弧・`、` `。`・助詞。`手順#2を見直して` や `issue 2つ目` のように、
# 番号が別の語の一部のときは読まない。URL の番号は今のまま（`_NUM`）。
_AFTER = r"(?=\Z|\s|[.,;:!?)\]}\"'`]|[)」』）、。]|[をのがにはでともへや])"
_BODY_NUM = r"([1-9][0-9]{0,8})" + _AFTER
# 囲みのコードブロックとインラインコード。貼り付けたコードやログの `#123` を拾わない。
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# `#N`・`!N` の直前。行頭・空白・開き括弧・引用符・読点（`手順#2` のように語の直後は読まない）。
_BEFORE = r"(?:^|(?<=[\s(\[（「『、。,\"']))"
# URL。GitHub の `/issues/N`・`/pull/N`、GitLab の `/-/issues/N`・`/-/merge_requests/N`。
# 前の 2 段（`owner/repo`。GitLab の入れ子のグループはその最後の 2 段）を、
# どのリポジトリかの手がかりに残す。
_URL_ISSUE = re.compile(r"(?:([\w.-]{1,100}/[\w.-]{1,100})/)?(?:-/)?issues/" + _NUM)
_URL_MR = re.compile(
    r"(?:([\w.-]{1,100}/[\w.-]{1,100})/)?(?:-/)?(?:pull|pulls|merge_requests)/" + _NUM
)
# 語のあとの番号。`issue 152`・`issue #152`・`Issue: 152`・`issue-152`。
_WORD_ISSUE = re.compile(r"(?<![A-Za-z0-9_])issues?[ \t]*[:：#＃-]?[ \t]*" + _BODY_NUM, re.I)
_WORD_MR = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:MR|PR|merge[ \t]+request|pull[ \t]+request|マージリクエスト|プルリクエスト|プルリク)"
    r"[ \t]*[:：#＃!-]?[ \t]*" + _BODY_NUM,
    re.I,
)
# `#152`。前が `_BEFORE` のときだけ。`手順#2`・`C#`・`&#123;`・`##`・URL の断片は読まない。
# `#fff` は番号でないので外れ、`# 見出し` は `#` の後ろが空白なので外れる。
_HASH = re.compile(_BEFORE + r"[#＃]" + _BODY_NUM)
# `!5`（GitLab の MR）。前が `_BEFORE` のときだけ。`すごい!5` や `!!5` を拾わない。
_BANG = re.compile(_BEFORE + "!" + _BODY_NUM)
# `#` の前がこれなら CSS の色として読む（`color: #333333`）。
_CSS_BEFORE = re.compile(
    r"(?:color|background|border|fill|stroke|outline|shadow)[-a-z]*\s*:\s*$", re.I
)


@dataclass(frozen=True)
class Ref:
    """依頼文の中の issue・MR の指定 1 つ。

    repo は URL から読めたときの `owner/repo`（読めなければ空）。
    """

    kind: str
    number: int
    repo: str = ""

    def label(self) -> str:
        mark = f"#{self.number}" if self.kind == ISSUE else f"!{self.number}"
        name = "issue" if self.kind == ISSUE else "MR"
        return f"{name} {mark}" + (f"（{self.repo}）" if self.repo else "")


def prompt_refs(text: str) -> list[Ref]:
    """依頼文の中の issue・MR の指定。見つけた順に、同じ種類と番号は 1 つにまとめる。"""
    if not isinstance(text, str) or not text:
        return []
    body = _drop_noise(_INLINE_CODE.sub(" ", _FENCE.sub(" ", text[:MAX_PROMPT])))
    found: list[tuple[int, Ref]] = []
    for pattern, kind in ((_URL_ISSUE, ISSUE), (_URL_MR, MR)):
        for m in pattern.finditer(body):
            found.append((m.start(), Ref(kind, int(m.group(2)), m.group(1) or "")))
    # URL の中身を消してから語と記号を探す（`.../issues/152#issuecomment-1` を 2 度数えない）。
    rest = re.sub(r"https?://\S+", lambda m: " " * len(m.group(0)), body)
    for pattern, kind in ((_WORD_ISSUE, ISSUE), (_WORD_MR, MR)):
        for m in pattern.finditer(rest):
            found.append((m.start(), Ref(kind, int(m.group(1)))))
    # 語で読んだ分も消す（`PR #12` の `#12` を issue としても数えない）。
    rest = _blank(rest, _WORD_ISSUE, _WORD_MR)
    for m in _BANG.finditer(rest):
        found.append((m.start(), Ref(MR, int(m.group(1)))))
    for m in _HASH.finditer(rest):
        if _CSS_BEFORE.search(rest[max(0, m.start() - 40) : m.start()]):
            continue
        found.append((m.start(), Ref(ISSUE, int(m.group(1)))))
    refs: list[Ref] = []
    seen: dict[tuple[str, int], int] = {}
    for _, ref in sorted(found, key=lambda pair: pair[0]):
        key = (ref.kind, ref.number)
        if key in seen:
            # URL から読めたリポジトリの手がかりは残す。
            if ref.repo and not refs[seen[key]].repo:
                refs[seen[key]] = ref
            continue
        seen[key] = len(refs)
        refs.append(ref)
    # `#5` と `!5` のように同じ番号が両方に出るのは普通（同じ MR を言い換えた）なので、
    # そのまま並べる。
    return refs[:MAX_REFS]


# ユーザの指定ではない文。貼り付けた内容・system-reminder・サブエージェントの報告の枠は閉じタグまで
# （閉じが無ければ末尾まで）。`[ccnavi]` で始まる通知は、次の空行までの 1 段落。
# 他のセッションからの通知（"Another Claude session sent a message"）は、以降の末尾まで。
_NOISE_TAG = re.compile(
    r"<(pasted_content|system-reminder|agent-message)\b[^>]*>.*?(?:</\1\s*>|\Z)", re.S | re.I
)
_NOTICE = re.compile(r"^[ \t]*\[ccnavi\].*?(?:\n[ \t]*\n|\Z)", re.S | re.M)
_OTHER_SESSION = re.compile(r"Another Claude session sent a message.*\Z", re.S | re.I)


def _drop_noise(text: str) -> str:
    """ユーザの指定ではない部分を空白にする。通知の外にある指定は残る。"""
    return _blank(text, _NOISE_TAG, _OTHER_SESSION, _NOTICE)


def _blank(text: str, *patterns: re.Pattern) -> str:
    """当たった範囲を同じ長さの空白に置き換える（位置は変えない）。"""
    for pattern in patterns:
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


@dataclass
class Bound:
    """指定がすでに結び付いている先。"""

    branch: str
    tickets: list[str]
    worktrees: list[str]


def _repo_of(conf: settings.Settings, root: str, cwd: str) -> tuple[tree.Tree | None, str, str]:
    """cwd のリポジトリ（元のツリー）・プロジェクト名・読めない理由。"""
    repo_tree = tree.tree_of(root, cwd, conf.projects) if cwd else None
    if repo_tree is None:
        return None, "", f"{cwd} はワークスペースの外"
    project = repo_tree.project
    if project:
        repo = next((t for t in tree.projects(conf.projects) if t.project == project), None)
    else:
        repo = tree.main_tree(root)
    if repo is None:
        return None, project, f"プロジェクト {project} が見つからない"
    return repo, project, ""


def _bound(conf: settings.Settings, root: str, cwd: str, ref: Ref) -> Bound | None:
    """指定がすでに結び付いている先。手元の候補（`collect` と同じ集め方）だけを見る。

    照合は厳密にする。issue はチケットの `issue:` の一致、MR はチケットの `mr:` の一致
    （どちらも提案も承認済みも）。名前に番号を含むだけのブランチ
    （`fix-12-foo`）は結び付きとして数えない。読めないときは None（案内する側に倒す）。
    URL で別のリポジトリが示されているときも None（番号が同じだけの別物かもしれない）。
    """
    if ref.repo:
        return None
    try:
        # issue はチケットの `issue:`、MR はチケットの `mr:` の一致（どちらも提案も承認済みも対象）
        data, failed = collect(conf, root, cwd, ref.kind, ref.number, Host())
        if failed and ref.kind == ISSUE:
            return None
        for c in data.get("candidates", []):
            if "ticket" in c["sources"] and c["tickets"]:
                links = [f"{t['ticket']}({t['state']})" for t in c["tickets"]]
                return Bound(c["branch"], links, c["worktrees"])
        return None
    except OSError:
        return None
    return None


def _mark(ref: Ref) -> str:
    return f"{ref.kind}:{ref.number}"


def prompt_context(
    conf: settings.Settings,
    root: str,
    text: str,
    cwd: str = "",
    session: str = "",
    stderr: TextIO | None = None,
) -> str:
    """依頼文に issue・MR の指定があれば、`ccnavi-start.sh` で着手させる指示。無ければ空。

    チケット制御が disable なら出さない（指示の中身が親の識別子の承認に寄るため）。

    同じ番号の案内は同じセッションで 1 回だけ。案内した番号は、セッション単位の一時的な
    記録（`ctxfile.told_refs`）に残す。記録を読めない・書けないときは案内する側に倒す。
    すでにチケットに結び付く番号は `ccnavi-start.sh` を勧めず、そのチケットのワークツリーで
    続けるよう案内する（`_bound`）。
    """
    if not conf.tickets_enabled:
        return ""
    refs = prompt_refs(text)
    if not refs:
        return ""
    err = stderr if stderr is not None else sys.stderr
    told = ctxfile.told_refs(err, conf.state, session)
    if told is not None:
        refs = [r for r in refs if _mark(r) not in told]
        if not refs:
            return ""
    bound = [(r, b) for r in refs for b in [_bound(conf, root, cwd, r)] if b is not None]
    refs = [r for r in refs if all(r is not x for x, _ in bound)]
    lines = [_bound_line(r, b) for r, b in bound]
    if refs:
        lines += ([""] if lines else []) + _start_lines(conf, root, refs)
    if told is not None:
        told |= {_mark(r) for r, _ in bound} | {_mark(r) for r in refs}
        ctxfile.remember_refs(err, conf.state, session, told)
    return "\n".join(lines)


def _bound_line(ref: Ref, b: Bound) -> str:
    where = (
        f"そのチケットのワークツリー（{'・'.join(b.worktrees)}）で続ける"
        if b.worktrees
        else "そのチケットのワークツリーで続ける（ワークツリーはまだ無い。"
        "ccnavi-ticket.sh status でチケットの状態を確かめる）"
    )
    return (
        f"[ccnavi] 依頼の {ref.label()} は、すでに {'・'.join(b.tickets)}（ブランチ {b.branch}）に"
        f"結び付いている。実装の依頼なら、{where}。--check を最初に打つ。"
        "説明・調査だけなら、作業場所を変えずに答えてよい。"
    )


def project_of_repo(conf: settings.Settings, root: str, repo: str) -> str | None:
    """`owner/repo` を origin に持つ場所。自身なら空文字、プロジェクトなら名前、無ければ None。

    ワークスペースと projects/ の各プロジェクトの origin のパス（最後の 2 段）を、
    大文字小文字を区別せずに比べる。
    """
    want = pathcase.fold(repo)
    if pathcase.fold(tree.origin_repo(tree.main_tree(root).root)) == want:
        return ""
    for t in tree.projects(conf.projects):
        if pathcase.fold(tree.origin_repo(t.root)) == want:
            return t.project
    return None


def _start_lines(conf: settings.Settings, root: str, refs: list[Ref]) -> list[str]:
    sh = settings.script_command(root, "ccnavi-start.sh")
    labels = "・".join(r.label() for r in refs)
    starts: list[str] = []
    notes: list[str] = []
    for r in refs:
        where = project_of_repo(conf, root, r.repo) if r.repo else ""
        if where is None:
            notes.append(
                f"{r.label()} のリポジトリ {r.repo} は projects/ の中に無い"
                "（まだ clone していない）。"
                "ccnavi-start.sh は実行せず、clone が要ることをユーザに伝えて止まる。"
            )
            continue
        args = f"--{r.kind} {r.number}" + (f" --repo {r.repo}" if r.repo else "")
        if r.repo:
            # 実行する場所を絶対パスで名指しする（cwd がどこでも届く）。ワークスペース自身も同じ形
            place = os.path.join(conf.projects, where) if where else root
            starts.append(f"'cd {settings.shell_path(place)} && {sh} {args}'")
        else:
            starts.append(f"'{sh} {args}'")
    lines = [f"[ccnavi] 依頼に {labels} の指定がある。"]
    if starts:
        lines.append(
            f"実装の依頼なら、着手前に '{sh} --check'、続けて {'・'.join(starts)} を打ち、"
            "出力の「次:」に従う。Draft MR とワークツリーができる。"
        )
    lines.extend(notes)
    lines.append(
        "調べ・質問に答えるだけなら、チケット作業にせずそのまま答えてよい。"
        "どちらか分からなければ、ユーザに聞く。"
    )
    return lines


# ---- サブコマンド `ccnavi branches <issue|mr> <番号> --result <json>`


@dataclass
class TicketLink:
    """候補に結び付くチケット 1 枚。"""

    ticket: str
    state: str
    approved: bool
    title: str = ""
    issue: int | None = None

    def as_dict(self) -> dict:
        return {
            "ticket": self.ticket,
            "state": self.state,
            "approved": self.approved,
            "title": self.title,
            "issue": self.issue,
        }

    def label(self) -> str:
        return f"{self.ticket}({self.state})"


@dataclass
class Candidate:
    """候補のブランチ 1 本。"""

    branch: str
    local: bool = False
    origin: bool = False
    sources: list[str] = field(default_factory=list)
    mrs: list[dict] = field(default_factory=list)
    worktrees: list[str] = field(default_factory=list)
    tickets: list[TicketLink] = field(default_factory=list)

    def add_source(self, source: str) -> None:
        if source not in self.sources:
            self.sources.append(source)

    def as_dict(self) -> dict:
        return {
            "branch": self.branch,
            "local": self.local,
            "origin": self.origin,
            "sources": list(self.sources),
            "mrs": list(self.mrs),
            "worktrees": list(self.worktrees),
            "tickets": [t.as_dict() for t in self.tickets],
        }


@dataclass
class Host:
    """sh がホストを見たかどうかと、見た結果。"""

    checked: bool = False
    reason: str = ""
    name: str = ""
    repo: str = ""
    mrs: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "checked": self.checked,
            "reason": self.reason,
            "name": self.name,
            "repo": self.repo,
        }


# ホストから来た表記のうち、出力に載せるもの。改行や制御文字で行を割らせない。
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(value, limit: int = 200) -> str:
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub(" ", value).strip()[:limit]


def read_host(path: str) -> Host:
    """sh が書いたホストの結果。無い・読めない・形が違うなら「見ていない」として理由を付ける。

    形は `checked`（ホストを見たら true）・`reason`・`host`・`repo`・`mrs` の鍵を持つ JSON。
    """
    if not path:
        return Host(reason="ホストの結果が渡されていない（--result がありません）")
    data, failed = fsio.read_json(path)
    if failed is not None or not isinstance(data, dict):
        return Host(reason="ホストの結果を読めない")
    if data.get("checked") is not True:
        return Host(reason=_clean(data.get("reason")) or "理由は分からない")
    mrs = []
    for raw in data.get("mrs") or []:
        if not isinstance(raw, dict):
            continue
        number = raw.get("number")
        branch = _clean(raw.get("branch"), 250)
        if not isinstance(number, int) or isinstance(number, bool) or not branch:
            continue
        mrs.append(
            {
                "number": number,
                "branch": branch,
                "state": _clean(raw.get("state"), 20),
                "url": _clean(raw.get("url"), 500),
                "title": _clean(raw.get("title")),
                "base": _clean(raw.get("base"), 250),
                "fork": raw.get("fork") is True,
            }
        )
    return Host(
        checked=True,
        name=_clean(data.get("host")),
        repo=_clean(data.get("repo")),
        mrs=mrs,
    )


def names_number(name: str, number: int) -> bool:
    """ブランチ名が番号を含むか。番号の前後が数字でないこと（`feature-152-x`・`152-x`・`fix/152`・`issue-152`）。"""
    return re.search(rf"(?<![0-9]){number}(?![0-9])", name) is not None


def _refs(repo_root: str) -> tuple[set[str], set[str], str]:
    """手元のブランチと origin のブランチ。読めなければ理由。"""
    done = gitcmd.run(
        repo_root,
        ["for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/origin/"],
    )
    if not done.ok:
        return set(), set(), (done.failure or done.err.strip() or "git for-each-ref が失敗した")
    local: set[str] = set()
    origin: set[str] = set()
    for line in done.out.splitlines():
        if line.startswith("refs/heads/"):
            local.add(line[len("refs/heads/") :])
        elif line.startswith("refs/remotes/origin/"):
            name = line[len("refs/remotes/origin/") :]
            if name and name != "HEAD":
                origin.add(name)
    return local, origin, ""


def _rel(root: str, path: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return fsio.slashed(path)
    return "." if rel == "." else fsio.slashed(rel)


def _checked_out(conf: settings.Settings, root: str, repo: tree.Tree) -> dict[str, list[str]]:
    """このリポジトリでブランチをチェックアウトしているツリー（元のツリーとワークツリー）。"""
    out: dict[str, list[str]] = {}
    trees = [repo, *[t for t in tree.worktrees(root, conf.projects) if t.project == repo.project]]
    for t in trees:
        name = tree.branch_of(t.root)
        if name:
            out.setdefault(name, []).append(_rel(root, t.root))
    return out


def _tickets(conf: settings.Settings, root: str, project: str) -> list[ticket_model.Ticket]:
    """このリポジトリの承認済みチケット（作業中・レビュー待ち・閉じた）と、承認待ちの提案。"""
    found: list[ticket_model.Ticket] = []
    doing, _ = approval.scan(conf, root)
    for t in doing:
        t.state = t.state or ticket_model.DOING
    done, _ = approval.scan(conf, root, closed=True)
    for t in done:
        t.state = ticket_model.DONE
    review, _ = approval.scan_review(conf, root)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    todo = [t for t in proposals if t.state == ticket_model.TODO]
    for t in [*doing, *review, *done, *todo]:
        if t.project == project:
            found.append(t)
    return found


def _branch_of_ticket(t: ticket_model.Ticket, names: set[str]) -> str:
    """チケットが示すブランチ。

    識別子になるブランチ（`/` を `-` に置き換えて識別子と一致する手元か origin のブランチ。
    識別子と同じ名前を先に見る）。無ければ識別子。
    """
    if t.ticket in names:
        return t.ticket
    return next(
        (n for n in sorted(names) if ticket_ids.branch_to_id(n) == t.ticket),
        t.ticket,
    )


def collect(
    conf: settings.Settings, root: str, cwd: str, kind: str, number: int, host: Host
) -> tuple[dict, str]:
    """候補を集める。返すのは JSON の形の辞書と、止める理由（あれば）。"""
    repo, project, failed = _repo_of(conf, root, cwd)
    if repo is None:
        return {}, failed
    local, origin, failed = _refs(repo.root)
    if failed:
        return {}, f"ブランチを読めない: {failed}"
    checked_out = _checked_out(conf, root, repo)
    tickets = _tickets(conf, root, project) if conf.tickets_enabled else []

    found: dict[str, Candidate] = {}

    def candidate(branch: str) -> Candidate:
        if branch not in found:
            found[branch] = Candidate(branch, local=branch in local, origin=branch in origin)
        return found[branch]

    # 1. ホスト。MR の元ブランチ（MR 指定）か、issue を参照している開いた MR の元ブランチ
    # （issue 指定）。
    for mr in host.mrs:
        c = candidate(mr["branch"])
        c.add_source("mr")
        c.mrs.append(mr)
    # 2. チケット。issue 指定なら `issue: <番号>` を持つ親（同じリポジトリの課題だけ）。
    issue_tickets: list[TicketLink] = []
    if kind == ISSUE:
        for t in tickets:
            if t.is_child or t.issue != number or t.issue_repo:
                continue
            approved = t.state != ticket_model.TODO
            link = TicketLink(t.ticket, t.state, approved, t.title, t.issue)
            issue_tickets.append(link)
            candidate(_branch_of_ticket(t, local | origin)).add_source("ticket")
        # 3. 名前に番号を含むブランチ（手元と origin）。
        for name in sorted(local | origin):
            if names_number(name, number):
                candidate(name).add_source("name")
    elif kind == MR:
        # 2'. MR 指定なら `mr: <番号>` を持つ親（提案も承認済みも。`mr:` は同じリポジトリだけ）。
        for t in tickets:
            if t.is_child or t.mr != number:
                continue
            approved = t.state != ticket_model.TODO
            link = TicketLink(t.ticket, t.state, approved, t.title, t.issue)
            issue_tickets.append(link)
            candidate(_branch_of_ticket(t, local | origin)).add_source("ticket")
    # どの候補にも、ワークツリーと結び付くチケットを添える。
    for branch, c in found.items():
        c.worktrees = sorted(checked_out.get(branch, []))
        for t in tickets:
            approved = t.state != ticket_model.TODO
            # 識別子になるブランチ（`/` を `-` に置き換えて識別子と一致するもの）に結び付く
            if ticket_ids.branch_to_id(branch) == t.ticket:
                c.tickets.append(TicketLink(t.ticket, t.state, approved, t.title, t.issue))
    order = {"ticket": 0, "mr": 1, "name": 2}
    ranked = sorted(
        found.values(),
        key=lambda c: (min(order.get(s, 9) for s in c.sources), c.branch),
    )
    data = {
        "kind": kind,
        "number": number,
        "repo": {"project": project, "root": _rel(root, repo.root)},
        "host": host.as_dict(),
        "tickets_checked": conf.tickets_enabled,
        "candidates": [c.as_dict() for c in ranked],
        "issue_tickets": [t.as_dict() for t in issue_tickets],
    }
    return data, ""


_SOURCE_LABEL = {"mr": "MR", "ticket": "チケット", "name": "名前に番号"}


def _where(c: dict) -> str:
    places = [p for p, on in (("手元", c["local"]), ("origin", c["origin"])) if on]
    if places:
        return ",".join(places)
    if c["mrs"]:
        return "ホストだけ"
    return "まだ無い"


def render(data: dict) -> str:
    """1 候補 1 行の形。エージェントが読んでユーザに見せる。"""
    kind, number = data["kind"], data["number"]
    mark = f"issue #{number}" if kind == ISSUE else f"MR !{number}"
    repo = data["repo"]
    where = f"プロジェクト {repo['project']}" if repo["project"] else "ワークスペース"
    lines = [f"ccnavi-branches: {mark} に紐づくブランチ（{where}: {repo['root']}）"]
    host = data["host"]
    if host["checked"]:
        lines.append(f"ホスト: {host['name']} {host['repo']} を見た")
    else:
        lines.append(f"ホストは見ていない（{host['reason']}）。手元の候補だけを出す")
    if not data["tickets_checked"]:
        lines.append("チケットは見ていない（チケット制御が disable）")
    for c in data["candidates"]:
        sources = ",".join(_SOURCE_LABEL.get(s, s) for s in c["sources"])
        mrs = ",".join(
            f"!{m['number']}({m['state']}{',フォーク' if m['fork'] else ''})" for m in c["mrs"]
        )
        tickets = ",".join(f"{t['ticket']}({t['state']})" for t in c["tickets"])
        parts = [
            f"候補 {c['branch']}",
            f"在りか={_where(c)}",
            f"由来={sources}",
            f"MR={mrs or '-'}",
            f"ワークツリー={','.join(c['worktrees']) or '-'}",
            f"チケット={tickets or '-'}",
        ]
        lines.append("  ".join(parts))
    for t in data["issue_tickets"]:
        state = "承認済み" if t["approved"] else "提案（承認前）"
        lines.append(f"チケット {t['ticket']}  {state}  状態={t['state']}  題={t['title'] or '-'}")
    count = len(data["candidates"])
    if count:
        lines.append(
            f"候補 {count} 件。ユーザに見せ、既存のブランチで続けるか・新しく切るか・"
            "やめるかを聞いて返事を待つ"
        )
    else:
        lines.append("候補なし")
    return "\n".join(lines) + "\n"


def report(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    cwd: str,
    kind: str,
    number_text: str,
    result_path: str,
    as_json: bool,
) -> int:
    """`ccnavi branches <issue|mr> <番号>`。0 は出した、1 は引数か場所の誤り。"""
    if kind not in KINDS:
        stderr.write(f"ccnavi: branches の種類は issue か mr（{kind!r} は違う）\n")
        return 1
    if not re.fullmatch(r"[1-9][0-9]{0,8}", number_text or ""):
        stderr.write(f"ccnavi: branches の番号 {number_text!r} は正の整数ではない\n")
        return 1
    host = read_host(result_path)
    data, failed = collect(conf, root, cwd, kind, int(number_text), host)
    if failed:
        stderr.write(f"ccnavi: {failed}\n")
        return 1
    if as_json:
        stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    else:
        stdout.write(render(data))
    return 0
