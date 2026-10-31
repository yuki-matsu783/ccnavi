/**
 * 取り込み待ち画面（React）を happy-dom で動かす。対象の一覧（種類・状態・errors・warnings・差分）、
 * 取り込めない理由、選択と取り込みの確認、錠、defense のターミナルの案内、取り込みの結果、無効・エラー・読み込み中を見る。
 * 見本は `test/helpers/handoff.ts`（種類と状態を 1 本ずつ並べた一覧）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { HandoffEntry } from "../../src/core/handoff-view.js";
import { entry, list, openHandoff, openPage, page, sampleEntries } from "../helpers/handoff.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLInputElement, HTMLButtonElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

const TEMPLATE = ".ccnavi/config/project/templates/login.yml";
const RULES = ".ccnavi/config/project/rules.yml";
const DOCS = ".ccnavi/docs/guide.md";
const HOOK = ".claude/hooks/guard.sh";
const LOCKED = { locked: true, reason: "作業中のチケットがあります（T1）", doing: ["T1"], turns: [] };

/** 要素の文字。前後の空白は除く */
function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

const row = (path: string): string => `li.entry[data-path="${path}"]`;
const pick = (dom: DomPage, path: string): HTMLInputElement => dom.one<HTMLInputElement>(`${row(path)} input[data-action="select"]`);

async function check(dom: DomPage, path: string): Promise<void> {
  dom.click(pick(dom, path));
  await dom.settle();
}

test("CB-D366 一覧は対象ごとに 1 行。パス・種類・状態のバッジと、errors / warnings の件数、要約の件数を出す", async () => {
  const dom = await openHandoff();
  try {
    assert.deepEqual(
      dom.all("li.entry").map((li) => li.getAttribute("data-path")),
      sampleEntries().map((e) => e.path),
    );
    assert.equal(text(dom.one(`${row(TEMPLATE)} [data-role="kind"]`)), "テンプレート");
    assert.equal(text(dom.one(`${row(TEMPLATE)} [data-role="state"]`)), "新規");
    assert.equal(text(dom.one(`${row(RULES)} [data-role="kind"]`)), "設定");
    assert.equal(text(dom.one(`${row(RULES)} [data-role="state"]`)), "変更あり");
    assert.equal(text(dom.one(`${row(".ccnavi/docs/same.md")} [data-role="state"]`)), "変更なし");
    assert.match(text(dom.one(row(".ccnavi/docs/broken.md"))), /error 1/);
    assert.match(text(dom.one(row(DOCS))), /warn 1/);
    // 要約: 対象 6 件、取り込める 3 件（template・layer-config の changed・docs）、選択 0 件、対象外 2 件
    assert.equal(text(dom.one("#counts")), "対象 6 件・取り込める 3 件・選択 0 件・対象外 2 件");
    const ignored = text(dom.one("#ignored-paths"));
    assert.match(ignored, /repro\.sh: 取り込める種類の表に無い/);
    assert.match(ignored, /linked: リンクのディレクトリ/);
  } finally {
    await dom.close();
  }
});

test("CB-D367 見出しを押すと開き、差分と errors / warnings を出す。差分が空なら無いと言う", async () => {
  const dom = await openHandoff({ list: list([entry(), entry({ path: ".ccnavi/docs/guide.md", kind: "docs", warnings: ["見出しが無い"], errors: ["UTF-8 ではない"], diff: "", entry_sha: "c".repeat(16) })]) });
  try {
    assert.equal(dom.all('[data-role="diff"]').length, 0, "閉じている間は差分を出さない");
    dom.click(dom.one(`${row(TEMPLATE)} .row-head`));
    await dom.settle();
    assert.equal(dom.one('[data-role="diff"]').textContent, "+name: login\n+description: ログイン\n");
    dom.click(dom.one(`${row(DOCS)} .row-head`));
    await dom.settle();
    assert.equal(text(dom.one(`${row(DOCS)} [data-role="no-diff"]`)), "差分はありません。");
    assert.match(text(dom.one(`${row(DOCS)} .sev-error`)), /\[error\] UTF-8 ではない/);
    assert.match(text(dom.one(`${row(DOCS)} .sev-warn`)), /\[warn\] 見出しが無い/);
  } finally {
    await dom.close();
  }
});

test("CB-D368 取り込めない対象は選べず、理由を出す。error・unchanged・defense（ターミナルのみ）", async () => {
  const dom = await openHandoff();
  try {
    for (const path of [TEMPLATE, RULES, DOCS]) {
      assert.equal(pick(dom, path).disabled, false, `${path} は選べる`);
      assert.equal(dom.all(`${row(path)} [data-role="reason"]`).length, 0, `${path} に理由は出ない`);
    }
    const reasons: [string, RegExp][] = [
      [".ccnavi/docs/broken.md", /取り込めません: 検査で error があります/],
      [".ccnavi/docs/same.md", /取り込めません: 取り込み先と同じ内容/],
      [HOOK, /取り込めません: ボードからは取り込めません。ターミナルで取り込みます/],
    ];
    for (const [path, pattern] of reasons) {
      assert.equal(pick(dom, path).disabled, true, `${path} は選べない`);
      assert.match(text(dom.one(`${row(path)} [data-role="reason"]`)), pattern);
    }
    assert.match(text(dom.one(row(HOOK))), /ターミナルで取り込む/);
  } finally {
    await dom.close();
  }
});

test("CB-D369 選んで「取り込む…」を押すと確認の帯が出て、「この内容で取り込む」で import をパスの配列つきで送る。やめれば何も送らない", async () => {
  const dom = await openHandoff();
  try {
    const open = dom.one<HTMLButtonElement>('[data-action="import"]');
    assert.equal(open.disabled, true, "何も選んでいない間は押せない");
    await check(dom, TEMPLATE);
    await check(dom, DOCS);
    assert.equal(text(dom.one("#counts")), "対象 6 件・取り込める 3 件・選択 2 件・対象外 2 件");
    dom.click(open);
    await dom.settle();
    assert.deepEqual(
      dom.all('[data-role="confirm"] li').map((li) => text(li)),
      [TEMPLATE, DOCS],
    );
    dom.click(dom.one('[data-action="import-cancel"]'));
    await dom.settle();
    assert.equal(dom.all('[data-role="confirm"]').length, 0);
    assert.equal(dom.posted.filter((m) => m.type === "import").length, 0, "やめたら送らない");
    dom.click(open);
    await dom.settle();
    dom.click(dom.one('[data-action="import-yes"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "import"), [{ type: "import", paths: [TEMPLATE, DOCS] }]);
    // 往復の間は欄を止める。更新・選択・取り込みは押せない
    assert.equal(dom.one<HTMLButtonElement>('[data-action="reload"]').disabled, true);
    assert.equal(pick(dom, RULES).disabled, true);
  } finally {
    await dom.close();
  }
});

test("CB-D370 取り込める対象をすべて選ぶ／選択を外す。選ぶのは取り込める 3 件だけで、error・unchanged・defense は入らない", async () => {
  const dom = await openHandoff();
  try {
    dom.click(dom.one('[data-action="select-all"]'));
    await dom.settle();
    assert.deepEqual(
      dom.all('input[data-action="select"]').filter((box) => (box as unknown as HTMLInputElement).checked).map((box) => box.closest("li")?.getAttribute("data-path")),
      [TEMPLATE, RULES, DOCS],
    );
    dom.click(dom.one('[data-action="clear"]'));
    await dom.settle();
    assert.equal(dom.all('input[data-action="select"]').filter((box) => (box as unknown as HTMLInputElement).checked).length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D371 作業中の錠が掛かると template と layer-config は選べなくなり、選んでいたものも外れる。docs は選べるまま。帯に理由を出す", async () => {
  const dom = await openHandoff();
  try {
    assert.ok(dom.one("#lock").classList.contains("hidden"));
    await check(dom, TEMPLATE);
    await check(dom, DOCS);
    await dom.send({ type: "lock", lock: LOCKED });
    assert.ok(!dom.one("#lock").classList.contains("hidden"));
    assert.match(text(dom.one("#lock")), /テンプレートと設定の取り込みは止めています: 作業中のチケットがあります（T1）/);
    assert.equal(pick(dom, TEMPLATE).disabled, true);
    assert.equal(pick(dom, TEMPLATE).checked, false, "止まった種類の選択は外れる");
    assert.match(text(dom.one(`${row(TEMPLATE)} [data-role="reason"]`)), /作業中のチケットがあります（T1）/);
    assert.equal(pick(dom, RULES).disabled, true);
    assert.equal(pick(dom, DOCS).disabled, false, "docs には掛けない");
    assert.equal(pick(dom, DOCS).checked, true);
    assert.equal(dom.all(`${row(DOCS)} [data-role="reason"]`).length, 0);
    // 錠が外れたら選べる（帯も消える）
    await dom.send({ type: "lock", lock: { locked: false, reason: "", doing: [] } });
    assert.ok(dom.one("#lock").classList.contains("hidden"));
    assert.equal(pick(dom, TEMPLATE).disabled, false);
  } finally {
    await dom.close();
  }
});

test("CB-D372 錠が掛かった状態で開いても、template と layer-config は選べない。取り込める対象が template と layer-config だけでなければ帯は出さない", async () => {
  const locked = await openHandoff({ lock: LOCKED });
  try {
    assert.equal(pick(locked, TEMPLATE).disabled, true);
    assert.equal(pick(locked, RULES).disabled, true);
    assert.equal(pick(locked, DOCS).disabled, false);
    assert.ok(!locked.one("#lock").classList.contains("hidden"));
    assert.equal(text(locked.one("#counts")), "対象 6 件・取り込める 1 件・選択 0 件・対象外 2 件");
  } finally {
    await locked.close();
  }
  const docsOnly = await openHandoff({ lock: LOCKED, list: list([entry({ path: DOCS, kind: "docs", state: "changed" })]) });
  try {
    assert.ok(docsOnly.one("#lock").classList.contains("hidden"), "止まる種類の対象が無ければ帯を出さない");
  } finally {
    await docsOnly.close();
  }
});

test("CB-D373 defense はターミナルの案内を出す。「ターミナルのコマンドを出す」で terminal を送り、返ったコマンドを出してコピーできる", async () => {
  const dom = await openHandoff();
  try {
    assert.equal(dom.all('[data-role="terminal-guide"]').length, 0, "閉じている間は出さない");
    dom.click(dom.one(`${row(HOOK)} .row-head`));
    await dom.settle();
    assert.match(text(dom.one('[data-role="terminal-guide"]')), /ボードから取り込めません/);
    assert.match(text(dom.one('[data-role="terminal-guide"]')), /防御を外せてしまうため/);
    dom.click(dom.one('[data-action="show-terminal"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "terminal"), [{ type: "terminal", path: HOOK }]);
    const command = `sh .ccnavi/scripts/ccnavi-user-handoff.sh ${HOOK} --yes --expect ${"ab".repeat(32)}`;
    await dom.send({ type: "terminal", path: HOOK, command, error: "" });
    assert.equal(dom.one('[data-role="terminal-command"]').textContent, command);
    assert.equal(dom.all('[data-action="show-terminal"]').length, 0);
    dom.click(dom.one('[data-action="copy-terminal"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "copy"), [{ type: "copy", text: command }]);
    // ボードからは取り込まない
    assert.equal(dom.posted.filter((m) => m.type === "import").length, 0);
  } finally {
    await dom.close();
  }
  const failed = await openHandoff();
  try {
    failed.click(failed.one(`${row(HOOK)} .row-head`));
    await failed.settle();
    await failed.send({ type: "terminal", path: HOOK, command: "", error: "検査で error があります" });
    assert.equal(text(failed.one('[data-role="terminal-error"]')), "検査で error があります");
    assert.equal(failed.all('[data-action="copy-terminal"]').length, 0);
  } finally {
    await failed.close();
  }
});

test("CB-D374 ボードから取り込める対象にはターミナルの案内を出さない", async () => {
  const dom = await openHandoff();
  try {
    dom.click(dom.one(`${row(DOCS)} .row-head`));
    await dom.settle();
    assert.equal(dom.all('[data-role="terminal-guide"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D375 取り込みの結果を出す。取り込めた対象・消した下書き・残した下書きと理由。確認の帯は閉じ、往復の間止めた欄は戻る", async () => {
  const dom = await openHandoff();
  try {
    await check(dom, DOCS);
    dom.click(dom.one('[data-action="import"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="import-yes"]'));
    await dom.settle();
    await dom.send({
      type: "imported",
      paths: [DOCS],
      result: {
        ok: true,
        applied: [{ path: DOCS, kind: "docs" }],
        removedDrafts: ["wip/handoff/.ccnavi/docs/guide.md"],
        keptDrafts: [{ path: "wip/handoff/.ccnavi/docs/other.md", reason: "取り込み後に下書きが編集されていた" }],
      },
    });
    assert.equal(dom.all('[data-role="confirm"]').length, 0);
    assert.equal(dom.one<HTMLButtonElement>('[data-action="reload"]').disabled, false);
    assert.match(text(dom.one('[data-role="result"] h3')), /取り込みました（1 件）。下書きを 1 件削除しました。下書きを 1 件残しました/);
    assert.deepEqual(dom.all('[data-role="applied"] li').map((li) => text(li)), [`${DOCS}（docs）`]);
    assert.deepEqual(dom.all('[data-role="removed-drafts"] li').map((li) => text(li)), ["wip/handoff/.ccnavi/docs/guide.md"]);
    assert.match(text(dom.one('[data-role="kept-drafts"] li')), /other\.md\s+取り込み後に下書きが編集されていた/);
  } finally {
    await dom.close();
  }
});

test("CB-D376 取り込めなかった結果は理由を出す。選択は残る（読み直したあとも同じ中身の対象は選んだまま）", async () => {
  const dom = await openHandoff();
  try {
    await check(dom, DOCS);
    await dom.send({ type: "imported", paths: [DOCS], result: { ok: false, error: "--expect が合いません" } });
    assert.equal(text(dom.one('[data-role="result-error"]')), "--expect が合いません");
    assert.equal(pick(dom, DOCS).checked, true);
  } finally {
    await dom.close();
  }
});

test("CB-D377 読み直すと、同じ entry_sha で取り込める対象だけ選択が残り、変わった対象・消えた対象の選択は外れる。ターミナルの案内も破棄する", async () => {
  const dom = await openHandoff();
  try {
    await check(dom, TEMPLATE);
    await check(dom, RULES);
    await check(dom, DOCS);
    const next: HandoffEntry[] = sampleEntries()
      .filter((e) => e.path !== TEMPLATE)
      .map((e) => (e.path === RULES ? { ...e, entry_sha: "9".repeat(16) } : e));
    await dom.send({ type: "data", data: { kind: "page", page: page({ list: list(next) }) } });
    assert.equal(dom.all(row(TEMPLATE)).length, 0);
    assert.equal(pick(dom, RULES).checked, false, "中身が変わった対象は選び直す");
    assert.equal(pick(dom, DOCS).checked, true, "同じ中身の対象は選んだまま");
    assert.match(text(dom.one("#counts")), /選択 1 件/);
  } finally {
    await dom.close();
  }
});

test("CB-D378 更新はそのまま refresh を送り、ツリーの切り替えは switchTree を送る。取り込み待ちが無ければ空と言う", async () => {
  const dom = await openHandoff();
  try {
    dom.click(dom.one('button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "refresh"), [{ type: "refresh" }]);
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    dom.change(dom.one<HTMLSelectElement>("select#tree"), "project:lib");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "switchTree"), [{ type: "switchTree", key: "project:lib" }]);
    // 欄の値は拡張ホストが中身を渡し直すまで変わらない
    assert.equal(dom.one<HTMLSelectElement>("select#tree").value, "main:");
  } finally {
    await dom.close();
  }
  const empty = await openHandoff({ list: list([]) });
  try {
    assert.match(text(empty.one('[data-role="empty"]')), /取り込み待ちの下書きはありません/);
    assert.equal(empty.all('[data-action="select-all"], [data-action="import"]').length, 0);
  } finally {
    await empty.close();
  }
});

test("CB-D380 読み込みのエラーは文面とツリーの欄、読み込み中は読み込みの文を出す。見た目は body のクラスだけを変える", async () => {
  const dom = await openHandoff();
  try {
    await dom.send({ type: "data", data: { kind: "error", error: "実行ファイルが返らない", tree: "main:", trees: page().trees } });
    assert.equal(text(dom.one("pre.load-error")), "実行ファイルが返らない");
    assert.equal(dom.all("select#tree").length, 1);
    await dom.send({ type: "data", data: { kind: "loading", text: "読み込み中です" } });
    assert.equal(text(dom.one("#ccnavi-loading")), "読み込み中です");
    await dom.send({ type: "appearance", value: "claude-dark" });
    assert.ok(dom.document.body.classList.contains("ccnavi-claude-dark"));
    await dom.send({ type: "appearance", value: "vscode" });
    assert.ok(!dom.document.body.classList.contains("ccnavi-claude-dark"));
  } finally {
    await dom.close();
  }
});

test("CB-D381 画面が組み上がったら ready を送る。画面は取り込みの判定を持たず、取り込み以外のメッセージを勝手に送らない", async () => {
  const dom = await openHandoff();
  try {
    assert.deepEqual(dom.posted.map((m) => m.type), ["ready"]);
    // 選んだだけでは何も送らない（取り込みは確認の帯の「この内容で取り込む」だけ）
    await check(dom, TEMPLATE);
    assert.deepEqual(dom.posted.map((m) => m.type), ["ready"]);
  } finally {
    await dom.close();
  }
});
