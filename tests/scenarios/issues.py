"""setup.issue の issue を gh / glab で作って閉じる（層 1）。"""

import re
import subprocess
from pathlib import Path

ISSUE_PLACEHOLDER = "{issue}"

# origin の URL（https・ssh・scp 形式）からホストを取り出す。実行ファイルの分け方と同じ表記を読む。
_REMOTE = re.compile(
    r"^(?:(?i:https?)://|(?i:ssh)://git@|git@)(?:[^/]*@)?"
    r"(?P<host>\[[^\]/]+\]|[^/:@]+)(?::[0-9]+)?[/:]+.+?(?:\.git)?/?$"
)
_GHE_DOMAIN = "ghe.com"


def host_kind(url):
    """origin の URL から、ホストの種類（github / gitlab。読めない表記なら空）を返す。

    github.com と GHE.com（`ghe.com` とそのサブドメイン）は GitHub、ほかは GitLab として扱う。
    """
    m = _REMOTE.match(url)
    if m is None:
        return ""
    host = m.group("host").lower()
    if host == "github.com" or host == _GHE_DOMAIN or host.endswith("." + _GHE_DOMAIN):
        return "github"
    return "gitlab"


def issue_number(url):
    """issue 作成の出力から、issue の番号を取る。

    URL の行（GitHub は `.../issues/12`、GitLab は `.../-/issues/12`）の末尾の数字。
    glab は URL の前後に説明の行を出すので、最終行ではなく URL の行を探す。
    """
    for line in reversed(url.strip().splitlines()):
        tail = line.strip().rstrip("/").rsplit("/", 1)[-1]
        if "://" in line and "/issues/" in line and tail.isdigit():
            return int(tail)
    raise RuntimeError(f"issue の番号が取れない: {url.strip()}")


def host_cli(kind):
    """ホストの種類（host_kind）から CLI の名前を返す。GitLab は glab、GitHub は gh。"""
    if kind == "github":
        return "gh"
    if kind == "gitlab":
        return "glab"
    raise RuntimeError("origin からホストの種類を判別できない")


def issue_create_args(kind, issue):
    """issue を作る引数の列（CLI の名前から始まる）。"""
    title, body = issue["title"], issue.get("body", "")
    if kind == "github":
        return ["gh", "issue", "create", "--title", title, "--body", body]
    return [host_cli(kind), "issue", "create", "--title", title, "--description", body, "--yes"]


def issue_close_args(kind, number):
    """issue を閉じる引数の列（CLI の名前から始まる）。"""
    return [host_cli(kind), "issue", "close", str(number)]


def repo_host_kind(repo_dir):
    """リポジトリの origin から、ホストの種類（github / gitlab。読めなければ空）を返す。"""
    r = subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return ""
    return host_kind(r.stdout.strip())


def make_issue(issue, root):
    """setup の issue の欄どおりに、test-repo へ issue を新しく 1 つ作る。番号を返す。"""
    repo = Path(root) / issue["repo"]
    args = issue_create_args(repo_host_kind(repo), issue)
    r = subprocess.run(
        args, cwd=repo, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} が終了コード {r.returncode}: {r.stderr.strip()}")
    return issue_number(r.stdout)


def close_issue(issue, number, root):
    """回したあとに、作った issue を閉じる（setup の issue の close: true のときだけ）。"""
    repo = Path(root) / issue["repo"]
    try:
        r = subprocess.run(
            issue_close_args(repo_host_kind(repo), number),
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        # 後片付けの途中なので、対象を戻す処理を止めない
        print(f"issue #{number} を閉じられなかった: {exc}")
        return
    if r.returncode != 0:
        print(f"issue #{number} を閉じられなかった: {r.stderr.strip()}")


def fill_issue(text, number):
    """prompt の {issue} を、作った issue の番号に置き換える。番号が無ければそのまま。"""
    return text if number is None else text.replace(ISSUE_PLACEHOLDER, str(number))
