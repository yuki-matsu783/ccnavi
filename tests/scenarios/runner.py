"""受入シナリオを claude -p で回す実行器。手順と決まりは .agents/docs/acceptance-scenarios.md。

普段のテストには含めない。人間に確認を取ってから、明確な指示があったときだけ回す。

    uv run python -m tests.scenarios.runner preflight <シナリオ>
    uv run python -m tests.scenarios.runner run <シナリオ>
    uv run python -m tests.scenarios.runner run <シナリオ> --confirmed
    uv run python -m tests.scenarios.runner run-all <ディレクトリ or glob> --max-total-usd <USD>
    uv run python -m tests.scenarios.runner run-all <...> --max-total-usd <USD> --confirmed <目印>
    uv run python -m tests.scenarios.runner next <出力の置き場>
    uv run python -m tests.scenarios.runner restore <出力の置き場>
    uv run python -m tests.scenarios.runner judge <出力の置き場> <判定の表のファイル> [--accept]

run-all は、使い捨てのリポジトリのシナリオ（人間の操作が無く、setup.reset を持つもの）だけを、
1 回の確認で直列に回す。--confirmed を付けずに打つと、回す ID の一覧・概要・base・費用の上限と
目印を出して止まる（終了コード 3）。目印は ID の集合・base・費用の上限・概要から作る。
実行の直前に作り直し、合わなければ止まる。実費の累計が --max-total-usd を超えそうなら、
次のシナリオは回さずに止まる。終了コードは 0 全部 pass / 1 fail がある・回せるシナリオが無い /
2 未成立か意味の判定待ちがある・上限が不正 / 3 確認が要る・止まった。

next は、人間の操作で止まったあと、人間が「次へ」と言ってから打つ。
restore は、人間の操作で止まった回を続けずにやめるとき、setup のファイルを消し、
setup.host の代役の置き場を消し、使い捨てのリポジトリを setup.reset の base へ戻して、
残し印を外す。next は、setup.host が fake-gitlab なら模擬を残した状態ファイルのまま起こし直す。

--confirmed を付けるのは、人間が「状態がきれいで最新か」（と、test_repo のシナリオでは
「test-repo にブランチや MR ができてよいか」）に答えたあとだけ。
付けずに打つと、確認に使う事実を出して止まる（終了コード 3）。
ccnavi 本体にブランチ・コミット・ワークツリーが残らないことは、
回す前後の状態を比べて確かめる（repo）。

終了コード: 0 全部通った / 1 不合格がある・setup のパスが合わず回せない /
2 未成立か意味の判定待ちがある /
3 人間の確認が要る・回す前の拒否・使い捨てのリポジトリを戻せなかった（手作業が要る） /
10 人間の操作で止まった（「次へ」のあと next で続ける。setup.reset の対象と setup.host の
置き場は残し、模擬だけ止める）。

判定は 3 つ。機械で比べられるもの（ccnavi の判定の記録、ファイルの最終状態、
コマンドの出力）は pass / fail。期待した呼び出しをエージェントが打たなかったものは
inconclusive（未成立。fail にしない）。意味で読むもの（judge）は、evidence.md に証拠を
抜き出し、別のエージェントが基準に照らして読む。

中身は責務ごとのモジュールに分けてある（段の低い方から）。import は同じ段か下へだけ向ける。
0 common・schema・stream・hostenv・hostsnap / 1 expect・evidence・workspace・issues /
2 repo / 3 prepare / 4 actors/ / 5 report / 6 drive / 7 commands / 8 この入口。
"""

import argparse
import sys

from tests.scenarios import commands
from tests.scenarios.common import ROOT, enable_entry


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--confirmed", action="store_true")
    r.add_argument("--root", default=str(ROOT))
    r.add_argument("--out")
    r.set_defaults(fn=commands.cmd_run)
    ra = sub.add_parser("run-all")
    ra.add_argument("scenarios", nargs="+", help="ディレクトリ・glob・シナリオのファイル")
    ra.add_argument("--max-total-usd", type=float, required=True, help="実費の累計の上限")
    ra.add_argument("--confirmed", help="確認文が出した目印（ID の集合・base・費用の上限・概要）")
    ra.add_argument("--root", default=str(ROOT))
    ra.add_argument("--out", help="要約（summary.md / summary.json）の置き場")
    ra.set_defaults(fn=commands.cmd_run_all)
    p = sub.add_parser("preflight")
    p.add_argument("scenario")
    p.add_argument("--root", default=str(ROOT))
    p.set_defaults(fn=commands.cmd_preflight)
    j = sub.add_parser("judge")
    j.add_argument("out")
    j.add_argument(
        "file",
        nargs="?",
        help="判定の表（| 基準 | 判定 | 理由 |）のファイル。足し済みの回の受け入れには要らない",
    )
    j.add_argument("--accept", action="store_true", help="ユーザが判定を受け入れた")
    j.set_defaults(fn=commands.cmd_judge)
    n = sub.add_parser("next")
    n.add_argument("out")
    n.set_defaults(fn=commands.cmd_next)
    rs = sub.add_parser("restore")
    rs.add_argument("out")
    rs.set_defaults(fn=commands.cmd_restore)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    enable_entry()
    sys.exit(main())
