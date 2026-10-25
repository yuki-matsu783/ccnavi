"""GitLab API v4 の代役。`.ccnavi/scripts/ccnavi-review.sh` を実物なしで通すための的。

ユーザが手で 1 周させるための道具。本体は `tests/sh/fake_gitlab.py` にあり、
ここは起動するだけの入口。
自動テストは、その本体の `start` を import して起こす（受入シナリオからも同じ関数を呼べる）。

## なぜ要るか

実際の GitLab CE は 4GB 要る。手元の機械に無いことが普通にあり、無いままだと
sh の HTTP 経路（curl / gh / glab、jq での組み立て、ページング、投稿、認証）が
1 行も走らない。そこがいちばん実測できていない場所なので、GitLab と同じ形の JSON を
返すサーバを立てて、sh の側だけを実際に走らせる。

**これで分かるのは sh が動くことだけ。** 返す形が実物と同じかどうかは分からない。
実物で確かめる代わりにはならない。

## 使い方

    python tests/manual/gitlab/fake_gitlab.py <state.json> <port>

状態は JSON ファイル 1 つ。最初は次の形で置く。

    {"project": "demo/greeter", "seq": 100, "issues": [], "mrs": []}

`<port>` に 0 を渡すと OS が空きポートを決める。決まったポートは、起動時に標準エラーへ出る
`fake-gitlab listening on 127.0.0.1:<port> state=<state.json>` の行で分かる。
下の origin の設定は、ポートが分かってから行う。

対象のプロジェクトは、origin を `http://127.0.0.1:<port>/demo/greeter.git` にして、
push だけ手元の bare リポジトリへ向ける（`git remote set-url --push`）。
`GITLAB_TOKEN` に `TOKEN`（`fake-gitlab-token-0001`）を置くと、sh は curl 経路でここを呼ぶ。

人間役（issue を立てる、スレッドを立てる、解決する、変更要求を出す、マージする）は
curl で直に呼ぶ。sh と同じ道具を使わないほうが、片方の不具合がもう片方に隠れない。

## 時計をずらしてある

実際のホストと手元の時計は揃わない。揃っている前提の実装がすぐ動かなくなるので、
既定で 3 分進めてある（`skew`）。

## 関数として使う

自動テストと受入シナリオは、コマンドラインを使わず import して起こす。

    from tests.sh import fake_gitlab

    server = fake_gitlab.start(state_path, skew=None, initial_state={...})
    server.url      # http://127.0.0.1:<port>
    server.port     # OS が決めた空きポート
    server.token    # GITLAB_TOKEN に置く値
    server.requests # 受けた要求の記録（"GET /api/v4/..."）
    server.stop()

- ポートは OS に決めさせる。接続を受けられるのを確かめてから返る
  （上限 5 秒。超えたら `TimeoutError`）
- `initial_state` を渡すと `state_path` に書いてから起こす。
  渡さなければ `state_path` がすでにあること
- `skew` は `timedelta` か秒数。0 も負も渡せる
- `stop()` は、keep-alive の接続を開いたままの相手がいても返る。その接続も切る
- 状態・ロック・トークン・時計のずれはサーバが持つ。
  **同じプロセスで複数立てられる**（状態ファイルは別にする）
- 並列実行（何本同時に立てるか、状態ファイルをどこに置くか）は呼ぶ側が管理する
- 起動と停止は診断ログ `fake-gitlab` に残る（トークンは書かない）
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def load_body():
    """本体を、ファイルのパスから読む。`tests` パッケージ（PATH を変える）は読み込まない。"""
    src = os.path.join(ROOT, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    path = os.path.join(ROOT, "tests", "sh", "fake_gitlab.py")
    spec = importlib.util.spec_from_file_location("fake_gitlab_body", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(load_body().main())
