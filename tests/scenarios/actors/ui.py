"""人間役の画面操作（human_ui。段 4）。足場。

ブラウザ（Playwright）で画面を操作する人間役の口。`api.py`・`terminal.py` と同じ
`run(out, st, idx, step, root)` の形を持つ。schema と drive にはまだつないでいない
（`human_ui` の step を書けるようにするのは、画面で確かめたいシナリオを足すとき）。

Playwright は任意の依存。無い環境でも、この module を import するだけなら失敗しない。
無いまま run を呼ぶと、後続の step に進まず止める（api と同じ 11）。
"""

from tests.scenarios.common import write_state


def load_playwright():
    """playwright.sync_api を返す。入っていなければ None。"""
    try:
        from playwright import sync_api
    except ImportError:
        return None
    return sync_api


def op_text(op):
    """操作の表示（種類と対象。入力した本文は出さない）。"""
    target = op.get("url") or op.get("selector")
    return f"{op['op']}（{target}）" if target else op["op"]


def _apply(page, op):
    kind = op["op"]
    if kind == "goto":
        page.goto(op["url"])
    elif kind == "click":
        page.click(op["selector"])
    elif kind == "fill":
        page.fill(op["selector"], op["text"])
    elif kind == "press":
        page.press(op["selector"], op["key"])
    else:
        raise ValueError(f"未知の画面操作: {kind}")


def _halt(out, st, idx, reason):
    st["next_step"] = idx + 1
    st["halt"] = {"step": idx + 1, "kind": "human_ui", "reasons": [reason]}
    write_state(out, st)
    print(f"step {idx + 1}: 人間役の画面操作が失敗した。後続の step には進まず止める（{reason}）")
    return 11


def run(out, st, idx, step, root):
    """human_ui の操作を書いた順に実行して、次の step へ進む。失敗したら 11。"""
    sync_api = load_playwright()
    if sync_api is None:
        return _halt(out, st, idx, "playwright が入っていない（uv run --with playwright で回す）")
    print(f"\n== 人間役の操作（step {idx + 1}、画面）==")
    st.setdefault("human_ops", [])
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # ブラウザ本体が無い（playwright install chromium の忘れ）など
            return _halt(
                out, st, idx, f"ブラウザを起動できない（playwright install chromium）: {e}"
            )
        try:
            page = browser.new_page()
            for op in step["human_ui"]:
                try:
                    _apply(page, op)
                except Exception as e:  # Playwright の例外型は任意依存なので絞らない
                    st["human_ops"].append({"step": idx + 1, "op": op["op"], "error": str(e)})
                    return _halt(out, st, idx, f"{op_text(op)}: {e}")
                st["human_ops"].append({"step": idx + 1, "op": op["op"], "status": 200})
                write_state(out, st)
                print(f"  {op_text(op)}: 成功")
        finally:
            browser.close()
    st["next_step"] = idx + 1
    write_state(out, st)
    return None
