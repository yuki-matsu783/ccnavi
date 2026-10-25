"""md の frontmatter の索引を組み、それを引く（`ccnavi --docs`）。

索引を組む側は `docsearch_index`、引く側（問いの検査・ヒット・並べ方・表）は `docsearch_query` に
分けてある。どちらも docsearch を読まない。

ドキュメントを探すエージェントは、ふだん grep や Glob で本文を端から探す。ヒットするのは行で、
そのファイルが何の文書かは開くまで分からず、よそからの言及も同じ重みで混ざる。
頭の frontmatter（`type` `title` `description` `tags` `keywords`）を索引にしておけば、
「何の文書か」で引ける。形は参考にした運用（`参考/MR-driven-workflow` の
`extract-frontmatter.sh` / `search-frontmatter.sh`）に合わせてある。

## 索引

- 組むのはワークスペースルートと、プロジェクトの置き場の直下の各プロジェクト（`tree.projects`。
  それぞれ別の git）。どれも `.git` をルートに持つものだけ。引くときは全部を合わせ、
  `concept_id` はワークスペースルートからの相対（`projects/<名前>/docs/x`）にする。
  ccnavi-index.jsonl に書く行はそのツリーのルートからの相対
- 対象は各ツリーの `git ls-files --cached --others --exclude-standard` の `*.md`。消したがまだ
  ステージしていないもの（実体が無いもの）と、シンボリックリンク・ふつうのファイルでない
  ものは飛ばす。ccnavi ディレクトリ（`.ccnavi/`）の下はチケットとマーカーの置き場で、
  文書ではないので載せない。ただし使い方の文書の置き場 `.ccnavi/docs/` は載せる。
  実体のパスがツリーの外に出るディレクトリ（ジャンクションやリンク越し）は読みも書きもしない
- md が直下にあるディレクトリごとに `ccnavi-index.jsonl`。1 行
  `{"concept_id":…,"directory":…,"frontmatter":{…}|null,"mtime":"YYYY-MM-DDTHH:MM:SS"}`。
  `concept_id` はツリーのルートからの相対パスから `.md` を除いたもの。ルート直下の
  `directory` は `.`
- `concept_id` と `mtime` が既存の行と同じなら、その行を使い回す。
  書くのは一時ファイルに書いて置き換える形で、中身が同じなら書かない。一時ファイルは
  git のディレクトリ（`.git/`）の中に排他で作る（ワークツリーの `git status` に出さない）。
  `.git` が別のファイルシステムなら、そのディレクトリの下の `.ccnavi-tmp-*/ccnavi-index.jsonl`
  （git に無視されることを確かめてから）に作る
- 期限（SessionStart）を過ぎたら md を読むのをやめるが、どのディレクトリも読めた分までは書く
- 書くのは、git がそこの `ccnavi-index.jsonl` を無視しているときだけ。ワークツリーに追跡されて
  いないファイルを置くと、`git status`・実行後チェック・`worktree remove` のどれにも出る。
  md を持つディレクトリのどれでも無視されていないツリーは、書く前に、そのツリーのルートの
  `.gitignore` に `**/ccnavi-index.jsonl` を 1 行だけ足して確かめ直す（ワークスペースも
  プロジェクトも同じ。ファイルが無ければ作る）。足したことは
  SessionStart の案内と標準エラーで名指しする（未コミットの差分が残るため）。`!` で
  ccnavi-index.jsonl を否定する行があれば（ユーザが追跡している）足さない。足せなかった・
  足しても無視されないツリーだけが索引の対象外になり、引かない（案内と標準エラーで名指しする）。
  一部のディレクトリだけが無視されていないなら（追跡されている ccnavi-index.jsonl など）、
  足さず、そこは書かずに行だけを組む
- ccnavi が書いた形でない ccnavi-index.jsonl は、上書きも削除もしない。空か、空でない行が
  全部この 4 つのキーを持つ行として読めるときだけを ccnavi のものとみなす。よその道具が
  同じ名前で置いたファイルを壊さない
- md が全部消えたディレクトリ（追跡はされているが実体が無い）の `ccnavi-index.jsonl` は、上の条件で
  消す。それ以外の経路で残った古い `ccnavi-index.jsonl` は読まない
- git への問い合わせの失敗（git が無い・期限切れ・破損したリポジトリ）は「git の外」とも
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

import fnmatch
import os
import re
import time
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import fsio, hookio, settings, tree
from ..tickets import ticket_places
from . import docsearch_index, docsearch_query

# 規約の詳しい文書。ワークスペースに在るときだけ案内に名前を出す。
CONVENTION_DOC = ".ccnavi/docs/frontmatter.md"


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
    """索引を組む 1 つの git のワークツリー。prefix は concept_id の頭に付ける文字列。"""

    name: str
    base: str
    prefix: str
    excluded: tuple[str, ...]
    # excluded の中で索引に載せる例外（ccnavi ディレクトリの文書の置き場）。
    kept: tuple[str, ...] = ()


# 案内と標準エラーで、ワークスペース自身を指す名前。
WORKSPACE = "ワークスペース"


# ccnavi ディレクトリの下で、使い方の文書を置く場所。ここだけは索引に載せる。
DOCS_DIR = "docs"


def kept_dirs(home: str) -> tuple[str, ...]:
    """ccnavi ディレクトリ（home）を除外するなかで、索引に載せる例外。"""
    return (f"{home}/{DOCS_DIR}",) if home else ()


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
    kept = kept_dirs(home)
    found = [Place(WORKSPACE, root, "", tuple(workspace_excluded), kept)]
    for project in tree.projects(conf.projects):
        prefix = _rel(project.root, root)
        if not prefix:
            prefix = f"{os.path.basename(os.path.normpath(conf.projects))}/{project.project}"
        found.append(Place(project.project, project.root, prefix, own, kept))
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
    # md を持つが ccnavi-index.jsonl を無視していないので対象外にしたツリーの名前。
    unignored: list[str] = field(default_factory=list)
    # git への問い合わせに失敗したツリー（名前: 理由）。対象外とも git の外とも別。
    failed: list[str] = field(default_factory=list)
    # ccnavi のものでないので触らなかった ccnavi-index.jsonl（ワークスペースルートから）。
    foreign: list[str] = field(default_factory=list)
    # 無視の行を足したツリーの .gitignore（ワークスペースルートから）。未コミットの差分が残る。
    ignore_added: list[str] = field(default_factory=list)
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

    処理フロー:
    1. 置き場（`places`）を 1 つずつ回す。期限を過ぎたら `timed_out` にして止める。
    2. 置き場ごとに索引を組む（`docsearch_index.build`）。`.git` が無ければ飛ばし、
       git に聞けなければ `failed` に残す。
    3. 置き場の接頭辞を付けて、無視の状態・外のファイル・問題を集める（`_prefixed` の行も）。
    4. 同じ `concept_id` は 1 つにまとめ、`concept_id` の順に並べて返す。
    """
    result = Collected()
    seen: set[str] = set()
    for place in places(conf, root):
        if deadline is not None and time.monotonic() > deadline:
            result.timed_out = True
            break
        try:
            built = docsearch_index.build(
                place.base,
                place.excluded,
                refresh=refresh,
                deadline=deadline,
                parse=parse,
                kept_dirs=place.kept,
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
        if built.ignore_added:
            result.ignore_added.append(at(built.ignore_added))
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


UNIGNORED = "は .gitignore に `**/ccnavi-index.jsonl` が無く、足せなかったので索引の対象外"
IGNORE_ADDED = "に `**/ccnavi-index.jsonl` を追加しました（未コミットの差分が残る）"


# --- 入口 ---------------------------------------------------------------------------

# SessionStart で索引を新しくするのに使ってよい時間（秒）の上限。実際には hook の判定の
# 期限（events.decide が持つ deadline）の残りと小さいほう。過ぎたら残りは次の回に回す。
# 使い回せていれば数百本の md でも 1 秒に届かない。初めての大きなプロジェクトだけが
# ここにヒットし、書けたディレクトリの分は次のセッションで使い回される。
START_SECONDS = 3.0
# 残りがこれに満たなければ新しくしない（既存の ccnavi-index.jsonl だけで案内する）。
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
    for path in found.ignore_added:
        stderr.write(f"ccnavi: {path} {IGNORE_ADDED}\n")
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
    md を読まず書かずに既存の ccnavi-index.jsonl の行だけを集めて案内する（git には STALE_SECONDS
    だけ与える）。何が起きても開始は止めない。md が 1 本も無い・何かが失敗したときは何も出さない
    （空を返す）。索引の対象外にしたツリーと、触らなかった ccnavi-index.jsonl があれば短くつける。
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


# 案内で名指しする、触らなかった ccnavi-index.jsonl の数の上限。
FOREIGN_SHOWN = 3
# 引き方の案内の頭。作業の決まり（`ccnavimd.session_text`）が、この案内を出した回にだけ
# 「詳しくは --docs で引く」を添えるのに使う。
GUIDE_HEAD = "[ccnavi] ドキュメント（*.md）を探すときは"


def notice(conf: settings.Settings, root: str, found: Collected) -> str:
    extra = []
    if found.ignore_added:
        extra.append(f"{', '.join(found.ignore_added)} {IGNORE_ADDED}")
    if found.unignored:
        extra.append(
            f"{', '.join(found.unignored)} {UNIGNORED}"
            "（.gitignore が書けないか、ccnavi-index.jsonl を否定する行がある）"
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
    lines = guide_lines(conf, root, f"{GUIDE_HEAD}、grep・Glob より先に")
    lines.extend(f"  {e}" for e in extra)
    return "\n".join(lines)


def guide_lines(conf: settings.Settings, root: str, lead: str) -> list[str]:
    """索引の引き方の案内。`lead` の後ろに `--docs` の呼び方が続く 1 行。

    SessionStart の案内（notice）と、失敗が続いたときの案内（fail_streak_note）が同じ文面を使う。
    起動・再開・compact・clear のたびに届く常駐の文脈なので、引き方の要点の 1 行だけにする。
    オプションの一覧と frontmatter の書き方は先に渡さない。書き方は frontmatter の無い md を
    作った時点で案内し（`missing_frontmatter`）、詳しいことは決まりの文書を --docs で引かせる。
    """
    command = _command(conf, root)
    line = (
        f"{lead} '{command} --docs --keyword <語>' で"
        " frontmatter の索引を引いてください"
        "（--keyword のほかに --type・--tag・--path・--text でも絞れる。"
        f"絞り方の一覧は '{command} --docs --help' で見られる。"
        "ワークスペースとプロジェクトを横断し、パスはワークスペースルートから）。"
        "grep は本文中の文字列を探すときか、0 件だったときに使ってください。"
    )
    if _has_convention(root):
        line += f"書き方の決まりは '{command} --docs --keyword frontmatter' で引けます。"
    return [line]


def fail_streak_note(conf: settings.Settings, root: str, n: int) -> str:
    """失敗か拒否が n 回続いたときの案内。資料の索引を引くよう促す。

    止められた呼び出しを迂回する案内にはしない。止まった理由と決まった手順を確かめるために
    引かせ、見つからなければユーザに相談させる。
    """
    lead = (
        f"[ccnavi] ツール呼び出しの失敗か拒否が {n} 回続いています。"
        "同じ操作を言い換えて再実行したり、止められた操作の迂回を探したりせず、"
        "止まった理由と決まった手順を資料で確かめてください。確かめても分からなければ、"
        "ユーザに相談してください。資料は"
    )
    return "\n".join(guide_lines(conf, root, lead))


def _has_convention(root: str) -> bool:
    return os.path.isfile(os.path.join(root, *CONVENTION_DOC.split("/")))


# --- 書いた時点の案内（PostToolUse） --------------------------------------------------

# frontmatter の無い md を新しく作ったときの案内の理由コード（設計 付録 A）。
CODE_MISSING_FRONTMATTER = "NOTICE_DOC_FRONTMATTER"
# frontmatter を付けない決まりの md の名前。中身がそのまま使われる（README はホストや
# エディタが頭から表示し、CLAUDE.md は毎回モデルの文脈に入る）ので、頭の YAML がそのまま
# 人にもモデルにも見える。決まりの文書（CONVENTION_DOC）の「付けていない」と揃える。
NO_FRONTMATTER_NAMES = ("README.md", "CLAUDE.md", "CHANGELOG.md")
# 決まりの文書（CONVENTION_DOC）の「対象外」の表で、付けない・付けなくてよいとする置き場
# （ツリーのルートからの glob）。表を直したらここも直す
# （tests/core/test_docsearch.py が表と比べる）。
# チケットと下書きの置き場は設定で動くので、設定の値（conf.tickets・下書きの置き場）でも見る。
NO_FRONTMATTER_PLACES = (
    "wip/proposals/**",
    ".ccnavi/approved/**",
    "wip/design/scripts/i*.md",
    "wip/handoff/**",
    "scratchpad/**",
    ".claude/skills/yomiyasu/**",
)
_NO_FRONTMATTER = re.compile(
    "|".join(fnmatch.translate(glob) for glob in NO_FRONTMATTER_PLACES), re.IGNORECASE
)
# git に「無視されているか」を聞く時間の上限（秒）。過ぎたら何も言わない。
CHECK_SECONDS = 2.0


def missing_frontmatter(conf: settings.Settings, root: str, payload: hookio.Input) -> str:
    """Write で新しく作った md に frontmatter か `type` が無ければ、1 度だけ案内を返す。

    判定は変えない。案内を出すのは次をすべて満たすときだけ。

    - Write で新しく作った（`tool_response.type` が `create`）。
      既にある md の Edit・上書きでは出さない。意図して frontmatter を持たない md
      （README.md・CLAUDE.md・CHANGELOG.md、取り込んだ資料）を直すたびに促すと、
      付けに行かせることになる。作るのは 1 本につき 1 度なので、
      セッションをまたいで覚える必要も無い
    - 索引の対象に入る md。書いた先のツリー（ワークスペース・プロジェクト・ワークツリー）の
      git で決める。ワークツリーはワークスペースの `.gitignore` では無視されるが、統合した後は
      統合先の索引に入るので、そのワークツリー自身の git に聞く。ccnavi ディレクトリの下と、
      git が無視するものは索引に載らないので出さない
    - 決まりの文書で「付けない・付けなくてよい」とされている md ではない。
      README.md・CLAUDE.md・CHANGELOG.md と、
      対象外の表の置き場（NO_FRONTMATTER_PLACES。チケットとチケットの下書き・下書きの置き場・
      外から取り込んだスキル）。下書きは索引には載るが、frontmatter を求めない
    """
    if payload.tool_name != "Write" or payload.tool_response.get("type") != "create":
        return ""
    raw = str(payload.tool_input.get("file_path") or "")
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if not raw.endswith(".md") or name.casefold() in (n.casefold() for n in NO_FRONTMATTER_NAMES):
        return ""
    try:
        written = os.path.abspath(os.path.join(payload.cwd or root, raw))
        where = tree.tree_of(root, fsio.full_path(raw, payload.cwd or root), conf.projects)
        if where is None or not os.path.exists(os.path.join(where.root, ".git")):
            return ""
        rel = tree.relative(where, written)
        home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
        exempt = tuple(p for p in (home, ticket_places.HANDOFF, conf.tickets.strip("/")) if p)
        if (
            not rel
            or rel.startswith("../")
            or docsearch_index._excluded(rel, exempt, kept_dirs(home))
        ):
            return ""
        if _NO_FRONTMATTER.match(rel):
            return ""
        if rel in docsearch_index._ignored(where.root, [rel], timeout=CHECK_SECONDS):
            return ""
        head = docsearch_index._read_head(written)
    except Exception:  # noqa: BLE001 - 案内の足しなので、何が起きても実行後チェックを止めない
        return ""
    if head is None:
        return ""
    front = docsearch_index.front_matter(head)
    if front is not None and str(front.get("type") or "").strip():
        return ""
    return _frontmatter_hint(conf, root, _rel(written, root) or rel, front is not None)


def _frontmatter_hint(conf: settings.Settings, root: str, shown: str, has_front: bool) -> str:
    command = _command(conf, root)
    if has_front:
        lack = "frontmatter に type が無い"
        todo = (
            "既にあるキーの値と順序は変えず、type: skill を足してください。"
            if shown.replace("\\", "/").rsplit("/", 1)[-1].casefold() == "skill.md"
            else "既にあるキーの値と順序は変えず、type を足してください。"
        )
    else:
        lack = "frontmatter が無いか読めない"
        todo = (
            "頭を --- で囲んだ frontmatter にし、type（必須）・title・description・tags・"
            "keywords を書いてください。"
        )
    line = (
        f"[ccnavi] {CODE_MISSING_FRONTMATTER} ({shown}) "
        f"{lack}と、'{command} --docs' の --type・--tag・--keyword で見つかりません。{todo}"
    )
    if _has_convention(root):
        line += (
            f"type の値と決まりは {CONVENTION_DOC}"
            f"（'{command} --docs --keyword frontmatter' でも引けます）。"
        )
    return line
