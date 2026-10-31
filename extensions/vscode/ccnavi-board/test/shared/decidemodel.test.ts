/**
 * 残った指摘の JSON（実行ファイルとの契約）の読み取り。`src/core/decidemodel.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { choicesProblem, parseDecidePreview, parseDecideResult } from "../../src/core/decidemodel.js";

const DIGEST = "v2.eyJiIjoiYiIsInQiOnsidTEiOiJ2In19";

function previewJson(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: 2,
    parent: "i0001",
    phase: 2,
    mr: { number: 7, url: "https://example.com/mr/7" },
    threads: [
      {
        key: "u1",
        url: "u1",
        path: "src/a.py",
        line: 3,
        body: "命名",
        version: "v".repeat(32),
        last_comment: { id: "c1", at: "2026-10-01T00:00:00Z", author: "me" },
      },
    ],
    digest: DIGEST,
    ...extra,
  });
}

const ASKED = { parent: "i0001", phase: 2, mr: 7 };

function thread(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { key: "u1", url: "https://example.com/mr/7#n1", path: "src/a.py", line: 3, body: "命名", version: "v".repeat(32), last_comment: { id: "c1", at: "t", author: "me" }, ...over };
}

function errorOf(text: string, expect = ASKED): string {
  const parsed = parseDecidePreview(text, expect);
  assert.equal(parsed.ok, false, text.slice(0, 160));
  return parsed.ok ? "" : parsed.error;
}

test("CB-T206 残った指摘の一覧を読む。正しく読めたものは版・親・フェーズ・MR・ダイジェスト・スレッドをそのまま持つ", () => {
  const parsed = parseDecidePreview(previewJson(), ASKED);
  assert.ok(parsed.ok);
  assert.equal(parsed.ok && parsed.value.threads[0].key, "u1");
  assert.equal(parsed.ok && parsed.value.phase, 2);
  assert.equal(parsed.ok && parsed.value.parent, "i0001");
  assert.equal(parsed.ok && parsed.value.mr.number, 7);
  assert.equal(parsed.ok && parsed.value.digest, DIGEST);
  assert.equal(parsed.ok && parsed.value.threads[0].version, "v".repeat(32));
  assert.equal(parsed.ok && parsed.value.threads[0].last_comment.id, "c1");
  // 頼んだものを渡さなければ、頼んだものとの照合だけを省く（型の検査はする）
  assert.ok(parseDecidePreview(previewJson()).ok);
  // MR の番号が分からないとき（null）は、番号の食い違いを見ない
  assert.ok(parseDecidePreview(previewJson({ mr: { number: 9, url: "" } }), { ...ASKED, mr: null }).ok);
  // 未解決が 0 件と正しく読めたときは、空の配列
  const zero = parseDecidePreview(previewJson({ threads: [] }), ASKED);
  assert.ok(zero.ok && zero.value.threads.length === 0);
  // URL が http(s) でないことはエラーにしない（画面がリンクにしないだけ）
  const odd = parseDecidePreview(previewJson({ threads: [thread({ url: "javascript:alert(1)" })], mr: { number: 7, url: "ftp://x" } }), ASKED);
  assert.ok(odd.ok && odd.value.threads[0].url === "javascript:alert(1)");
});

test("CB-T206b 読み取りの検証: JSON でない・最上位がマップでない・版が違うものは、件数を出さずにエラーにする", () => {
  assert.match(errorOf("not json"), /JSON として解釈できません/);
  assert.match(errorOf(""), /JSON として解釈できません/);
  assert.match(errorOf("[]"), /最上位がオブジェクトではありません/);
  assert.match(errorOf("null"), /最上位がオブジェクトではありません/);
  assert.match(errorOf(previewJson({ version: 1 })), /版が違います/);
  assert.match(errorOf(previewJson({ version: "2" })), /版が違います/);
  assert.match(errorOf(previewJson({ version: undefined })), /版が違います/);
});

test("CB-T206c 読み取りの検証: parent・phase・mr.number は、型が違っても頼んだものと違っても読み取れなかったことにする", () => {
  assert.match(errorOf(previewJson({ parent: 1 })), /parent が文字列ではありません/);
  assert.match(errorOf(previewJson({ parent: undefined })), /parent が文字列ではありません/);
  assert.match(errorOf(previewJson({ parent: "i0002" })), /parent が頼んだもの（i0001）と違います（i0002）/);
  assert.match(errorOf(previewJson({ phase: "2" })), /phase が整数ではありません/);
  assert.match(errorOf(previewJson({ phase: 2.5 })), /phase が整数ではありません/);
  assert.match(errorOf(previewJson({ phase: undefined })), /phase が整数ではありません/);
  assert.match(errorOf(previewJson({ phase: 3 })), /phase が頼んだもの（2）と違います（3）/);
  assert.match(errorOf(previewJson({ mr: { number: "7", url: "" } })), /mr\.number が整数ではありません/);
  assert.match(errorOf(previewJson({ mr: { number: 7.5, url: "" } })), /mr\.number が整数ではありません/);
  assert.match(errorOf(previewJson({ mr: null })), /mr\.number が整数ではありません/);
  assert.match(errorOf(previewJson({ mr: { url: "" } })), /mr\.number が整数ではありません/);
  assert.match(errorOf(previewJson({ mr: { number: 8, url: "" } })), /頼んだ依頼のマージリクエスト（#7）と違います（#8）/);
});

test("CB-T206d 読み取りの検証: digest は無い・文字列でない・v2. で始まらないとエラー", () => {
  assert.match(errorOf(previewJson({ digest: undefined })), /digest/);
  assert.match(errorOf(previewJson({ digest: 2 })), /digest/);
  assert.match(errorOf(previewJson({ digest: "" })), /digest/);
  assert.match(errorOf(previewJson({ digest: "v1.abc" })), /digest/);
  assert.match(errorOf(previewJson({ digest: "b".repeat(64) })), /digest/);
  assert.match(errorOf(previewJson({ digest: "xyz" })), /digest/);
  // 中身は読まない（不透明な文字列）。v2. で始まれば通す
  assert.ok(parseDecidePreview(previewJson({ digest: "v2.任意/の+文字列=" }), ASKED).ok);
});

test("CB-T206e 読み取りの検証: threads は無い・配列でないとエラー。要素が不正なら破棄せずに全体をエラーにする（0 件と読み間違えない）", () => {
  assert.match(errorOf(previewJson({ threads: undefined })), /threads が配列ではありません/);
  assert.match(errorOf(previewJson({ threads: {} })), /threads が配列ではありません/);
  assert.match(errorOf(previewJson({ threads: null })), /threads が配列ではありません/);
  const bad = (t: unknown) => errorOf(previewJson({ threads: [thread({ key: "ok" }), t] }));
  assert.match(bad("x"), /threads\[1\] がマップではありません/);
  assert.match(bad(null), /threads\[1\] がマップではありません/);
  assert.match(bad([]), /threads\[1\] がマップではありません/);
  assert.match(bad(thread({ key: "" })), /threads\[1\] の key が空か、文字列ではありません/);
  assert.match(bad(thread({ key: 5 })), /key が空か、文字列ではありません/);
  assert.match(bad(thread({ key: undefined })), /key が空か、文字列ではありません/);
  assert.match(bad(thread({ key: "ok" })), /threads\[1\] の key が重複しています（ok）/);
  for (const name of ["body", "url", "path"]) {
    assert.match(bad(thread({ key: "k2", [name]: 1 })), new RegExp(`threads\\[1\\] の ${name} が文字列ではありません`));
    assert.match(bad(thread({ key: "k2", [name]: undefined })), new RegExp(`${name} が文字列ではありません`));
    assert.match(bad(thread({ key: "k2", [name]: null })), new RegExp(`${name} が文字列ではありません`));
  }
  assert.match(bad(thread({ key: "k2", line: "3" })), /line が整数ではありません/);
  assert.match(bad(thread({ key: "k2", line: 3.5 })), /line が整数ではありません/);
  assert.match(bad(thread({ key: "k2", line: undefined })), /line が整数ではありません/);
  // 空文字の url・path と 0 の line は文字列・整数なので通る（場所の無い指摘）
  const none = parseDecidePreview(previewJson({ threads: [thread({ url: "", path: "", line: 0 })] }), ASKED);
  assert.ok(none.ok && none.value.threads.length === 1);
});

test("CB-T207 置いた答えを読む。食い違いは mismatch、ok が真でなければ置けたと読まない", () => {
  const ok = parseDecideResult(
    JSON.stringify({ version: 2, ok: true, parent: "i0001", phase: 2, reviewed: false, pending_extra: ["u9"], followup: "i0001-02-03", prompt: "文", issue_url: "", warning: "" }),
  );
  assert.ok(ok.ok);
  assert.equal(ok.ok && ok.value.followup, "i0001-02-03");
  assert.deepEqual(ok.ok && ok.value.pending_extra, ["u9"]);
  const mismatch = parseDecideResult(JSON.stringify({ version: 2, ok: false, mismatch: true }));
  assert.deepEqual(mismatch, { ok: false, mismatch: true });
  const unknown = parseDecideResult(JSON.stringify({ version: 2 }));
  assert.equal(unknown.ok, false);
  assert.ok(!unknown.ok && "error" in unknown);
});

test("CB-T208 対応方針は見せた指摘の全部に 1 つずつ。知らない対応方針は通さず、issue はいつでも通す", () => {
  const preview = parseDecidePreview(previewJson());
  assert.ok(preview.ok);
  if (!preview.ok) return;
  assert.equal(choicesProblem(preview.value, { u1: "keep" }), undefined);
  assert.notEqual(choicesProblem(preview.value, {}), undefined);
  assert.notEqual(choicesProblem(preview.value, { u1: "keep", u2: "keep" }), undefined);
  assert.notEqual(choicesProblem(preview.value, { u1: "later" }), undefined);
  // issue はいつでも選べる
  assert.equal(choicesProblem(preview.value, { u1: "issue" }), undefined);
});
