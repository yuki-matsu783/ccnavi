"""記録と state の後始末。記録を大きさでローテートし、古い記録と終わったセッションの記録を消す。

走るのはセッションの開始（events.decide_at_start）と、ユーザが端末から打つ `ccnavi --prune`
だけ。実行前チェック（PreToolUse）では走らせない。ツール呼び出しのたびに置き場を数えると、
判定を待たせる（ADR-0003 と同じ理由で、重い仕事はセッションに 1 度の場所へ置く）。

**記録（`logs/decisions.jsonl`）。** 大きさが上限を超えていたら、同じディレクトリの
`decisions.<日時>.jsonl` へ名前を変える。中身は 1 行も捨てない。「記録が無い呼び出し = ccnavi が
動かなかった」という読み方（audit）は、ローテートした分も合わせて読めば変わらない。
消すのは、ローテートした記録のうち最後に書かれてから保持日数を過ぎたものだけで、
いま書いている `decisions.jsonl` は消さない。

**診断ログ（`logs/diag/*.log`）。** 記録と同じしきい値で、大きさでローテートし、
保持日数（CCNAVI_LOG_KEEP_DAYS）のあいだ書かれていないものを消す（_prune_diag）。

**state（`logs/state/`）。** 消すのはセッションを名前に持つものだけで、セッションごとにまとめて
判断する。そのセッションの記録のどれかが保持日数のうちに書かれていれば、全部を残す。
動いているセッションは、ターンの始まりの基準（`<セッション>.turn.json`）をプロンプトの
たびに、自己防衛のバックアップ（`selfguard/<セッション>/`）を実行前チェックのたびに書き直すので、
必ずどれかが新しい。Claude Code はセッションの終わりを hook に知らせないので、「終わった」は
「保持日数のあいだ何も書いていない」で読む。いま始まったセッション自身は日付を見ずに残す。

セッションを名前に持たないもの（レビューの下書き `review-*.md`、リスクの判定の下書き
`risk-judge-*.md`、戻したときに退避したファイル `aside/`、自己防衛の実体 `selfguard/store/`）は
消さない。チケットやレビューの寿命で使われるか、ユーザのファイルそのものなので、セッションの
日付では決められない。

しきい値は環境変数で動かせる（LIMITS）。0 はその段を止める。読めない値・有限でない値
（`inf`・`nan`）・0 より大きく下限（FLOORS、1 MB と 1 日）より小さい値は既定で動き、報告に
出す。下限を置くのは、しきい値を 0 に近づけるだけで保持日数のうちの記録が消えるため。
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field

from ..infra import fsio
from . import diaglog

# しきい値の環境変数と既定値。
LOG_ROTATE_MB_ENV = "CCNAVI_LOG_ROTATE_MB"
LOG_KEEP_DAYS_ENV = "CCNAVI_LOG_KEEP_DAYS"
STATE_KEEP_DAYS_ENV = "CCNAVI_STATE_KEEP_DAYS"
DEFAULT_LOG_ROTATE_MB = 10.0
DEFAULT_LOG_KEEP_DAYS = 14.0
DEFAULT_STATE_KEEP_DAYS = 14.0
LIMITS = (
    (LOG_ROTATE_MB_ENV, DEFAULT_LOG_ROTATE_MB),
    (LOG_KEEP_DAYS_ENV, DEFAULT_LOG_KEEP_DAYS),
    (STATE_KEEP_DAYS_ENV, DEFAULT_STATE_KEEP_DAYS),
)
# 0 のほかに受ける値の下限。これより小さい値は既定で動く。
FLOORS = {
    LOG_ROTATE_MB_ENV: 1.0,
    LOG_KEEP_DAYS_ENV: 1.0,
    STATE_KEEP_DAYS_ENV: 1.0,
}

# ローテートした記録の日時の書き方。名前の順と時刻の順が合う形。
STAMP = "%Y%m%d-%H%M%S"

# 自己防衛のバックアップの置き場（selfguard.BACKUP_DIR / STORE_DIR）。selfguard は state の段なので
# ここからは読めない。名前をコピーして持ち、tests/core/test_prune.py が一致を見る。
SELFGUARD_DIR = "selfguard"
SELFGUARD_STORE = "store"

# 名前からセッションがそのまま読める記録。
_SESSION_ONLY = (
    re.compile(r"^nudged-(?P<s>.+)\.json$"),  # ops._nudge_path
    re.compile(r"^denied-(?P<s>.+)\.json$"),  # repeat._path
    re.compile(r"^stop-(?P<s>.+)\.json$"),  # ctxfile.stop_path
    re.compile(r"^(?P<s>.+)\.turn\.json$"),  # post._turn_path
)
# 名前の後ろにセッション以外の鍵が `-` で続く記録。セッションにも `-` が入るので、
# 名前だけでは切れ目が決まらない。既に分かっているセッションの表記に当てて決める。
_SESSION_AND_KEY = (
    ("once-", ".json"),  # ctxfile._once_path
    ("approved-", ".json"),  # agree._news_path
    ("subagent-", ".bounced"),  # subagent._bounce_path
)
# `<セッション>.json`（post._seen_path）。Claude Code のセッションは UUID なので、その形に
# 限る。`.+` で読むと、置き場に置かれた別のファイル（`package.json`、レビューの結果の
# コピーなど）をセッションの記録と読み違えて消す。形の違う名前は知らないファイルとして残す。
_SEEN = re.compile(
    r"^(?P<s>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.json$"
)


# ローテートした診断ログ（`<出どころ>.<日時>.log`）。_rotated_name と同じ形。
_DIAG_ROTATED = re.compile(r"\.\d{8}-\d{6}(?:-\d+)?\.log$")


@dataclass
class Report:
    """1 回の後始末で起きたこと。パスはワークスペースルートからの相対（外なら絶対）。"""

    rotated: list[tuple[str, str]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    state: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.rotated or self.logs or self.state)


def limits() -> tuple[float, float, float, list[str]]:
    """しきい値（ローテートの MB、記録の保持日数、記録の保持日数）と、読めなかった値の苦情。"""
    values = []
    problems = []
    for env, default in LIMITS:
        raw = os.environ.get(env, "").strip()
        if not raw:
            values.append(default)
            continue
        try:
            value = float(raw)
        except ValueError:
            value = -1.0
        floor = FLOORS[env]
        if value < 0 or not math.isfinite(value):
            problems.append(f"{env}={raw!r} は 0 以上の数ではないので既定の {default:g} で動く")
            value = default
        elif 0 < value < floor:
            problems.append(
                f"{env}={raw!r} は下限の {floor:g} より小さいので既定の {default:g} で動く"
                "（0 はその段を止める）"
            )
            value = default
        values.append(value)
    return values[0], values[1], values[2], problems


def run(
    root: str,
    log_path: str,
    state_dir: str,
    session: str = "",
    *,
    dry_run: bool = False,
    now: float | None = None,
) -> Report:
    """ローテートと削除を 1 度ずつ行う。dry_run なら何も動かさず、動かすはずのものを返す。

    途中の失敗は problems に積んで先へ進む。1 つ消せなくても、残りの後始末は続ける。
    """
    now = time.time() if now is None else now
    rotate_mb, log_days, state_days, problems = limits()
    report = Report(problems=problems)
    if log_path:
        if rotate_mb > 0:
            _rotate(report, root, log_path, int(rotate_mb * 1024 * 1024), dry_run, now)
        if log_days > 0:
            _prune_logs(report, root, log_path, now - log_days * 86400, dry_run)
    _prune_diag(report, root, rotate_mb, log_days, dry_run, now)
    if state_dir and state_days > 0:
        _prune_state(report, root, state_dir, session, now - state_days * 86400, dry_run)
    return report


def summary(report: Report) -> str:
    """記録（audit の detail）に残す 1 行。何も動かなければ空。"""
    if report.empty():
        return ""
    return f"pruned rotated={len(report.rotated)} logs={len(report.logs)} state={len(report.state)}"


def lines(report: Report, dry_run: bool) -> list[str]:
    """ユーザに見せる行。"""
    rotate, remove = ("ローテートする", "消す") if dry_run else ("ローテートした", "消した")
    out = [f"{rotate}  {src} → {dst}" for src, dst in report.rotated]
    out += [f"{remove}  {path}" for path in report.logs + report.state]
    if not out:
        out.append("動かすものは無い")
    return out


def _shown(root: str, path: str) -> str:
    """見せるパス。ワークスペースの中なら相対、外なら絶対。区切りは "/"。"""
    full = os.path.abspath(path)
    try:
        rel = os.path.relpath(full, os.path.abspath(root))
    except ValueError:
        # Windows で別のドライブ。
        return fsio.slashed(full)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return fsio.slashed(full)
    return fsio.slashed(rel)


def _rotated_name(log_path: str, now: float) -> str:
    """ローテート先。`decisions.jsonl` → `decisions.<日時>.jsonl`。

    同じ名前があれば `-2` から足す。
    """
    directory, base = os.path.split(log_path)
    stem, ext = os.path.splitext(base)
    stamp = time.strftime(STAMP, time.localtime(now))
    candidate = os.path.join(directory, f"{stem}.{stamp}{ext}")
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}.{stamp}-{n}{ext}")
        n += 1
    return candidate


def _reserve(log_path: str, now: float) -> str:
    """ローテート先を空のファイルで押さえて、その名前を返す。

    名前を決めてから変えるまでのあいだに、同じ秒に始まった別のセッションが同じ名前を
    選ぶと、後から変えた側が先の記録を上書きする。O_EXCL で作れた名前だけを自分のものに
    する。作ったファイルは名前を変えるときに置き換わる（os.replace。Windows でも上書きする）。
    """
    directory, base = os.path.split(log_path)
    stem, ext = os.path.splitext(base)
    stamp = time.strftime(STAMP, time.localtime(now))
    n = 1
    while True:
        suffix = "" if n == 1 else f"-{n}"
        candidate = os.path.join(directory, f"{stem}.{stamp}{suffix}{ext}")
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return candidate


def _rotated_pattern(log_path: str) -> re.Pattern[str]:
    stem, ext = os.path.splitext(os.path.basename(log_path))
    return re.compile("^" + re.escape(stem) + r"\.\d{8}-\d{6}(?:-\d+)?" + re.escape(ext) + "$")


def _rotate(
    report: Report, root: str, log_path: str, limit: int, dry_run: bool, now: float
) -> bool:
    """大きさが上限を超えていたら名前を変える。変えた（dry_run なら変える）とき真。

    名前を変えるだけで、書き写さない。同じ時に追記している hook は、開いたハンドルのまま
    ローテート先へ書き、次の起動からは新しい `decisions.jsonl` を作って書く（audit は開くたびに
    O_CREAT で作る）。どちらの 1 行も失われない。Windows で開かれていて変えられなければ、
    次の開始でまた試す。
    """
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return False
    if size <= limit:
        return False
    if dry_run:
        report.rotated.append((_shown(root, log_path), _shown(root, _rotated_name(log_path, now))))
        return True
    target = ""
    try:
        target = _reserve(log_path, now)
        # 同じ時に始まった別のセッションが先に変えていれば、ここには小さい新しい記録がある。
        if os.path.getsize(log_path) <= limit:
            _drop(target)
            return False
        os.replace(log_path, target)
    except FileNotFoundError:
        _drop(target)
        return False
    except OSError as exc:
        _drop(target)
        report.problems.append(f"{_shown(root, log_path)} をローテートできない: {exc}")
        return False
    report.rotated.append((_shown(root, log_path), _shown(root, target)))
    return True


def _drop(reserved: str) -> None:
    """押さえたまま使わなかったローテート先を消す。"""
    if reserved:
        with contextlib.suppress(OSError):
            os.remove(reserved)


def _prune_logs(report: Report, root: str, log_path: str, cutoff: float, dry_run: bool) -> None:
    """ローテートした記録のうち、最後に書かれてから保持日数を過ぎたものを消す。"""
    directory = os.path.dirname(log_path) or "."
    pattern = _rotated_pattern(log_path)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return
    for name in names:
        if not pattern.match(name):
            continue
        path = os.path.join(directory, name)
        try:
            if not os.path.isfile(path) or os.path.getmtime(path) >= cutoff:
                continue
        except OSError:
            continue
        if _remove(report, root, path, dry_run):
            report.logs.append(_shown(root, path))


def _prune_diag(
    report: Report, root: str, rotate_mb: float, log_days: float, dry_run: bool, now: float
) -> None:
    """診断ログ（`logs/diag/*.log`、diaglog）の後始末。しきい値は記録と同じものを使う。

    出どころごとの 1 本が上限を超えていたら、記録と同じく `<出どころ>.<日時>.log` へ名前を
    変える（_rotate）。そのうえで、ローテートした分も含めて最後に書かれてから保持日数を過ぎた
    `*.log` を消す。この回にローテートした 1 本はこの回には消さない（名前が変わって元の
    場所から無くなるため）。dry_run でも同じ扱いにして、本番と同じ結果を示す。
    置き場（`logs` か `logs/diag`）がリンクなら辿らない（state の置き場と同じ理由。logger も
    リンクの先には書かない）。
    """
    directory = os.path.join(root, diaglog.DIAG_DIR)
    for place in (os.path.dirname(directory), directory):
        if os.path.islink(place):
            report.problems.append(
                f"{_shown(root, place)} はリンクなので辿らない（診断ログを消さない）"
            )
            return
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return
    cutoff = now - log_days * 86400
    for name in names:
        if not name.endswith(".log"):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                continue
        except OSError:
            continue
        if (
            rotate_mb > 0
            and not _DIAG_ROTATED.search(name)
            and _rotate(report, root, path, int(rotate_mb * 1024 * 1024), dry_run, now)
        ):
            continue
        if log_days <= 0:
            continue
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
        except OSError:
            continue
        if _remove(report, root, path, dry_run):
            report.logs.append(_shown(root, path))


def _prune_state(
    report: Report, root: str, state_dir: str, session: str, cutoff: float, dry_run: bool
) -> None:
    """終わったセッションの記録を、セッションごとにまとめて消す。

    置き場そのものがリンクなら、何も消さずに報告に出す。リンクの先は ccnavi の置き場とは
    限らず、辿って消すと置き場の外のファイルを消す。
    """
    if os.path.islink(state_dir):
        report.problems.append(
            f"{_shown(root, state_dir)} はリンクなので辿らない（記録を消さない）"
        )
        return
    groups, loose = _session_entries(report, root, state_dir)
    mine = {fsio.safe_name(session), fsio.safe_name(session, limit=None)} if session else set()
    doomed: list[str] = []
    for token, paths in sorted(groups.items()):
        if token in mine:
            continue
        if max(_touched(p) for p in paths) >= cutoff:
            continue
        doomed.extend(paths)
    # どのセッションにも結び付けられなかったもの（名前の切れ目が決まらない）は、
    # それ自身の日付で見る。
    doomed.extend(p for p in loose if _touched(p) < cutoff)
    for path in sorted(doomed):
        if _remove(report, root, path, dry_run):
            report.state.append(_shown(root, path))


def _session_entries(
    report: Report, root: str, state_dir: str
) -> tuple[dict[str, list[str]], list[str]]:
    """state の置き場を、セッションの表記ごとにまとめる。2 つめはまとめられなかったもの。"""
    groups: dict[str, list[str]] = {}
    try:
        names = os.listdir(state_dir)
    except OSError:
        return groups, []

    guard = os.path.join(state_dir, SELFGUARD_DIR)
    guarded: list[str] = []
    if os.path.islink(guard):
        # 置き場そのものと同じ理由で辿らない。
        report.problems.append(
            f"{_shown(root, guard)} はリンクなので辿らない（バックアップを消さない）"
        )
    else:
        try:
            guarded = os.listdir(guard) if os.path.isdir(guard) else []
        except OSError:
            guarded = []
    for name in guarded:
        path = os.path.join(guard, name)
        if name != SELFGUARD_STORE and os.path.isdir(path):
            groups.setdefault(name, []).append(path)

    keyed: list[tuple[str, str, str]] = []
    for name in names:
        path = os.path.join(state_dir, name)
        if not os.path.isfile(path):
            continue
        pair = next(
            ((p, s) for p, s in _SESSION_AND_KEY if name.startswith(p) and name.endswith(s)),
            None,
        )
        if pair is not None:
            keyed.append((name, pair[0], pair[1]))
            continue
        for pattern in (*_SESSION_ONLY, _SEEN):
            m = pattern.match(name)
            if m:
                groups.setdefault(m.group("s"), []).append(path)
                break

    loose = []
    known = sorted(groups, key=len, reverse=True)
    for name, prefix, suffix in keyed:
        middle = name[len(prefix) : len(name) - len(suffix)]
        token = next((t for t in known if middle.startswith(t + "-")), None)
        path = os.path.join(state_dir, name)
        if token is None:
            loose.append(path)
        else:
            groups[token].append(path)
    return groups, loose


def _touched(path: str) -> float:
    """最後に書かれた時刻。ディレクトリなら中身も見る（名前が増えないと親の時刻は変わらない）。"""
    newest = 0.0
    entries = [path]
    if os.path.isdir(path):
        with contextlib.suppress(OSError):
            entries += [os.path.join(path, n) for n in os.listdir(path)]
    for entry in entries:
        try:
            newest = max(newest, os.stat(entry).st_mtime)
        except OSError:
            continue
    return newest


def _remove(report: Report, root: str, path: str, dry_run: bool) -> bool:
    """1 つを消す。消した（dry_run なら消すはずの）とき True。"""
    if dry_run:
        return True
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except FileNotFoundError:
        # 同じ時に始まった別のセッションが先に消した。
        return False
    except OSError as exc:
        report.problems.append(f"{_shown(root, path)} を消せない: {exc}")
        return False
    return True
