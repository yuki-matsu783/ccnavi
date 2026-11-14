// `claude -p --output-format stream-json` の記録を、呼んだ操作と結果の頭だけに要約する。
//
//   node scripts/real-screen/summarize-run.js <記録.jsonl> [<結果の頭の文字数>]
//
// 拒否（hook の DENY_*）や、文書を引き直した場面（--docs・sed -n・grep）を探すときの入口。
"use strict";

const fs = require("node:fs");

// 文字数で切る（サロゲートペアを割らない）。
function head(text, n) {
  return Array.from(text).slice(0, n).join("");
}

function textOf(content) {
  if (typeof content === "string") return content;
  return content.map((part) => part.text ?? "").join(" ");
}

function main() {
  const [file, widthArg] = process.argv.slice(2);
  if (!file) {
    console.error("使い方: node summarize-run.js <記録.jsonl> [<結果の頭の文字数>]");
    process.exit(1);
  }
  const width = widthArg === undefined ? 350 : Number(widthArg);
  if (!Number.isInteger(width)) {
    console.error(`結果の頭の文字数が整数ではありません: ${widthArg}`);
    process.exit(1);
  }
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      // timeout で殺されて最後の行が欠けたときも、読めたところまで要約する
      continue;
    }
    if (event.type === "assistant") {
      for (const part of event.message.content) {
        if (part.type === "text") {
          console.log("\n[AI]", head(part.text, 900));
        } else if (part.type === "tool_use") {
          console.log("\n[CALL]", part.name, head(JSON.stringify(part.input), 300));
        }
      }
    } else if (event.type === "user" && Array.isArray(event.message.content)) {
      for (const part of event.message.content) {
        if (part.type === "tool_result") {
          console.log("  [RES]", head(textOf(part.content), width).replaceAll("\n", "\n        "));
        }
      }
    } else if (event.type === "result") {
      console.log("\n[RESULT]", event.result);
    }
  }
}

main();
