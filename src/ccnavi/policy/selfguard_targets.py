"""守る対象の一覧。設定ファイル・層の 3 本・実行ファイルと、ワークツリー側の写しを集める。

パスを解いて並べるだけで、バックアップも復元もしない。selfguard から分けた。selfguard を読まない。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from ..infra import fsio, gitstate, platformtag, settings, tree

# 守る対象。root からの相対で書く。rules は設定で動くので、ここには無い。
_SETTINGS_FILES = (
    ("settings", os.path.join(".claude", "settings.json")),
    ("settings-local", os.path.join(".claude", "settings.local.json")),
)


@dataclass
class Target:
    """守る対象 1 つ。"""

    # key はバックアップのファイル名。対象ごとに固定してある。パスから作ると、
    # 設定でルールファイルの場所を変えただけでバックアップが別名になり、
    # 直前の断面を見失う。
    key: str = ""
    # path は行き着く先まで解いた絶対パス。
    path: str = ""
    # label は報告に出すパス。root からの相対で、ユーザが探せる形。
    label: str = ""
    # heavy は、中身を毎回読むには大きすぎる対象。実行ファイルがこれで、
    # PyInstaller が作るものは数十 MB になる。呼び出しのたびに読むと、
    # 判定に設けた期限に影響する。バックアップはセッション開始で 1 度だけ取り、
    # 照合は大きさと更新時刻で行う。
    heavy: bool = False
    # top は、この対象を git から戻すときに渡すルート。空なら root の側から戻す。
    # 持つのは 2 通り。ワークツリー側の設定と、プロジェクトのレイヤーの設定。
    # ワークスペースルートの git に
    # `.claude/worktrees/...` を聞いても、そこは `.gitignore` の中なので何も持っていない。
    # `projects/...` も同じで、プロジェクトは自分の git を持つ。持っているのは
    # そのワークツリー自身の git と、そのプロジェクト自身の git。
    top: str = ""
    # copy は、これがワークツリー側の設定であること。報告の文面がここで分かれる。
    # ワークツリー側の設定は今の時点では誰も読まないので、「何も起きていないのに
    # 戻された」と読まれる。統合で反映される経路であることを言わないと、同じ操作が繰り返される。
    copy: bool = False
    # spelled は、リンクを解く前のパス（ワークツリー側の設定だけ持つ）。着手がコピーした分かを
    # 答えさせるときに渡す。解いた先で答えると、リンクに差し替えた形が指す先の中身で外れる。
    spelled: str = ""


def targets(
    root: str,
    rules_path: str,
    bin_path: str = "",
    layers: list[settings.LayerFile] = (),
    projects_dir: str = "",
) -> list[Target]:
    """守る対象を組み立てる。

    ルールファイルと実行ファイルは設定で動くので、解決済みのパスを受け取る。
    空なら、その設定を持たないということなので、対象からも外れる。

    layers は `settings.LayerFile`（レイヤーの種別, レイヤーの名前, kind, そのファイル）のリスト
    （`ruleload.layer_files`）。kind は rules / phases / risk。共通レイヤーは phases と
    risk の 2 本で来る。rules は `rules_path` が渡していて、両方から並べると同じ
    ファイルを 2 度守ることになる。

    バックアップの key はレイヤーごとに分ける。共通レイヤーは kind そのまま（`rules` / `phases` /
    `risk`）、それ以外は `rules:self` / `phases:lib` の形。key はそのままバックアップの
    名前になるので、レイヤーが違えば別の断面として残り、取り違えが起きない。
    """
    places = _places(root, projects_dir, rules_path, layers)
    found = [
        Target(key=key, path=path, label=_relative(root, path), top=_home_top(root, home))
        for key, home, path in places
    ]
    if bin_path:
        full = os.path.realpath(bin_path)
        found.append(Target(key="bin", path=full, label=_relative(root, full), heavy=True))
        # 指す先が振り分けの sh なら、hook が実際に走らせるのは `../bin/` の実体。
        # そちらもバックアップする。探す先は binary_clause が守る置き場と同じ条件で決まる。
        # 見つからなければ足さない。組み立てが無いことは sh が起動の時に言う。
        launched = platformtag.launched_executable(bin_path)
        if launched:
            real = os.path.realpath(launched)
            found.append(
                Target(key="bin-launched", path=real, label=_relative(root, real), heavy=True)
            )
    found.extend(_worktree_copies(root, projects_dir, places))
    return found


def _places(
    root: str,
    projects_dir: str,
    rules_path: str,
    layers: list[settings.LayerFile],
) -> list[tuple[str, str, str]]:
    """守る対象の (バックアップの key, 追跡している git プロジェクトルート, 絶対パス)。

    git プロジェクトルートを一緒に持つのは 2 つの用が在るから。git から戻すときに
    どの git に聞くか（プロジェクトのレイヤーはそのプロジェクト自身の git）と、ワークツリー側の
    設定をどこからの相対で組むか（元リポジトリから）。

    同じ key が二度来たら後ろを捨てる。バックアップの名前が key で決まるので、重なったまま
    並べると、2 つの対象が同じバックアップを書き換え合う。
    """
    found = [(key, root, os.path.join(root, rel)) for key, rel in _SETTINGS_FILES]
    if rules_path:
        found.append(("rules", root, rules_path))
    for origin, layer, kind, path in layers:
        if not path:
            continue
        found.append(
            (
                _layer_key(origin, layer, kind),
                _layer_home(root, projects_dir, origin, layer),
                path,
            )
        )

    seen = set()
    places = []
    for key, home, path in found:
        if key in seen:
            continue
        seen.add(key)
        places.append((key, home, os.path.realpath(path)))
    return places


def _layer_key(origin: str, layer: str, kind: str) -> str:
    """バックアップの key。共通レイヤーは kind そのまま、それ以外は `<kind>:<レイヤー>`。

    決めるのはレイヤーの種別（`settings.ORIGIN_*`）で、名札の表記ではない。名札で
    比べると、`projects/common/` の 3 本が共通レイヤーと同じ key（`rules` / `phases` /
    `risk`）になり、`_places` の重複の排除でそのレイヤーの 3 本がバックアップと復元の対象から
    まとめて外れる。プロジェクトが名前を 1 つ選ぶだけで保護が外れることになる。

    予約名のプロジェクトは置き場をつける（`rules:projects/self`）。`projects/self/`
    の 3 本をそのまま `rules:self` にすると、こんどはワークスペース自身のレイヤーと
    ぶつかって、先に積んだほうだけが残る。名札に予約してある表記は名札の側で
    使い、プロジェクトの側は別の表記にする。
    """
    if origin == settings.ORIGIN_COMMON:
        return kind
    if origin == settings.ORIGIN_MIRROR:
        return f"{kind}:{settings.MIRROR_KEY_HOME}{layer}"
    if origin == settings.ORIGIN_PROJECT and settings.is_reserved_layer_name(layer):
        return f"{kind}:{settings.PROJECT_KEY_HOME}{layer}"
    return f"{kind}:{layer}"


def _layer_home(root: str, projects_dir: str, origin: str, layer: str) -> str:
    """そのレイヤーの設定を追跡している git プロジェクトルート。

    共通レイヤーと自身のレイヤーはワークスペースルート、プロジェクトのレイヤーはそのプロジェクト。
    ここもレイヤーの名前では決めない。`projects/common/` の設定はそのプロジェクトの git が
    追跡しているので、レイヤーの名前で共通レイヤーと同じに扱うと、ワークスペースの git に戻し方を
    聞きに行くことになる。

    名前を引けないものはワークスペースルートとして扱う。そこから切ったワークツリーに
    ワークツリー側の設定が無ければ対象から外れるだけで、別の場所を保護に行くことにはならない。
    """
    if origin not in (settings.ORIGIN_PROJECT, settings.ORIGIN_MIRROR):
        return root
    return tree.project_root(projects_dir, layer) or root


def _home_top(root: str, home: str) -> str:
    """git から戻すときに渡すルート。ワークスペースルートなら空（root の側から戻す）。"""
    if not home or os.path.realpath(home) == os.path.realpath(root):
        return ""
    return home


def _worktree_copies(
    root: str, projects_dir: str, places: list[tuple[str, str, str]]
) -> list[Target]:
    """ワークツリー側の設定。root の下と同じ設定ファイルが、ワークツリーの中にもある。

    なぜ守るかは冒頭の「ワークツリー側の設定も同じ扱い」に書いた。ここでは
    対象の組み立て方だけ。

    ワークツリーの一覧は `tree.worktrees` から取る。`.claude/worktrees/` の下に
    在るだけではワークツリーと呼ばず、`.git` ファイルと元リポジトリの登録の相互参照が
    両向きに揃ったものだけを数える。参照実装のコピーのような、ただの
    ディレクトリを保護に行かないため。git は起こさないので、呼び出しごとに
    通っても外部プロセスは増えない。

    元リポジトリをつけて列挙する。ワークツリーはワークスペースからもプロジェクトからも
    切れて、中に入っているワークツリー側の設定は元リポジトリが追跡しているものだけになる。
    lib から切ったツリーに `.claude/settings.json` は無いし、lib のレイヤーのパスは
    `projects/lib/.ccnavi/config/rules.yml` ではなく `.ccnavi/config/rules.yml`。
    ワークスペースルートからの相対で組むと、どちらの向きにも当たらない。

    設定の置き場は設定で動く。元リポジトリの外を指しているなら、ワークツリーの中に
    対応するものは無いので、そこは対象から外れる。
    """
    by_home: dict[str, list[tuple[str, str]]] = {}
    for key, home, path in places:
        rel = _inside(home, path)
        if rel:
            by_home.setdefault(os.path.realpath(home), []).append((key, rel))

    copies = []
    for work in tree.worktrees(root, projects_dir):
        home = tree.project_root(projects_dir, work.project) if work.project else root
        for key, rel in by_home.get(os.path.realpath(home), ()):
            full = os.path.realpath(os.path.join(work.root, rel))
            copies.append(
                Target(
                    key=_copy_key(key, work.name),
                    path=full,
                    label=_relative(root, full),
                    top=work.root,
                    copy=True,
                    spelled=os.path.join(work.root, rel),
                )
            )
    return copies


def _copy_key(key: str, name: str) -> str:
    """ワークツリー側の設定のバックアップの名前。

    key はそのままファイル名になるので、ワークツリーの名前をそのまま混ぜると、
    区切り文字の入った名前でバックアップが別の場所へ書かれる。使えない字を置き換えた表記だけにすると、
    今度は `a/b` と `a_b` が同じ名前になって、別のワークツリーのバックアップを互いに
    書き戻すことになる。中身が入れ替わるので、取り違えは実際に問題になる。
    置き換えた表記に元の名前の digest をつけて、読めることと衝突しないことの
    両方を取る。
    """
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{key}.{fsio.safe_name(name, 40)}-{digest}"


def _inside(root: str, path: str) -> str:
    """root の下に在るなら root からの相対、外に在るなら空文字。"""
    if not path:
        return ""
    rel = _relative(root, os.path.realpath(path))
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return ""
    return rel


def _top(root: str, target: Target) -> str:
    """この対象を git から戻すときに渡す、ワークツリーのルート。"""
    return target.top or gitstate.top_level(root)


def _relative(base: str, path: str) -> str:
    """報告と git に渡すための、base からの相対。
    別のドライブに在るなど、相対にできないものは絶対のまま返す。

    base も行き着く先まで解く。path（Target.path）は解いたパスで来るので、base が
    リンクを含むまま（macOS の /var → /private/var など）だと、相対が base の外へ
    `../` で回り込み、git に渡すパスが別の場所を指して戻せなくなる。"""
    try:
        return os.path.relpath(path, os.path.realpath(base))
    except ValueError:
        return path
