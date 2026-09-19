/** 画面に中身を渡す段取り。どの状態で何が飛ぶか（拡張ホスト側。VS Code は要らない） */
import { test } from "node:test";
import assert from "node:assert/strict";
import { screenHost, type Surface } from "../../src/core/screen-host.js";

interface Spy extends Surface {
  visible: boolean;
  readonly pages: string[];
  readonly posted: unknown[];
}

function surface(visible = true): Spy {
  const pages: string[] = [];
  const posted: unknown[] = [];
  return {
    visible,
    pages,
    posted,
    html(text) {
      pages.push(text);
    },
    post(message) {
      posted.push(message);
    },
  };
}

test("CB-T143 1 枚目は入れ物ごと入れる。組み上がる前に渡したものは送らない", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => `<html>${data}</html>`);
  host.send("あ");
  assert.deepEqual(spy.pages, ["<html>あ</html>"]);
  assert.deepEqual(spy.posted, []);
  // 作り直している最中。受け口がまだ無いので送らない（入れ直しもしない。いま読み込んでいるものを捨てない）
  assert.equal(host.reloading, true);
  assert.equal(host.live, false);
  host.send("い");
  assert.deepEqual(spy.pages, ["<html>あ</html>"]);
  assert.deepEqual(spy.posted, []);
});

test("CB-T144 組み上がったら中身だけを送る。渡し直すのは受けた側", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);
  host.send("あ");
  host.ready();
  assert.equal(host.live, true);
  host.send("い");
  assert.deepEqual(spy.posted, [{ type: "data", data: "い" }]);
  assert.deepEqual(spy.pages, ["あ"], "入れ物は入れ直さない（画面が作り直されて状態が飛ぶ）");
});

test("CB-T145 裏に回ったら入れ物ごと入れ直す。表に戻っても、組み上がるまでは送らない", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);
  host.send("あ");
  host.ready();
  // 裏へ。画面は捨てられる
  spy.visible = false;
  host.hidden();
  assert.equal(host.live, false);
  assert.equal(host.reloading, false);
  host.send("い");
  assert.deepEqual(spy.pages, ["あ", "い"], "戻った瞬間に新しい中身が出るよう、入れておく");
  assert.deepEqual(spy.posted, []);
  // 表へ。VS Code が入れてある HTML から作り直す間は送らない
  spy.visible = true;
  assert.equal(host.reloading, true);
  host.send("う");
  assert.deepEqual(spy.pages, ["あ", "い"]);
  assert.deepEqual(spy.posted, []);
  // 組み上がったら届く
  host.ready();
  host.send("う");
  assert.deepEqual(spy.posted, [{ type: "data", data: "う" }]);
});

test("CB-T146 1 度きりのメッセージは、届いたときだけ真を返す", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);
  assert.equal(host.post({ type: "filter" }), false, "まだ 1 枚も入れていない");
  host.send("あ");
  assert.equal(host.post({ type: "filter" }), false, "作り直している最中");
  host.ready();
  assert.equal(host.post({ type: "filter" }), true);
  assert.deepEqual(spy.posted, [{ type: "filter" }]);
  spy.visible = false;
  host.hidden();
  assert.equal(host.post({ type: "filter" }), false, "裏に回った画面には届かない");
});
