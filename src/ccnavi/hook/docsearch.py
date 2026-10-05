"""md の frontmatter の索引を組み、それを引く（`ccnavi --docs`）。

索引を組む側は `docsearch_index`、引く側（問いの検査・当たり・並べ方・表）は `docsearch_query` に
分けてある。どちらも docsearch を読まない。

ドキュメントを探すエージェントは、ふだん grep や Glob で本文を端から探す。当たるのは行で、
そのファイルが何の文書かは開くまで分からず、よそからの言及も同じ重みで混ざる。
頭の frontmatter（`type` `title` `description` `tags` `keywords`）を索引にしておけば、
「何の文書か」で引ける。形は参考にした運用（`参考/MR-driven-workflow` の
`extract-frontmatter.sh` / `search-frontmatter.sh`）に合わせてある。

## 索引

- 組むのはワークスペースルートと、プロジェクトの置き場の直下の各プロジェクト（`tree.projects`。
  それぞれ別の git）。どれも `.git` をルートに持つものだけ。引くときは全部を合わせ、
  `concept_id` はワークスペースルートからの相対（`projects/<名前>/docs/x`）にする。
  index.jsonl に書く行はそのツリーのルートからの相対
- 対象は各ツリーの `git ls-files --cached --others --exclude-standard` の `*.md`。消したがまだ
  ステージしていないもの（実体が無いもの）と、シンボリックリンク・ふつうのファイルでない
  ものは飛ばす。ccnavi ディレクトリ（`.ccnavi/`）の下はチケットとマーカーの置き場で、
  文書ではないので載せない。実体のパスがツリーの外に出るディレクトリ（ジャンクションや
  リンク越し）は読みも書きもしない
- md が直下にあるディレクトリごとに `index.jsonl`。1 行
  `{"concept_id":…,"directory":…,"frontmatter":{…}|null,"mtime":"YYYY-MM-DDTHH:MM:SS"}`。
  `concept_id` はツリーのルートからの相対パスから `.md` を落としたもの。ルート直下の
  `directory` は `.`
- `concept_id` と `mtime` が既存の行と同じなら、その行を使い回す（読み直さない）。
  書くのは一時ファイルに書いて置き換える形で、中身が同じなら書かない。一時ファイルは
  git のディレクトリ（`.git/`）の中に排他で作る（作業ツリーの `git status` に出さない）。
  `.git` が別のファイルシステムなら、そのディレクトリの下の `.ccnavi-tmp-*/index.jsonl`
  （git に無視されることを確かめてから）に作る
- 期限（SessionStart）を過ぎたら md を読むのをやめるが、どのディレクトリも読めた分までは書く
- **書くのは、git がそこの `index.jsonl` を無視しているときだけ。** 作業ツリーに追跡されて
  いないファイルを置くと、`git status`・実行後チェック・`worktree remove` のどれにも出る。
  md を持つディレクトリのどれでも無視されていないツリーは、索引の対象外にして引かない
  （案内と標準エラーで名指しする。`.gitignore` は書き換えない）。一部のディレクトリだけが
  無視されていないなら（追跡されている index.jsonl など）、そこは書かずに行だけを組む
- **ccnavi が書いた形でない index.jsonl は、上書きも削除もしない。** 空か、空でない行が
  全部この 4 つの鍵を持つ行として読めるときだけを ccnavi のものとみなす。よその道具が
  同じ名前で置いたファイルを壊さない
- md が全部消えたディレクトリ（追跡はされているが実体が無い）の `index.jsonl` は、上の条件で
  消す。それ以外の経路で残った古い `index.jsonl` は読まない
- git への問い合わせの失敗（git が無い・期限切れ・壊れたリポジトリ）は「git の外」とも
  「無視されていない」とも別に扱う

frontmatter は PyYAML の SafeLoader（別名を拒む `flow._Loader`）で読む。読めないもの・
マッピングでないもの・JSON に書けないものは `null` にして、索引づくりは止めない。
日付などの JSON に載らない値は文字列にする。読むのはファイルの頭の 64 KiB まで（UTF-8）。

## 引く

同じオプションの繰り返しは OR、違うオプションどうしは AND。大文字小文字は区別せず、
文字列は NFC に揃えてから比べる。`--type` `--tag` `--keyword` は完全一致（`tags` が
スカラーでもリストとして扱う）、`--path` は `concept_id` への部分一致、`--text` は
`concept_id`・`mtime`・frontmatter のすべてのスカラーの値（キー名は含まない）への部分一致。
`--since` / `--until` は `mtime` と文字列で比べ、`--until` は書いた桁の終わりまで延ばす
（日付だけなら `T23:59:59`、`THH` なら `:59:59`、`THH:MM` なら `:59`）。0 件でも終了コードは 0。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import settings, tree
from . import docsearch_index, docsearch_query

# 規約の詳しい文書。ワークスペースに在るときだけ案内に名前を出す。
CONVENTION_DOC = "docs/claude/frontmatter.md"


def _rel(path: str, root: str) -> str:
    """root からの相対（`/` 区切り）。root の外なら空。"""
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except ValueError:
        return ""
    rel = rel.replace(os.sep, "/")
    return "" if rel in (".", "..") or rel.startswith("../") else rel


@dataclass
class Place:
    """索引を組む 1 つの git の作業ツリー。prefix は concept_id の頭に付ける文字列。"""

    name: str
    base: str
    prefix: str
    excluded: tuple[str, ...]


# 案内と標準エラーで、ワークスペース自身を指す名前。
WORKSPACE = "ワークスペース"


def places(conf: settings.Settings, root: str) -> list[Place]:
    """ワークスペースルートと、プロジェクトの置き場の直下のプロジェクト（`tree.projects`）。

    ワークスペースの一覧からは ccnavi ディレクトリとプロジェクトの置き場を外す。
    プロジェクトは別の git なので、そこの md はそのプロジェクトの一覧で拾う。
    プロジェクトの concept_id はワークスペースルートからの相対（`projects/<名前>/…`）にする。
    """
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
    own = (home,) if home else ()
    workspace_excluded = list(own)
    projects_rel = _rel(conf.projects, root) if conf.projects else ""
    if projects_rel:
        workspace_excluded.append(projects_rel)
    found = [Place(WORKSPACE, root, "", tuple(workspace_excluded))]
    for project in tree.projects(conf.projects):
        prefix = _rel(project.root, root)
        if not prefix:
            prefix = f"{os.path.basename(os.path.normpath(conf.projects))}/{project.project}"
        found.append(Place(project.project, project.root, prefix, own))
    return found


def without_git(conf: settings.Settings) -> list[str]:
    """プロジェクトの置き場の直下にあって `.git` を持たないディレクトリの名前。"""
    if not conf.projects:
        return []
    try:
        names = sorted(os.listdir(conf.projects))
    except OSError:
        return []
    return [
        name
        for name in names
        if os.path.isdir(os.path.join(conf.projects, name))
        and not os.path.exists(os.path.join(conf.projects, name, ".git"))
    ]


def _prefixed(row: dict, prefix: str) -> dict:
    if not prefix:
        return row
    moved = dict(row)
    moved["concept_id"] = f"{prefix}/{row.get('concept_id', '')}"
    directory = row.get("directory") or docsearch_index.ROOT_DIRECTORY
    moved["directory"] = (
        prefix if directory == docsearch_index.ROOT_DIRECTORY else f"{prefix}/{directory}"
    )
    return moved


@dataclass
class Collected:
    """ワークスペースとプロジェクトを合わせた索引。concept_id はワークスペースルートから。"""

    rows: list[dict] = field(default_factory=list)
    # md を持つが index.jsonl を無視していないので対象外にしたツリーの名前。
    unignored: list[str] = field(default_factory=list)
    # git への問い合わせに失敗したツリー（名前: 理由）。対象外とも git の外とも別。
    failed: list[str] = field(default_factory=list)
    # ccnavi のものでないので触らなかった index.jsonl（ワークスペースルートから）。
    foreign: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # どれかのツリーが md を持ち、索引の対象だった。
    indexable: bool = False
    timed_out: bool = False


def collect(
    conf: settings.Settings,
    root: str,
    refresh: bool = True,
    deadline: float | None = None,
    parse: bool = True,
) -> Collected:
    """ワークスペースとプロジェクトの索引を新しくして集める。

    `.git` の無いツリーは何も出さずに飛ばす。
    """
    result = Collected()
    seen: set[str] = set()
    for place in places(conf, root):
        if deadline is not None and time.monotonic() > deadline:
            result.timed_out = True
            break
        try:
            built = docsearch_index.build(
                place.base, place.excluded, refresh=refresh, deadline=deadline, parse=parse
            )
        except docsearch_index.NotARepository:
            continue
        except docsearch_index.GitFailed as exc:
            result.failed.append(f"{place.name}: {exc}")
            result.timed_out = result.timed_out or docsearch_index._past(deadline)
            continue
        if built.unignored:
            result.unignored.append(place.name)
        result.timed_out = result.timed_out or built.timed_out
        result.indexable = result.indexable or built.indexable

        def at(rel: str, place: Place = place) -> str:
            return f"{place.prefix}/{rel}" if place.prefix else rel

        result.foreign.extend(at(p) for p in built.foreign)
        result.problems.extend(at(p) for p in built.problems)
        for row in built.rows:
            moved = _prefixed(row, place.prefix)
            key = docsearch_index._norm(str(moved.get("concept_id", "")))
            if key in seen:
                continue
            seen.add(key)
            result.rows.append(moved)
    result.rows.sort(key=lambda r: str(r.get("concept_id", "")))
    return result


UNIGNORED = "は .gitignore に `**/index.jsonl` が無いので索引の対象外"


# --- 入口 ---------------------------------------------------------------------------

# SessionStart で索引を新しくするのに使ってよい時間（秒）の上限。実際には hook の判定の
# 期限（events.decide が持つ deadline）の残りと小さいほう。過ぎたら残りは次の回に回す。
# 使い回せていれば数百本の md でも 1 秒に届かない。初めての大きなプロジェクトだけが
# ここに当たり、書けたディレクトリの分は次のセッションで使い回される。
START_SECONDS = 3.0
# 残りがこれに満たなければ新しくしない（既存の index.jsonl だけで案内する）。
MIN_REFRESH_SECONDS = 0.5
# 新しくしないときに git に与える時間（ls-files と check-ignore）。
STALE_SECONDS = 1.0


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    query: docsearch_query.Query,
    refresh: bool = True,
) -> int:
    """`ccnavi --docs`。ワークスペースと、索引の対象になるプロジェクトの md を引く。0 件でも 0。

    期限は無い。初めての回は md を全部読む（1 本につき頭の数 KiB）ので、大きなツリーでは
    数秒かかることがある。2 回目からは mtime の変わった md だけを読む。
    """
    problems = docsearch_query.problems_of(query)
    if problems:
        for problem in problems:
            stderr.write(f"ccnavi: {problem}\n")
        return 1
    found = collect(conf, root, refresh=refresh)
    for name in without_git(conf):
        stderr.write(f"ccnavi: プロジェクト {name} は .git を持たないので引かない\n")
    for failure in found.failed:
        stderr.write(f"ccnavi: git への問い合わせに失敗したので引かない: {failure}\n")
    for problem in found.problems:
        stderr.write(f"ccnavi: 索引を残せなかった（引くのは続ける）: {problem}\n")
    if found.unignored:
        stderr.write(f"ccnavi: {', '.join(found.unignored)} {UNIGNORED}\n")
    hits, matched = docsearch_query.search(found.rows, query)
    docsearch_query.render(stdout, hits, matched, len(found.rows), query.format)
    if query.format in ("table", "detail"):
        shown = f" shown={len(hits)}" if len(hits) != matched else ""
        stderr.write(f"matched={matched}{shown} total={len(found.rows)}\n")
    return 0


def _command(conf: settings.Settings, root: str) -> str:
    """案内に書く ccnavi のパス。設定が相対ならワークスペースルートから書く。"""
    path = conf.bin
    if path and not os.path.isabs(path):
        path = os.path.realpath(os.path.join(root, path))
    return settings.bin_command(path)


def at_start(conf: settings.Settings, root: str, deadline: float | None = None) -> str:
    """SessionStart。ワークスペースとプロジェクトの索引を差分で新しくし、引き方の案内を返す。

    使う時間は START_SECONDS と、渡された期限（hook の判定の期限）の残りの小さいほう。
    残りが MIN_REFRESH_SECONDS に満たない（期限が既に切れているときも）なら新しくせず、
    md を読まず書かずに既存の index.jsonl の行だけを集めて案内する（git には STALE_SECONDS
    だけ与える）。何が起きても開始は止めない。md が 1 本も無い・何かが壊れたときは何も出さない
    （空を返す）。索引の対象外にしたツリーと、触らなかった index.jsonl があれば短くつける。
    git への問い合わせに失敗したツリーは何も言わない（対象外と取り違えさせない）。
    """
    try:
        now = time.monotonic()
        limit = now + START_SECONDS
        if deadline is not None:
            limit = min(limit, deadline)
        if limit - now >= MIN_REFRESH_SECONDS:
            found = collect(conf, root, deadline=limit)
        else:
            found = collect(conf, root, refresh=False, parse=False, deadline=now + STALE_SECONDS)
            found.timed_out = True
        return notice(conf, root, found)
    except Exception:  # noqa: BLE001 - 索引は案内の足しで、セッションの開始を止めない
        return ""


def _has_md(root: str) -> bool:
    """ワークスペースルートの直下に md があるか（git に聞けないときの目安）。"""
    try:
        with os.scandir(root) as entries:
            return any(e.name.endswith(".md") and e.is_file() for e in entries)
    except OSError:
        return False


# 案内で名指しする、触らなかった index.jsonl の数の上限。
FOREIGN_SHOWN = 3


def notice(conf: settings.Settings, root: str, found: Collected) -> str:
    extra = []
    if found.unignored:
        extra.append(
            f"{', '.join(found.unignored)} {UNIGNORED}（ccnavi は .gitignore を書き換えない）"
        )
    if found.foreign:
        names = ", ".join(found.foreign[:FOREIGN_SHOWN])
        rest = len(found.foreign) - FOREIGN_SHOWN
        more = f" ほか {rest} 本" if rest > 0 else ""
        extra.append(f"ccnavi の索引ではないので書き換えなかった: {names}{more}")
    # 行が無くても、索引の対象のツリーに md がある（打ち切りで読めなかった）か、時間切れで
    # git に聞けなかったがワークスペースに md があるなら、引き方の案内は出す。
    guide = bool(found.rows) or found.indexable
    if not guide and found.timed_out and not found.unignored:
        guide = _has_md(root)
    if not guide:
        return "\n".join(f"[ccnavi] md の索引（--docs）: {e}" for e in extra)
    command = _command(conf, root)
    lines = [
        f"[ccnavi] ドキュメント（*.md）を探すときは、grep・Glob より先に '{command} --docs' で"
        " frontmatter の索引を引いてください（ワークスペースとプロジェクトを横断。パスは"
        "ワークスペースルートから）。grep は本文中の文字列を探すときか、0 件だったときに"
        "使ってください。",
        "  絞り込み: --type / --tag / --keyword <値>（完全一致）、--path <部分>（パス）、"
        "--text <部分>（パス・更新日時・frontmatter の値）、--since / --until <YYYY-MM-DD>。"
        "同じものの繰り返しは OR、違うものどうしは AND。大文字小文字は区別しない",
        "  並べ方と形: --sort path|mtime|type|title、-r（逆順）、--limit <N>、"
        "--format table|path|detail|json|jsonl|count",
        "  md を書くときは頭に frontmatter を付けてください。type は必須、title・description・"
        "tags（kebab-case で 2〜4 個）・keywords は推奨。",
    ]
    if os.path.isfile(os.path.join(root, *CONVENTION_DOC.split("/"))):
        lines[-1] += f"type の値と詳しい決まりは {CONVENTION_DOC}"
    lines.extend(f"  {e}" for e in extra)
    return "\n".join(lines)
