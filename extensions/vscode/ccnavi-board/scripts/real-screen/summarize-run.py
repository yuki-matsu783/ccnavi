"""`claude -p --output-format stream-json` の記録を、呼んだ操作と結果の頭だけに要約する。

    python3 summarize-run.py <記録.jsonl> [<結果の頭の文字数>]

拒否（hook の DENY_*）や、文書を引き直した場面（--docs・sed -n・grep）を探すときの入口。
"""

import json
import sys


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(part.get("text", "") for part in content)


def main() -> None:
    path = sys.argv[1]
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 350
    for line in open(path, encoding="utf-8"):
        event = json.loads(line)
        kind = event.get("type")
        if kind == "assistant":
            for part in event["message"]["content"]:
                if part["type"] == "text":
                    print("\n[AI]", part["text"][:900])
                elif part["type"] == "tool_use":
                    print("\n[CALL]", part["name"], json.dumps(part["input"], ensure_ascii=False)[:300])
        elif kind == "user" and isinstance(event["message"]["content"], list):
            for part in event["message"]["content"]:
                if part.get("type") == "tool_result":
                    print("  [RES]", text_of(part["content"])[:width].replace("\n", "\n        "))
        elif kind == "result":
            print("\n[RESULT]", event.get("result"))


if __name__ == "__main__":
    main()
