import { test } from "node:test";
import assert from "node:assert/strict";
import { isToolName, tmpName, TMP_RULES_PREFIX } from "../../src/core/rules-view.js";

test("CB-T555 ツール名は英字で始まり英数字・_・- だけを受け付ける（MCP の名前も通る）", () => {
  for (const name of ["Bash", "Read", "NotebookEdit", "mcp__github__get_me", "Tool-2", "A"]) {
    assert.equal(isToolName(name), true, name);
  }
});

test("CB-T556 空・先頭の数字や記号・空白・| や -（引数として読まれる形）は受け付けない", () => {
  for (const name of ["", " ", "2Tool", "_x", "-test", "--json", "Bash|Read", "Bash Read", "Bash;rm", "a\nb", "Bash\n", "Ｂash", "tool(x)"]) {
    assert.equal(isToolName(name), false, JSON.stringify(name));
  }
});

test("CB-T1226 tmpName は編集中の一時ファイルのパスだけを消す（相対・Windows の絶対パス・id に : を含む・複数・共通レイヤーはそのまま）", () => {
  assert.equal(tmpName("../../../tmp/ccnavi-rules-aB3dE9/rules.yml:no-rm"), "rules.yml:no-rm");
  assert.equal(tmpName("C:\\Users\\a\\AppData\\Local\\Temp\\ccnavi-rules-aB3dE9\\rules.yml:no-rm"), "rules.yml:no-rm");
  assert.equal(tmpName("..\\..\\Temp\\ccnavi-rules-aB3dE9\\rules.yml:a:b"), "rules.yml:a:b");
  assert.equal(
    tmpName("[ccnavi] X (rule: ../../tmp/ccnavi-rules-aB3dE9/rules.yml:x) と ../t/ccnavi-rules-zzzzzz/rules.yml:y"),
    "[ccnavi] X (rule: rules.yml:x) と rules.yml:y",
  );
  assert.equal(tmpName(".ccnavi/config/common/rules.yml:no-rm"), ".ccnavi/config/common/rules.yml:no-rm");
  assert.equal(TMP_RULES_PREFIX, "ccnavi-rules-");
});
