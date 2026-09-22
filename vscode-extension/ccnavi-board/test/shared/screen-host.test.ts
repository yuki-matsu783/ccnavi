/** 画面に中身を渡す段取り。どの状態で何が飛ぶか（拡張ホスト側。VS Code は要らない） */
import { test } from "node:test";
import assert from "node:assert/strict";
import { retainedHost, screenHost, type Surface } from "../../src/core/screen-host.js";

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

test("CB-T143 1 枚目は入れ物ごと入れる。組み上がる前に渡したものは送らず、入れ直しもしない", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => `<html>${data}</html>`);
  assert.equal(host.send("あ"), "rebuilt");
  assert.deepEqual(spy.pages, ["<html>あ</html>"]);
  assert.deepEqual(spy.posted, []);
  // 作り直している最中。受け口がまだ無いので送らない（いま読み込んでいるものも捨てない）
  assert.equal(host.live, false);
  assert.equal(host.send("い"), "deferred");
  assert.deepEqual(spy.pages, ["<html>あ</html>"]);
  assert.deepEqual(spy.posted, []);
});

test("CB-T144 組み上がったら中身だけを送る。入れ物は入れ直さない", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);
  host.send("あ");
  host.ready();
  assert.equal(host.live, true);
  assert.equal(host.send("い"), "posted");
  assert.deepEqual(spy.posted, [{ type: "data", data: "い" }]);
  assert.deepEqual(spy.pages, ["あ"], "入れ直すと画面が作り直され、開いている中身も状態も飛ぶ");
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
  assert.equal(host.send("い"), "rebuilt", "戻った瞬間に新しい中身が出るよう、入れておく");
  assert.deepEqual(spy.pages, ["あ", "い"]);
  assert.deepEqual(spy.posted, []);
  // 表へ。VS Code が入れてある HTML から作り直す間は送らない
  spy.visible = true;
  assert.equal(host.live, false);
  assert.equal(host.send("う"), "deferred");
  assert.deepEqual(spy.pages, ["あ", "い"]);
  assert.deepEqual(spy.posted, []);
  // 組み上がったら届く
  host.ready();
  assert.equal(host.send("う"), "posted");
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
  // 裏に回った画面には届かない
  spy.visible = false;
  host.hidden();
  assert.equal(host.post({ type: "filter" }), false);
  assert.deepEqual(spy.posted, [{ type: "filter" }]);
});

test("CB-T147 裏にいる画面からの ready は、捨てられた画面の置き土産として捨てる", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);
  host.send("あ");
  // 画面が ready を送った直後に人がタブを裏へ回すと、裏に回ったことが先に届くことがある。
  // これを真に受けると、表に戻ったとき「組み上がっている」と誤って送ってしまう
  spy.visible = false;
  host.ready();
  spy.visible = true;
  assert.equal(host.live, false);
  assert.equal(host.send("い"), "deferred");
  assert.deepEqual(spy.posted, []);
  assert.equal(host.post({ type: "filter" }), false, "1 度きりの指示を、落ちる先へ送って消さない");
  // 本物の ready で届くようになる
  host.ready();
  assert.equal(host.send("い"), "posted");
  assert.equal(host.post({ type: "filter" }), true);
});

test("CB-T148 入れ物に埋めて渡すものは、入れ直す道のときだけ使う", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => `<html>${data}</html>`);
  assert.equal(host.send("素", "埋めた"), "rebuilt");
  assert.deepEqual(spy.pages, ["<html>埋めた</html>"]);
  host.ready();
  assert.equal(host.send("素", "埋めた"), "posted");
  assert.deepEqual(spy.posted, [{ type: "data", data: "素" }], "送る道では素のまま。埋めたほうは使わない");
});

test("CB-T149 裏に回ったと教えられたら、そのあいだ何も呼ばれなくても送らない", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);
  host.send("あ");
  host.ready();
  assert.equal(host.live, true);
  // 裏へ回って表へ戻るまでの間、この段取りは 1 度も呼ばれない（実機の onDidChangeViewState がその形）。
  // 教えてもらっていなければ、表裏を自分で読んでも行って戻ったことに気づけない
  spy.visible = false;
  host.hidden();
  spy.visible = true;
  assert.equal(host.live, false, "作り直している最中なので、まだ送れない");
  assert.equal(host.send("い"), "deferred");
  assert.equal(host.post({ type: "filter" }), false);
  assert.deepEqual(spy.posted, []);
  host.ready();
  assert.equal(host.send("い"), "posted");
});

test("CB-T150 教えてもらえなくても、表裏が変わっていれば気づく", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);
  host.send("あ");
  host.ready();
  // 裏に回ったことを誰も教えてくれない場合。裏にいる間に 1 度でも触れば、そこで気づく
  spy.visible = false;
  assert.equal(host.post({ type: "filter" }), false, "裏の画面には届かない");
  spy.visible = true;
  assert.equal(host.live, false, "戻ってきた画面は作り直されている");
  assert.equal(host.send("い"), "deferred");
  assert.deepEqual(spy.posted, []);
});

// ---- 保持する画面（retainContextWhenHidden: true）。裏でも生きているので段取りが変わる

test("CB-T151 保持する画面は入れ物を 1 度しか入れない。2 枚目からは中身だけ送る", () => {
  const spy = surface();
  const host = retainedHost<string>(spy, (data) => `<html>${data}</html>`);
  assert.equal(host.send("あ"), "rebuilt");
  assert.deepEqual(spy.pages, ["<html>あ</html>"]);
  // 組み上がるまでは受け口が無い。入れ直しもしない（読み込んでいるものを捨てない）
  assert.equal(host.live, false);
  assert.equal(host.send("い"), "deferred");
  assert.deepEqual(spy.pages, ["<html>あ</html>"]);
  host.ready();
  assert.equal(host.live, true);
  assert.equal(host.send("い"), "posted");
  assert.deepEqual(spy.pages, ["<html>あ</html>"], "入れ直すと打ちかけの編集が消える");
  assert.deepEqual(spy.posted, [{ type: "data", data: "い" }]);
});

test("CB-T152 保持する画面は裏に回っても入れ直さず、裏にいる間も送れる", () => {
  const spy = surface();
  const host = retainedHost<string>(spy, (data) => data);
  host.send("あ");
  host.ready();
  // 裏へ。VS Code は画面を捨てないので、送ったものは届く
  spy.visible = false;
  host.hidden();
  assert.equal(host.live, true);
  assert.equal(host.send("い"), "posted", "入れ直すと、裏で打ちかけていた編集が消える");
  assert.deepEqual(spy.pages, ["あ"]);
  assert.deepEqual(spy.posted, [{ type: "data", data: "い" }]);
  // 1 度きりのメッセージ（lock・changed）も裏のまま届く
  assert.equal(host.post({ type: "lock" }), true);
  spy.visible = true;
  assert.equal(host.send("う"), "posted", "表に戻っても作り直されていないので、そのまま送れる");
  assert.deepEqual(spy.pages, ["あ"]);
});

test("CB-T153 保持する画面でも、1 枚目が組み上がるまでは 1 度きりのメッセージを送らない", () => {
  const spy = surface();
  const host = retainedHost<string>(spy, (data) => data);
  assert.equal(host.post({ type: "lock" }), false, "まだ 1 枚も入れていない");
  host.send("あ");
  assert.equal(host.post({ type: "lock" }), false, "読み込んでいる最中。送っても落ちる");
  host.ready();
  assert.equal(host.post({ type: "lock" }), true);
  assert.deepEqual(spy.posted, [{ type: "lock" }]);
});

test("CB-T154 保持する画面も、入れ物に埋めて渡すものは 1 枚目にだけ使う", () => {
  const spy = surface();
  const host = retainedHost<string>(spy, (data) => `<html>${data}</html>`);
  assert.equal(host.send("素", "埋めた"), "rebuilt");
  assert.deepEqual(spy.pages, ["<html>埋めた</html>"]);
  host.ready();
  assert.equal(host.send("素", "埋めた"), "posted");
  assert.deepEqual(spy.posted, [{ type: "data", data: "素" }]);
});
