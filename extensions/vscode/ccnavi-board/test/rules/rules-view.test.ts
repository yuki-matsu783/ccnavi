import { test } from "node:test";
import assert from "node:assert/strict";
import { isToolName } from "../../src/core/rules-view.js";

test("CB-T532 ツール名は英字で始まり英数字・_・- だけを受け付ける（MCP の名前も通る）", () => {
  for (const name of ["Bash", "Read", "NotebookEdit", "mcp__github__get_me", "Tool-2", "A"]) {
    assert.equal(isToolName(name), true, name);
  }
});

test("CB-T533 空・先頭の数字や記号・空白・| や -（引数として読まれる形）は受け付けない", () => {
  for (const name of ["", " ", "2Tool", "_x", "-test", "--json", "Bash|Read", "Bash Read", "Bash;rm", "a\nb", "Bash\n", "Ｂash", "tool(x)"]) {
    assert.equal(isToolName(name), false, JSON.stringify(name));
  }
});
