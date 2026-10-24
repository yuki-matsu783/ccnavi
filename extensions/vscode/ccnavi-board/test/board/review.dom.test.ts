/**
 * レビュー完了の画面（残った指摘の読み取りと対応方針）。読み取れなかったときの表示、0 件の画面、相談のプロンプトのコピーの知らせ、
 * 表示した後に指摘が追加されたときの決め直し。遷移そのものは approval-machine.test.ts、読み取りの検証は decidemodel.test.ts。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { DecidePreview } from "../../src/core/decidemodel.js";
import { fixture } from "../helpers/fixture.js";
import { openBoard } from "../helpers/board.js";
import type { DomPage } from "../helpers/dom.js";

function preview(threads = 2): DecidePreview {
  return {
    version: 2,
    parent: "i0001",
    phase: 2,
    mr: { number: 7, url: "https://example.com/mr/7" },
    can_issue: true,
    threads: Array.from({ length: threads }, (_, i) => ({
      key: `u${i + 1}`,
      url: i === 0 ? "https://example.com/mr/7#n1" : "javascript:alert(1)",
      path: `src/a${i}.py`,
      line: i + 1,
      body: `指摘 ${i + 1}\n詳細`,
      version: "v".repeat(32),
      last_comment: { id: "", at: "", author: "" },
    })),
    digest: "v2.eyJiIjoiYSIsInQiOnt9fQ",
  };
}

const text = (page: DomPage, selector: string): string => page.one(selector).textContent;

test("CB-D730 読み取れなかったときは「指摘を読み取れませんでした（理由）」だけを出し、件数も確定のボタンも出さない", async () => {
  const page = await openBoard(fixture(), { approval: { kind: "error", error: "指摘を読み取れませんでした（threads が配列ではありません）" } });
  try {
    assert.equal(text(page, ".approval-note.error"), "指摘を読み取れませんでした（threads が配列ではありません）");
    assert.equal(page.all('button[data-action="decide-confirm"]').length, 0);
    assert.equal(page.all('button[data-action="decide-consult"]').length, 0);
    assert.equal(page.all(".decide-threads").length, 0);
    assert.ok(!page.one(".approval").textContent.includes("未解決の指摘はありません"), "0 件と読み間違えて見せない");
    assert.deepEqual(page.all(".approval-actions button").map((b) => b.textContent), ["閉じる"]);
  } finally {
    await page.close();
  }
});

test("CB-D731 対応方針の画面: URL は http(s) のときだけリンク。「全文」で全文を開ける。選択肢は同じ寸法の切り替え（ラジオのまま）", async () => {
  const page = await openBoard(fixture(), { approval: { kind: "decidePreview", preview: preview(), tree: "/w" } });
  try {
    assert.deepEqual(page.all("a.decide-url").map((a) => a.getAttribute("href")), ["https://example.com/mr/7#n1"]);
    // javascript: は文字として出すだけ
    assert.equal(text(page, 'li[data-key="u2"] .decide-url'), "javascript:alert(1)");
    assert.equal(page.all('li[data-key="u2"] a').length, 0);
    assert.equal(text(page, 'li[data-key="u1"] .decide-first'), "指摘 1");
    assert.equal(text(page, 'li[data-key="u1"] details.decide-more > summary'), "全文");
    // ラジオの意味: 同じグループ名・値・ラベルの文言は変えない。キーボード操作のため、input を display: none にしない
    const labels = page.all('li[data-key="u1"] label.decide-choice').map((l) => l.textContent.trim());
    assert.deepEqual(labels, ["対応しない", "このフェーズで直す", "issue に回す"]);
    assert.equal(page.all('li[data-key="u1"] input[type="radio"]').length, 3);
    assert.equal(page.all('li[data-key="u1"] [role="radiogroup"]').length, 1);
    // issue の注記は出さない
    assert.ok(!page.one(".approval").textContent.includes("issue に回せる"));
    assert.ok(!page.one(".approval").textContent.includes("フィードバック計画"));
  } finally {
    await page.close();
  }
});

test("CB-D732 相談のプロンプトのコピーの知らせ: できたら短く、できなかったら失敗と言う。選びかけの対応方針は保たれ、オーバーレイは閉じない", async () => {
  const page = await openBoard(fixture(), { approval: { kind: "decidePreview", preview: preview(), tree: "/w" } });
  try {
    page.click(page.one('li[data-key="u1"] input[data-choice="fix"]'));
    await page.settle();
    // できた（拡張ホストが同じ指摘のまま、コピーの結果を渡し直す）
    await page.send({ type: "data", data: { kind: "board", board: (await import("../../src/core/board.js")).buildBoard(fixture()), approval: { kind: "decidePreview", preview: preview(), tree: "/w", consult: { ok: true, text: "相談のプロンプトをコピーしました。エージェントに貼って送ってください" } } } });
    const ok = page.one('[data-consult="ok"]');
    assert.equal(ok.textContent, "相談のプロンプトをコピーしました。エージェントに貼って送ってください");
    assert.equal(ok.getAttribute("role"), "status");
    assert.equal((page.one('li[data-key="u1"] input[data-choice="fix"]') as unknown as { checked: boolean }).checked, true, "選びかけが保たれる");
    assert.equal(page.all(".approval-backdrop").length, 1);
    // できなかった
    await page.send({ type: "data", data: { kind: "board", board: (await import("../../src/core/board.js")).buildBoard(fixture()), approval: { kind: "decidePreview", preview: preview(), tree: "/w", consult: { ok: false, text: "コピーできませんでした（クリップボードを利用できません）" } } } });
    const ng = page.one('[data-consult="error"]');
    assert.match(ng.textContent, /コピーできませんでした/);
    assert.ok(ng.classList.contains("error"));
    assert.equal(page.all('[data-consult="ok"]').length, 0);
    assert.equal((page.one('li[data-key="u1"] input[data-choice="fix"]') as unknown as { checked: boolean }).checked, true, "選びかけが保たれる");
  } finally {
    await page.close();
  }
});

test("CB-D733 表示した後に指摘が追加されたとき: 「レビュー済みにしていません」と言い、［指摘を読み直して決め直す］で戻れる。続きの子が起きたときは出さない", async () => {
  const retry = await openBoard(fixture(), {
    approval: {
      kind: "prompt",
      title: "表示した後に指摘が追加されたため、レビュー済みにしていません",
      note: "今回の判断は適用済みです。",
      prompt: "[ccnavi] …",
      what: "対応方針のプロンプト",
      keep: false,
      retry: { parent: "i0001", phase: 2 },
    },
  });
  try {
    assert.equal(text(retry, "#approval-title"), "表示した後に指摘が追加されたため、レビュー済みにしていません");
    const button = retry.one('button[data-action="decide-retry"]');
    assert.equal(button.textContent, "指摘を読み直して決め直す");
    retry.click(button);
    await retry.settle();
    assert.deepEqual(retry.posted.at(-1), { type: "decide", parent: "i0001", phase: 2 });
    // コピー・新しいセッションで開く・閉じるも並ぶ
    assert.ok(retry.all('button[data-action="prompt-copy"]').length === 1 && retry.all('button[data-action="prompt-open"]').length === 1);
  } finally {
    await retry.close();
  }
  const kept = await openBoard(fixture(), { approval: { kind: "prompt", title: "決めました", note: "n", prompt: "p", keep: true } });
  try {
    assert.equal(kept.all('button[data-action="decide-retry"]').length, 0);
  } finally {
    await kept.close();
  }
});
