"""人間役の API 操作（human_api。層 4）。"""

from tests.scenarios import hostenv
from tests.scenarios.common import write_state


def run(out, st, idx, step, root):
    """human_api の操作を書いた順に実行して、止まらずに次の step へ進む。失敗したら 11。
    操作の結果は 1 件ごとに state に書く（再開しても同じ操作を二度実行しない）。"""
    print(f"\n== 人間役の操作（step {idx + 1}、API）==")
    st.setdefault("human_ops", [])
    st.setdefault("threads", {})
    for op in step["human_api"]:
        res = hostenv.human_op(root, st["host"], op, st["threads"])
        st["human_ops"].append({"step": idx + 1, **res})
        write_state(out, st)
        ok = 200 <= res["status"] < 300 and not res.get("error")
        detail = f"HTTP {res['status']}" if ok else res.get("error", "失敗")
        print(f"  {op_text(op)}: {'成功' if ok else '失敗'}（{detail}）")
        if not ok:
            st["next_step"] = idx + 1
            st["halt"] = {
                "step": idx + 1,
                "kind": "human_api",
                "reasons": [f"{op_text(op)}: {res.get('error', '失敗')}"],
            }
            write_state(out, st)
            print(f"step {idx + 1}: 人間役の操作が失敗した。後続の step には進まず止める")
            return 11
    st["next_step"] = idx + 1
    write_state(out, st)
    return None


def op_text(op):
    """操作の表示（種類と名前。本文は出さない）。"""
    name = op.get("as") or op.get("thread")
    return f"{op['op']}（{name}）" if name else op["op"]
