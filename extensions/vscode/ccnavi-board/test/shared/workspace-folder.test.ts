import { test } from "node:test";
import assert from "node:assert/strict";

import { multiFolderText, noFolderText } from "../../src/core/workspace-folder.js";

test("CB-T1248 フォルダが無いときと複数のときの案内は、画面の名前と最初のフォルダの名前を言う", () => {
  assert.equal(noFolderText("ルール管理画面"), "ワークスペースが開かれていないため、ルール管理画面を表示できません");
  assert.match(multiFolderText("app"), /最初のフォルダ app だけを対象にします/);
});
