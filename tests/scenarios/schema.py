"""シナリオの読み込みと形の検査（段 0）。

外を呼ばない。setup.host の値の検査だけ hostenv に任せる。
"""

import re

import yaml

from tests.scenarios import hostenv

SETUP_KEYS = {"files", "issue", "git", "reset", "host", "host_state"}

# 回す相手（実行器の --agent）。claude は本物の claude -p で回す。script は step の script を
# 代役（tests/fake_claude.py）で回す。シナリオのファイルには書かない（1 本を両方で回す）
AGENT_CLAUDE = "claude"
AGENT_SCRIPT = "script"


SHA40 = re.compile(r"[0-9a-fA-F]{40}")

# setup.git で、対象の外へ出られる git の指定
GIT_ESCAPES = ("-C", "--git-dir", "--work-tree")

# git の先頭（サブコマンドより前）に置くと、設定や実行ファイルを差し替えられる指定
GIT_GLOBAL_ESCAPES = ("-c", "--config-env", "--exec-path")


def load_scenario(path, agent=AGENT_CLAUDE):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for key in ("id", "steps"):
        if key not in data:
            raise ValueError(f"{path}: {key} がありません")
    data.setdefault("model", "haiku")
    data.setdefault("budget_usd", 1.0)
    data.setdefault("permission_mode", "auto")
    data.setdefault("expect", {})
    data["agent"] = agent
    _check_agent(path, data)
    _check_setup(path, data.get("setup"))
    _check_human_api(path, data)
    _check_human_run(path, data)
    for n, step in enumerate(data["steps"], 1):
        after = step.get("expect_after")
        if after is None:
            continue
        if step.get("human"):
            raise ValueError(
                f"{path}: step {n} 人間の操作の step には expect_after を付けられません"
            )
        extra = set(after) - {"decisions", "state"} if isinstance(after, dict) else None
        if extra is None:
            raise ValueError(
                f"{path}: step {n} expect_after は decisions と state を持つ dict です"
            )
        if extra:
            raise ValueError(
                f"{path}: step {n} expect_after に書けるのは decisions と state だけです: "
                + ", ".join(sorted(extra))
            )
    return data


def _check_agent(path, data):
    """代役で回すなら、人間の操作でない step はすべて script（打つ手の列）を持つ。"""
    if data["agent"] not in (AGENT_CLAUDE, AGENT_SCRIPT):
        raise ValueError(f"--agent は {AGENT_CLAUDE} か {AGENT_SCRIPT} です")
    for n, step in enumerate(data["steps"], 1):
        for key in ("script", "after_stop_block"):
            if key in step and not isinstance(step[key], list):
                raise ValueError(f"{path}: step {n} の {key} は手の list です")
        if data["agent"] == AGENT_SCRIPT and not step.get("human") and not step.get("script"):
            raise ValueError(f"{path}: step {n} 代役（--agent script）で回すには script が要ります")


def _check_human_api(path, data):
    """human step の human_api（人間役の API 操作）の形を確かめる。docker-gitlab 専用。"""
    named = set()
    host = (data.get("setup") or {}).get("host", hostenv.DEFAULT_HOST)
    for n, step in enumerate(data["steps"], 1):
        ops = step.get("human_api")
        if ops is None:
            continue
        where = f"{path}: step {n} human_api"
        if not step.get("human"):
            raise ValueError(f"{where} は human: true の step にだけ書けます")
        if host != hostenv.DOCKER:
            raise ValueError(
                f"{where} は setup.host: docker-gitlab のときだけ使えます（今は {host}）。"
                "setup.host を docker-gitlab にするか、"
                "human_api を消して instruction で手で操作する"
            )
        if not isinstance(ops, list) or not ops:
            raise ValueError(f"{where} は 1 件以上の操作の list です")
        for i, op in enumerate(ops, 1):
            if not isinstance(op, dict) or op.get("op") not in hostenv.HUMAN_OPS:
                raise ValueError(
                    f"{where} の {i} 件目: op は {' / '.join(hostenv.HUMAN_OPS)} のどれかです"
                )
            if op["op"] == "thread":
                if not isinstance(op.get("body"), str) or not op["body"].strip():
                    raise ValueError(f"{where} の {i} 件目: thread には body（本文）が要ります")
                if op.get("as") is not None:
                    if not isinstance(op["as"], str) or not op["as"].strip():
                        raise ValueError(f"{where} の {i} 件目: as は空でない文字列です")
                    named.add(op["as"])
            elif op["op"] == "resolve" and op.get("thread") not in named:
                raise ValueError(
                    f"{where} の {i} 件目: resolve の thread {op.get('thread')!r} は、"
                    "前の thread の as で名前を付けたものだけ指せます"
                )


def _check_human_run(path, data):
    """human step の human_run（人間役が端末で打つ sh）の形を確かめる。test_repo のシナリオ専用。"""
    for n, step in enumerate(data["steps"], 1):
        spec = step.get("human_run")
        if spec is None:
            continue
        where = f"{path}: step {n} human_run"
        if not step.get("human"):
            raise ValueError(f"{where} は human: true の step にだけ書けます")
        if step.get("human_api") is not None:
            raise ValueError(f"{where} は human_api と同じ step には書けません")
        if not data.get("test_repo"):
            raise ValueError(f"{where} は test_repo: true のシナリオにだけ書けます")
        if not isinstance(spec, dict) or spec.get("script") not in HUMAN_RUN_SCRIPTS:
            raise ValueError(f"{where}: script は {' / '.join(HUMAN_RUN_SCRIPTS)} のどれかです")
        extra = set(spec) - {"script", "answers"}
        if extra:
            raise ValueError(
                f"{where} に書けるのは script と answers だけです: " + ", ".join(sorted(extra))
            )
        answers = spec.get("answers", [])
        if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
            raise ValueError(f"{where}: answers は文字列の list です")
        if not str(step.get("instruction") or "").strip():
            raise ValueError(f"{where} には、端末を作れないときに手で打つ instruction が要ります")


def file_spec(value):
    """setup.files の値を (中身, 追記か) にする。文字列は従来どおり作って消す形。"""
    if isinstance(value, dict):
        return value["content"], bool(value.get("append"))
    return value, False


def check_git_argv(argv):
    """setup.git の 1 コマンドを検べる。問題があれば ValueError。"""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError("argv は空でない文字列の配列にする（シェルを介さない）")
    if argv[0] != "git":
        raise ValueError(f"先頭は git に限る: {argv[0]}")
    for a in argv[1:]:
        if a.startswith(GIT_ESCAPES):  # -C<パス>、--git-dir=<パス> も含む
            raise ValueError(f"{a} は対象の外へ出られるので書けない（-C・--git-dir・--work-tree）")
    for a in argv[1:]:
        if not a.startswith("-"):
            break  # サブコマンド。これより後ろの -c は、サブコマンドの引数（switch -c など）
        if a.startswith(GIT_GLOBAL_ESCAPES):
            raise ValueError(f"{a} は git の設定や実行ファイルを差し替えられるので書けない")


def _check_setup(path, setup):
    if setup is None:
        return
    if not isinstance(setup, dict):
        raise ValueError(f"{path}: setup は dict です")
    extra = set(setup) - SETUP_KEYS
    if extra:
        raise ValueError(
            f"{path}: setup に書けるのは {', '.join(sorted(SETUP_KEYS))} です: "
            + ", ".join(sorted(map(str, extra)))
        )
    appends = []
    files = setup.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError(f"{path}: setup.files は パス: 中身 の dict です")
    for rel, value in files.items():
        if isinstance(value, dict):
            bad = set(value) - {"content", "append"}
            if bad:
                raise ValueError(
                    f"{path}: setup.files[{rel}] に書けるのは content と append だけです: "
                    + ", ".join(sorted(map(str, bad)))
                )
            if not isinstance(value.get("content"), str):
                raise ValueError(f"{path}: setup.files[{rel}] の content は文字列で必須です")
            if "append" in value and not isinstance(value["append"], bool):
                raise ValueError(f"{path}: setup.files[{rel}] の append は true / false です")
            if value.get("append"):
                appends.append(rel)
        elif not isinstance(value, str):
            raise ValueError(f"{path}: setup.files[{rel}] は文字列か {{content, append}} です")
    git = setup.get("git")
    if git is not None:
        if not isinstance(git, list) or not git:
            raise ValueError(f"{path}: setup.git は argv の配列の配列です")
        for argv in git:
            try:
                check_git_argv(argv)
            except ValueError as exc:
                raise ValueError(f"{path}: setup.git {argv!r}: {exc}") from None
    reset = setup.get("reset")
    if reset is not None:
        if not isinstance(reset, dict) or set(reset) != {"base"}:
            raise ValueError(f"{path}: setup.reset は base だけを持つ dict です")
        base = reset["base"]
        if not isinstance(base, str) or not SHA40.fullmatch(base):
            raise ValueError(
                f"{path}: setup.reset.base は 40 桁の 16 進のコミット SHA にする"
                "（短縮 SHA・ブランチ名・タグ名は受けない）"
            )
        reset["base"] = base.lower()
    if (git or appends) and reset is None:
        raise ValueError(
            f"{path}: setup.git と setup.files の追記は、戻す setup.reset が無いと使えない"
        )
    hostenv.validate(path, setup)


def scenario_host(scn):
    """setup.host の値（bare / fake-gitlab / docker-gitlab / real）。欄が無ければ real。"""
    return (scn.get("setup") or {}).get("host", hostenv.DEFAULT_HOST)


def uses_target(scn):
    """使い捨てのリポジトリを触る setup（git・reset・追記・代役を使う相手）があるか。"""
    setup = scn.get("setup") or {}
    appends = any(file_spec(v)[1] for v in (setup.get("files") or {}).values())
    return bool(
        setup.get("git")
        or setup.get("reset")
        or appends
        or hostenv.uses_stub_git(scenario_host(scn))
    )


# human_run で実行器が人間役として打てる sh（ワークスペースルートからの相対）と、確認文に出す説明。
# argv は実行器が組み、yml には書かせない（本物のチケットの識別子や --root を書けないように）。
# 行を足すのは、必要なシナリオができたときだけ。
HUMAN_RUN_SCRIPTS = {
    "agree": (
        ".ccnavi/scripts/ccnavi-user-agree.sh",
        "この回で増えた test-repo の提案を承認し、push する",
    ),
}
