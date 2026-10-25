/**
 * ボード画面が何を描くか。React の画面を happy-dom で動かし、出来上がった DOM を見る。
 * 操作の続き（折りたたむ・絞り込み・承認の送り先）は board.dom.test.ts。
 *
 * CSS はバンドルした 1 本が `<style nonce>` に入っているので、規則そのものを見たいところは
 * `flatStyle()`（1 行にまとめた CSS）を見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../../src/core/board.js";
import type { ParentJson, PhaseJson, TicketJson } from "../../src/core/model.js";
import type { DecidePreview } from "../../src/core/decidemodel.js";
import { fixture } from "../helpers/fixture.js";
import { NONCE, approvePreview, boardPage, openBoard, openPage } from "../helpers/board.js";
import { flatStyle } from "../helpers/bundle.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

/** 見本のボードの HTML。nonce を文字列で見るときに使う */
function html(): string {
  return boardPage({ kind: "board", board: buildBoard(fixture()) });
}

/** 見本のボードの CSS（1 行にまとめたもの）。規則そのものを見るときに使う */
function css(): string {
  return flatStyle(html());
}

function text(page: DomPage, selector: string): string {
  return page.one(selector).textContent;
}

function texts(page: DomPage, selector: string): string[] {
  return page.all(selector).map((element) => element.textContent);
}

test("CB-T107 承認のオーバーレイに一覧・本文・対象外を出し、見せた識別子を承認ボタンに持たせる", async () => {
  const preview = approvePreview();
  const page = await openBoard(fixture(), { approval: { kind: "preview", preview } });
  try {
    assert.equal(page.one(".approval-backdrop").getAttribute("data-approval"), "preview");
    // 定義の範囲を超える子（i0001-01-02）は承認を止めないので一覧に載り、超過は本文の見出しに出る。
    assert.equal(text(page, "#approval-title"), "承認待ちのチケット 3 件");
    assert.deepEqual(texts(page, ".approval-batch td.approval-id"), ["i0001", "i0001-01-01", "i0001-01-02"]);
    const confirm = page.one('button[data-action="approve-confirm"]');
    assert.equal(confirm.getAttribute("data-tickets"), "i0001,i0001-01-01,i0001-01-02");
    assert.equal(confirm.textContent, "この 3 件を承認する");
    // 見せた識別子をそのまま送る（属性に持たせるだけでなく、押したときの中身も見る）
    page.click(confirm);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), {
      type: "approveConfirm",
      tickets: ["i0001", "i0001-01-01", "i0001-01-02"],
      digest: preview.digest,
    });
    assert.equal(page.all('button[data-action="approve-cancel"]').length, 1);
    const body = text(page, "pre.approval-text");
    assert.ok(body.startsWith("チケットの承認リクエスト"));
    assert.ok(body.includes("編集対象としているが"));
    assert.ok(body.includes("超えている"));
    // 対象にしないのは形の正しくない子（計画に無い番号）。計画の図の見出し（`.plan-graph h3`）は数えない
    assert.deepEqual(texts(page, ".approval > h3"), ["承認対象外の提案"]);
    assert.ok(text(page, ".approval-rejected").includes("i0001-05-05"));
    assert.ok(text(page, ".approval-rejected").includes("計画にありません"));
    assert.equal(page.all(".approval-problems").length, 0);
    // 承認の操作の列は共通の Dialog 部品の列に含まれる
    assert.ok(page.all(".approval .overlay-actions.approval-actions").length > 0);
  } finally {
    await page.close();
  }
  // 狭い幅でも表は枠に収め（識別子だけは折らない）、ボタンの行は折り返す
  assert.match(css(), /\.approval-batch th, \.approval-batch td \{[^}]*overflow-wrap: anywhere; \}/);
  assert.match(css(), /\.approval-id \{[^}]*white-space: nowrap; \}/);
  // 操作の列は共通の Dialog 部品（Dialog.css）が持つ。承認のボタンもその列に含まれる
  assert.match(css(), /\.overlay-actions \{ display: flex; flex-wrap: wrap;/);
  // 本文に何が書かれていても、文字として出すだけ。
  const spiked = await openBoard(fixture(), { approval: { kind: "preview", preview: { ...preview, text: "<script>alert(1)</script>" } } });
  try {
    assert.equal(text(spiked, "pre.approval-text"), "<script>alert(1)</script>");
    assert.equal(spiked.all("pre.approval-text script").length, 0);
  } finally {
    await spiked.close();
  }
});

function decidePreview(canIssue: boolean): DecidePreview {
  return {
    version: 2,
    parent: "i0001",
    phase: 2,
    mr: { number: 7, url: "https://example.com/mr/7" },
    can_issue: canIssue,
    threads: [
      { key: "u1", url: "u1", path: "src/a.py", line: 3, body: "命名を直す\n詳しく", version: "v".repeat(32), last_comment: { id: "", at: "", author: "" } },
      { key: "u2", url: "u2", path: "", line: 0, body: "<b>テスト</b>", version: "v".repeat(32), last_comment: { id: "", at: "", author: "" } },
    ],
    digest: "v2.eyJiIjoiYSIsInQiOnt9fQ",
  };
}

test("CB-T205 残った指摘は既定で全部「このフェーズで直す」。1 件ずつ・一括で選び直せる。選んだ対応方針をキーごとに送る", async () => {
  const page = await openBoard(fixture(), {
    approval: { kind: "decidePreview", preview: decidePreview(false), tree: "/w/.claude/worktrees/i0001" },
  });
  try {
    assert.equal(page.one(".approval-backdrop").getAttribute("data-approval"), "decidePreview");
    assert.equal(text(page, "#approval-title"), "フェーズ 2 で未解決（Unresolved）の指摘 2 件の対応方針");
    assert.ok(texts(page, ".approval-note").join(" ").includes("「このフェーズで直す」を 1 件でも選ぶと"));
    // 本文は 1 行目を出し、2 行以上あれば <details> で全文。HTML は文字として出す
    assert.deepEqual(texts(page, ".decide-first"), ["命名を直す", "<b>テスト</b>"]);
    assert.equal(page.all('li[data-key="u1"] details.decide-more').length, 1);
    assert.equal(page.all('li[data-key="u2"] details.decide-more').length, 0, "1 行だけの本文には「全文」を出さない");
    assert.equal(text(page, 'li[data-key="u1"] .decide-full'), "命名を直す\n詳しく");
    assert.equal(page.all(".decide-body b").length, 0);
    // 指摘の URL は http(s) のときだけリンクにする（そうでなければ文字として出すだけ）
    assert.equal(page.all("a.decide-url").length, 0);
    // 対応方針の選択肢は同じ寸法の切り替え。ラジオの意味（同じ name のグループ）とキーボード操作はそのまま
    const group = page.one('li[data-key="u1"] [role="radiogroup"]');
    assert.equal(group.querySelectorAll("label.decide-choice").length, 3);
    assert.equal(new Set(page.all('li[data-key="u1"] input[type="radio"]').map((r) => r.getAttribute("name"))).size, 1);
    // 相談のプロンプトのコピー（1 件以上のときだけ）。押してもオーバーレイは閉じず、選びかけも変えない
    const consult = page.one<HTMLButtonElement>('button[data-action="decide-consult"]');
    assert.equal(consult.textContent, "エージェントに相談するプロンプトをコピー");
    assert.equal(consult.disabled, false);
    // issue に回す行き先は、局面（フィードバック計画の承認の有無）に関わらず出す
    assert.equal(page.all('input[data-choice="issue"]').length, 2);
    assert.ok(!texts(page, ".approval-note").join(" ").includes("フィードバック計画"));
    assert.equal(page.all('input[data-choice="keep"]').length, 2);
    // 何も選ばなければ全部「このフェーズで直す」で、そのまま確定できる
    const checked = (sel: string): boolean => (page.one(sel) as unknown as { checked: boolean }).checked;
    assert.equal(checked('li[data-key="u1"] input[data-choice="fix"]'), true);
    assert.equal(checked('li[data-key="u2"] input[data-choice="fix"]'), true);
    assert.equal(page.one<HTMLButtonElement>('button[data-action="decide-confirm"]').disabled, false);
    page.click(page.one('li[data-key="u1"] input[data-choice="keep"]'));
    await page.settle();
    page.click(consult);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "decideConsult" });
    assert.equal(checked('li[data-key="u1"] input[data-choice="keep"]'), true, "選びかけの対応方針は保たれる");
    // 一括: 全部を同じ対応方針にする（個別の選択は上書きされる）
    page.click(page.one('button[data-bulk="issue"]'));
    await page.settle();
    assert.equal(checked('li[data-key="u1"] input[data-choice="issue"]'), true);
    assert.equal(checked('li[data-key="u2"] input[data-choice="issue"]'), true);
    page.click(page.one('li[data-key="u2"] input[data-choice="keep"]'));
    await page.settle();
    const ready = page.one<HTMLButtonElement>('button[data-action="decide-confirm"]');
    assert.equal(ready.disabled, false);
    page.click(ready);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "decideConfirm", choices: { u1: "issue", u2: "keep" } });
  } finally {
    await page.close();
  }
  const issued = await openBoard(fixture(), {
    approval: { kind: "decidePreview", preview: decidePreview(true), tree: "/w" },
  });
  try {
    assert.equal(issued.all('input[data-choice="issue"]').length, 2);
  } finally {
    await issued.close();
  }
  // 置いている最中は選べず、決められず、やめられない
  const deciding = await openBoard(fixture(), {
    approval: { kind: "deciding", preview: decidePreview(false), tree: "/w" },
  });
  try {
    assert.equal(deciding.one<HTMLButtonElement>('button[data-action="decide-confirm"]').disabled, true);
    assert.equal(deciding.one<HTMLButtonElement>('button[data-action="approve-cancel"]').disabled, true);
    assert.equal(deciding.one<HTMLButtonElement>('button[data-action="decide-consult"]').disabled, true, "確定中は相談のコピーを押せない");
    assert.ok(deciding.all("input[type=radio]").every((r) => (r as unknown as { disabled: boolean }).disabled));
  } finally {
    await deciding.close();
  }
  // 未解決が 0 件なら、選び方の注意（「このフェーズで直す」を選ぶと…）は出さない
  const none = await openBoard(fixture(), {
    approval: { kind: "decidePreview", preview: { ...decidePreview(false), threads: [] }, tree: "/w" },
  });
  try {
    assert.equal(text(none, "#approval-title"), "フェーズ 2 に未解決の指摘はありません");
    assert.equal(none.all('button[data-action="decide-consult"]').length, 0, "指摘が 0 件なら相談のボタンは出さない");
    assert.ok(none.one('button[data-action="decide-confirm"]').classList.contains("primary"));
    const notes = texts(none, ".approval-note").join(" ");
    assert.ok(!notes.includes("このフェーズで直す"), notes);
    assert.ok(!notes.includes("issue に回せる"), notes);
    assert.equal(none.one<HTMLButtonElement>('button[data-action="decide-confirm"]').textContent, "レビュー済みにする");
  } finally {
    await none.close();
  }
});

test("CB-D73 説明の付く見出しは次の行をツールチップに畳み、知らない見出しは畳まない", async () => {
  const page = await openBoard(fixture(), { approval: { kind: "preview", preview: approvePreview() } });
  try {
    const heads = page.all(".approval-head");
    const labels = heads.map((head) => head.textContent);
    // 説明のある見出しだけが折りたたまれる。「エージェントが記載した理由」の下は本文なので折りたたまない。
    assert.ok(labels.includes("■ このチケットで編集可能な範囲"), labels.join(" / "));
    assert.ok(labels.includes("■ チケットで編集対象としているが、書き込めない場所"), labels.join(" / "));
    assert.ok(!labels.includes("■ エージェントが記載した理由"), labels.join(" / "));
    const scope = heads.find((head) => head.textContent === "■ このチケットで編集可能な範囲");
    assert.match(scope?.getAttribute("title") ?? "", /allow は確認なしで編集できる場所/);
    // 折りたたんだ説明は目には出さないが、読み上げに渡すので DOM には残る。
    const hints = texts(page, ".approval-hint").join(" ");
    assert.ok(hints.includes("allow は確認なしで編集できる場所"), hints);
    // 要約は表の上、凡例は本文の外に出る。本文の語は置き換えない
    assert.match(text(page, "[data-approval-summary]"), /^承認するチケット 3 件/);
    assert.match(text(page, "[data-approval-summary-plan]"), /全体計画 2 ステップ（調査・設計）/);
    const legend = text(page, "[data-approval-legend]");
    for (const word of ["allow", "ask", "deny", "after", "scope", "review"]) {
      assert.ok(legend.includes(word), legend);
    }
    // 本文そのものは削らない。理由の中身は見出しの下にそのまま出る。
    const body = text(page, "pre.approval-text");
    assert.ok(body.includes("■ エージェントが記載した理由"), body);
  } finally {
    await page.close();
  }
});

test("CB-T108 承認の対象が空なら承認ボタンを出さず、承認中はボタンを押せず、食い違いの注意を出す", async () => {
  const preview = approvePreview();
  const empty = await openBoard(fixture(), {
    approval: { kind: "preview", preview: { ...preview, batch: [], text: "承認待ちのチケットはありません。" } },
  });
  try {
    assert.equal(text(empty, "#approval-title"), "承認待ちのチケットなし");
    // 実行ファイルの本文（文末に句点が付く文）はそのまま出す。画面のラベルとは違うもの。
    assert.equal(text(empty, "pre.approval-text"), "承認待ちのチケットはありません。");
    assert.equal(empty.all('button[data-action="approve-confirm"]').length, 0);
  } finally {
    await empty.close();
  }
  const approving = await openBoard(fixture(), { approval: { kind: "approving", preview } });
  try {
    assert.equal(approving.one(".approval-backdrop").getAttribute("data-approval"), "approving");
    const confirm = approving.one<HTMLButtonElement>('button[data-action="approve-confirm"]');
    assert.equal(confirm.textContent, "承認中…");
    assert.equal(confirm.disabled, true);
    assert.equal(approving.one<HTMLButtonElement>('button[data-action="approve-cancel"]').disabled, true);
  } finally {
    await approving.close();
  }
  const noticed = await openBoard(fixture(), { approval: { kind: "preview", preview, notice: "見せた一覧と今の一覧が違った" } });
  try {
    assert.equal(text(noticed, ".approval-note.warn"), "見せた一覧と今の一覧が違った");
  } finally {
    await noticed.close();
  }
  const failed = await openBoard(fixture(), { approval: { kind: "error", error: "実行ファイルがありません" } });
  try {
    assert.equal(text(failed, ".approval-note.error"), "実行ファイルがありません");
  } finally {
    await failed.close();
  }
});

test("CB-T108b 承認したら同じオーバーレイに文とコピー・新しいセッションで開く・ターミナルで実行・閉じるを出す", async () => {
  const page = await openBoard(fixture(), { approval: { kind: "done", count: 1, prompt: "i0001-02-03 を承認した <b>" } });
  try {
    assert.equal(page.one(".approval-backdrop").getAttribute("data-approval"), "done");
    assert.equal(text(page, "#approval-title"), "1 件を承認しました");
    // 文は文字として出す。<b> がタグにならない
    assert.equal(text(page, "pre.approval-text"), "i0001-02-03 を承認した <b>");
    assert.equal(page.all("pre.approval-text b").length, 0);
    // 文は Webview から送らせない。拡張が持っている文を使うので、押したことだけを伝える
    page.click(page.one('button[data-action="prompt-copy"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "promptCopy" });
    page.click(page.one('button[data-action="prompt-open"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "promptOpen" });
    page.click(page.one('button[data-action="prompt-run"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "promptRun" });
    assert.equal(page.all('button[data-action="approve-cancel"]').length, 1);
    // 承認の push の sh をターミナルに送ったときだけ、そう言う。
    assert.ok(!texts(page, ".approval-note").some((note) => note.includes("ターミナルに送りました")));
  } finally {
    await page.close();
  }
  const carried = await openBoard(fixture(), { approval: { kind: "done", count: 1, prompt: "i0001-02-03 を承認した", carried: true } });
  try {
    assert.ok(texts(carried, ".approval-note").includes("承認済みチケットをコミット・push するコマンドをターミナルに送信しました。"));
  } finally {
    await carried.close();
  }
});

test("CB-T108c カードの承認はそのカードの識別子だけを絞りとして送る", async () => {
  const pending = fixture().pending_approval[0];
  const page = await openBoard();
  try {
    const one = page.one(`button[data-action="approve-one"][data-ticket="${pending}"]`);
    assert.equal(one.textContent, "承認");
    page.click(one);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: [pending], filtered: true });
    // 上部のボタンは今までどおり見えている承認待ち全部。
    page.click(page.one('.controls button[data-action="approve"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: [pending], filtered: false });
  } finally {
    await page.close();
  }
});

test("CB-T109 オーバーレイを渡さなければ出ない", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.all(".approval-backdrop").length, 0);
    assert.equal(page.all('button[data-action="approve-confirm"]').length, 0);
    assert.equal(page.all('.controls button[data-action="approve"]').length, 1);
  } finally {
    await page.close();
  }
});

test("CB-T12 4 列と件数と承認ボタンを出す", async () => {
  const page = await openBoard();
  try {
    assert.deepEqual(texts(page, ".column button.fold .label"), ["未着手", "作業中", "完了", "取り消し"]);
    assert.equal(page.all(".column").length, 4);
    assert.equal(text(page, ".summary .counts"), "残り 4 / 全 6");
    assert.equal(text(page, ".summary .pending"), "承認待ち 1 件");
    // 色が付くのは warn を持つものだけ（.summary .warn）
    assert.ok(page.one(".summary .pending").classList.contains("warn"));
    // 0 件のものは見出しに出さない
    assert.equal(page.all(".summary .issues").length, 0);
    const approve = page.one<HTMLButtonElement>('.controls button[data-action="approve"]');
    assert.equal(approve.textContent, "承認待ち 1 件を承認");
    assert.equal(approve.disabled, false);
  } finally {
    await page.close();
  }
  // nonce は style と script の両方に付く（CSP が nonce だけを通す）
  assert.ok(html().includes(`<style nonce="${NONCE}">`));
  assert.ok(html().includes(`<script nonce="${NONCE}">`));
});

test("CB-T12b 列ごとに畳むボタンを出す", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.all("button.fold").length, 4);
    for (const state of ["todo", "doing", "done", "cancelled"]) {
      assert.equal(page.one(`button.fold[data-fold="${state}"]`).getAttribute("aria-expanded"), "true", state);
    }
  } finally {
    await page.close();
  }
});

test("CB-T12c 列の件数は見えているカードの数。畳んだ列は固定幅に縛られない", async () => {
  const page = await openBoard();
  try {
    const counts = (): string[] => page.all(".column").map((column) => column.querySelector(":scope > h2 > .count")?.textContent ?? "");
    const visible = (): string[] =>
      page.all(".column").map((column) => String(column.querySelectorAll(".card:not(.hidden)").length));
    assert.deepEqual(counts(), visible());
    // 絞り込んだ後も、見えているカードの数。全件のままにすると合わなくなる
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.deepEqual(counts(), visible());
    assert.notDeepEqual(counts(), ["1", "3", "1", "1"], "絞る前の数のままではない");
  } finally {
    await page.close();
  }
  // ドラッグで付けたインラインの width より折りたたんだ状態を優先する
  assert.match(css(), /\.column\.folded \{[^}]*width: auto !important/);
  // 最後の列の取っ手は内側に置き、ボードを常に横へ流さない
  assert.match(css(), /\.column:last-child \.resizer \{ right: 0; \}/);
  // 絞り込みの select は最長の選択肢の幅に広がらず、ページを横に流さない
  assert.match(css(), /\.filter select \{ flex: 0 1 auto; min-width: 0; max-width: var\(--w-field\);/);
  // 操作ボタン（card-button）は、親（カード・フェーズ一覧）の文字サイズを引き継がず、文字サイズ・高さ・余白を明示する。
  // 共通の button.action は使わない
  assert.match(css(), /\.card-button \{[^}]*min-height: 26px;[^}]*font-size: var\(--vscode-font-size, 13px\);/);
  assert.doesNotMatch(css(), /\.card-button \{[^}]*font: inherit/);
});

test("CB-T12d 承認ボタンは見えている承認待ちの数を出し、その識別子を送る。上部の集計は絞らない", async () => {
  const page = await openBoard();
  try {
    assert.equal(text(page, ".summary .pending"), "承認待ち 1 件");
    assert.equal(text(page, ".summary .counts"), "残り 4 / 全 6");
    page.click(page.one('.controls button[data-action="approve"]'));
    await page.settle();
    // 識別子と「絞り込み中か」を別々に送る。空の配列を「全部」に読ませない
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-02-03"], filtered: false });
    // 絞り込んでも上部の集計はボード全体の数のまま（変わるのはボタンの数だけ）
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.equal(text(page, ".summary .pending"), "承認待ち 1 件");
    assert.equal(text(page, ".summary .counts"), "残り 4 / 全 6");
    assert.equal(page.one<HTMLButtonElement>('.controls button[data-action="approve"]').textContent, "承認待ち 1 件を承認");
  } finally {
    await page.close();
  }
});

test("CB-T13b 親の絞り込みを出し、カードにどの親子のチケットかを付ける", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.all("#parent-filter").length, 1);
    assert.ok(texts(page, "#parent-filter option").some((option) => /^i0001 .+/.test(option)));
    assert.equal(page.all('.card[data-family="i0001"]').length, 6);
  } finally {
    await page.close();
  }
  const empty = await openBoard({ ...fixture(), tickets: [], parents: [], pending_approval: [] });
  try {
    assert.equal(empty.all("#parent-filter").length, 0);
  } finally {
    await empty.close();
  }
});

test("CB-T14 0 件のときは空の表示と無効な承認ボタン", async () => {
  const page = await openBoard({ ...fixture(), tickets: [], parents: [], pending_approval: [] });
  try {
    assert.equal(text(page, ".board-empty"), "チケットなし");
    assert.equal(page.all(".column .empty").length, 4);
    assert.equal(page.one<HTMLButtonElement>('.controls button[data-action="approve"]').disabled, true);
  } finally {
    await page.close();
  }
});

test("CB-T15 問題とプロジェクトの絞り込みを出す", async () => {
  const page = await openBoard({ ...fixture(), problems: ["承認済みチケット x を読み込めない"], projects: ["lib", "app"] });
  try {
    assert.deepEqual(texts(page, ".problems li"), ["承認済みチケット x を読み込めない"]);
    assert.deepEqual(texts(page, "#project-filter option"), ["すべて", "ワークスペース（プロジェクト外）", "lib", "app"]);
  } finally {
    await page.close();
  }
  const without = await openBoard();
  try {
    assert.equal(without.all("#project-filter").length, 0);
    assert.equal(without.all(".problems").length, 0);
  } finally {
    await without.close();
  }
});

test("CB-T16 本文の文字列で表示が崩れない", async () => {
  const base = fixture();
  const evil = { ...base.tickets[0], title: `<script>alert("x")</script>` };
  const page = await openBoard({ ...base, tickets: [evil, ...base.tickets.slice(1)] });
  try {
    assert.equal(text(page, `.card[data-id="${evil.ticket}"] .title`), `<script>alert("x")</script>`);
    // 画面の中に script は 1 本（バンドルした画面）だけ。中身から増えない
    assert.equal(page.all(".card script").length, 0);
  } finally {
    await page.close();
  }
});

test("CB-T131o 決めた結果のプロンプトのオーバーレイは、題・注意・プロンプトと、承認と同じコピー・新しいセッションで開く・閉じる", async () => {
  const page = await openBoard(fixture(), {
    approval: { kind: "prompt", title: "フェーズ 2 をレビュー済みにしました", note: "注意 <i>", prompt: "[ccnavi] レビューを終えた <b>" },
  });
  try {
    assert.equal(page.one(".approval-backdrop").getAttribute("data-approval"), "prompt");
    assert.equal(text(page, "#approval-title"), "フェーズ 2 をレビュー済みにしました");
    assert.equal(text(page, ".approval-note"), "注意 <i>");
    assert.equal(text(page, "pre.approval-text"), "[ccnavi] レビューを終えた <b>");
    assert.equal(page.all('button[data-action="prompt-copy"]').length, 1);
    assert.equal(page.all('button[data-action="prompt-open"]').length, 1);
    assert.equal(page.all('button[data-action="prompt-run"]').length, 1);
    assert.equal(page.all('button[data-action="approve-cancel"]').length, 1);
    assert.ok(!text(page, ".approval").includes("を承認しました"));
  } finally {
    await page.close();
  }
});

test("CB-T132r 「ユーザ待ちのみ」の絞り込みを出し、カードに要対応かどうかを付ける。判定は組み立てが出した値をそのまま使うだけ", async () => {
  const page = await openBoard();
  try {
    const label = page.one("label.filter.attention");
    assert.equal(
      label.getAttribute("title"),
      "承認・レビューなど、ユーザの操作で進むものだけを出します",
    );
    assert.equal(label.textContent.trim(), "ユーザ待ちのみ");
    assert.equal(page.one('.card[data-id="i0001-02-03"]').getAttribute("data-attention"), "1");
    assert.equal(page.one('.card[data-id="i0001"]').getAttribute("data-attention"), "0");
    assert.equal(page.one('.card[data-id="i0001-01-01"]').getAttribute("data-attention"), "0");
    // 絞り込み中の扱い（filtering）に入るので、承認は見えている承認待ちだけを送る
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.ok(page.document.body.classList.contains("filtering"));
  } finally {
    await page.close();
  }
});

test("CB-T162 再読み込みできなかった画面にも承認のオーバーレイが載り、閉じる手立てが付いてくる", async () => {
  const plain = await openPage({ kind: "error", error: "ccnavi --explain --json が失敗した: 60 秒で返らないので打ち切った" });
  try {
    assert.ok(text(plain, ".board-empty").includes("チケット管理画面を読み込めませんでした"));
    assert.ok(text(plain, "pre.load-error").includes("60 秒で返らないので打ち切った"));
    assert.equal(plain.all(".approval-backdrop").length, 0, "オーバーレイがなければ被せない");
    // ボードの部品は出さない
    assert.equal(plain.all(".column").length, 0);
  } finally {
    await plain.close();
  }
  const withApproval = await openPage({
    kind: "error",
    error: "読み込めない",
    approval: { kind: "done", count: 2, prompt: "i0001-02-03 を承認した" },
  });
  try {
    assert.equal(text(withApproval, "#approval-title"), "2 件を承認しました");
    assert.equal(text(withApproval, "pre.approval-text"), "i0001-02-03 を承認した");
    // ボタンが有効（画面の骨組みが載っている）
    withApproval.click(withApproval.one('button[data-action="prompt-copy"]'));
    await withApproval.settle();
    assert.deepEqual(withApproval.posted.at(-1), { type: "promptCopy" });
  } finally {
    await withApproval.close();
  }
  // 文に何が入っていても画面の構造を崩さない
  const escaped = await openPage({ kind: "error", error: "<b>", approval: { kind: "error", error: "<script>" } });
  try {
    assert.equal(text(escaped, "pre.load-error"), "<b>");
    assert.equal(text(escaped, ".approval-note.error"), "<script>");
    assert.equal(escaped.all(".approval script").length, 0);
  } finally {
    await escaped.close();
  }
});

test("CB-T142 見た目の切り替えは body のクラスだけを変更する。中身は作り直さない", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.document.body.className, "");
    await page.send({ type: "appearance", value: "claude-light" });
    assert.ok(page.document.body.classList.contains("ccnavi-claude-light"));
    await page.send({ type: "appearance", value: "claude-dark" });
    assert.ok(page.document.body.classList.contains("ccnavi-claude-dark"));
    assert.ok(!page.document.body.classList.contains("ccnavi-claude-light"));
    await page.send({ type: "appearance", value: "vscode" });
    assert.equal(page.document.body.className, "");
    // 画面は作り直されていない（列はそのまま）
    assert.equal(page.all(".column").length, 4);
  } finally {
    await page.close();
  }
});

test("CB-T264 先行を満たしていないカードに「先行待ち（先行の識別子）」のバッジ。どの先行が何の状態かは tooltip に実行ファイルの言葉で出す", async () => {
  const base = fixture();
  const unmet = [
    { ticket: "i0001-02-02", state: "doing", label: "作業中（doing/）" },
    { ticket: "i0001-01-09", state: "missing", label: "どの置き場にもありません" },
  ];
  const page = await openBoard({ ...base, tickets: base.tickets.map((t) => (t.ticket === "i0001-02-03" ? { ...t, predecessors_unmet: unmet } : t)) });
  try {
    assert.equal(text(page, '.card[data-id="i0001-02-03"] .badge.preds'), "先行待ち（i0001-02-02, i0001-01-09）");
    // 識別子は 1 つずつ折り返さない塊（`i0001-` と `02` に割れない）
    assert.deepEqual(texts(page, '.card[data-id="i0001-02-03"] .badge.preds .badge-id'), ["i0001-02-02", "i0001-01-09"]);
    assert.match(css(), /\.badge-id \{ white-space: nowrap; \}/);
    assert.doesNotMatch(css(), /\.badge \{[^}]*border-radius: 999px/);
    const title = page.one('.card[data-id="i0001-02-03"] .badge.preds').getAttribute("title") ?? "";
    assert.match(title, /承認・着手はできません/);
    assert.match(title, /i0001-02-02: 作業中（doing\/）/);
    assert.match(title, /i0001-01-09: どの置き場にもありません/);
    // 先行を満たしているカードには出さない
    assert.equal(page.all('.card[data-id="i0001-02-02"] .badge.preds').length, 0);
  } finally {
    await page.close();
  }
});

test("CB-T265 先行のバッジの言葉はカードの今で分ける。承認待ちは承認と着手、未着手は着手、着手済み・レビュー待ちは止まらないと言う", async () => {
  const base = fixture();
  const unmet = [{ ticket: "i0001-01-09", state: "doing", label: "作業中（doing/）" }];
  const at = (id: string) => base.tickets.find((t) => t.ticket === id)!;
  const tickets = base.tickets.map((t) => {
    if (t.ticket === "i0001-02-03") {
      return { ...t, predecessors_unmet: unmet }; // 承認待ち
    }
    if (t.ticket === "i0001-02-02") {
      return { ...t, started_at: "", predecessors_unmet: unmet }; // 承認済み・未着手
    }
    if (t.ticket === "i0001-02-04") {
      return { ...t, predecessors_unmet: unmet }; // レビュー待ち
    }
    return t;
  });
  assert.equal(at("i0001-02-03").copy.status, "none");
  assert.equal(at("i0001-02-02").copy.status, "open");
  assert.equal(at("i0001-02-04").copy.status, "review");
  const page = await openBoard({ ...base, tickets });
  try {
    const title = (id: string) => page.one(`.card[data-id="${id}"] .badge.preds`).getAttribute("title") ?? "";
    assert.equal(text(page, '.card[data-id="i0001-02-03"] .badge.preds'), "先行待ち（i0001-01-09）");
    assert.match(title("i0001-02-03"), /承認・着手はできません/);
    assert.equal(text(page, '.card[data-id="i0001-02-02"] .badge.preds'), "先行待ち（i0001-01-09）");
    assert.match(title("i0001-02-02"), /着手はできません/);
    assert.doesNotMatch(title("i0001-02-02"), /承認/);
    assert.equal(text(page, '.card[data-id="i0001-02-04"] .badge.preds'), "先行チケットの条件が未達成（i0001-01-09）");
    assert.match(title("i0001-02-04"), /作業を始めています/);
    assert.match(title("i0001-02-04"), /作業と finish コマンドの実行は制限されません/);
  } finally {
    await page.close();
  }
});

