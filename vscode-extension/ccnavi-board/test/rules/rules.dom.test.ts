/**
 * ルール設定画面（React）を happy-dom で動かす。描くものも、押したときの動きもここで見る。
 *
 * 判定は実行ファイルの仕事なので、その結果（`judged` / `sampled`）は拡張ホストから届いたものとして送る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readRules } from "../../src/core/rules-doc.js";
import { KNOWN_TOOLS, type Sections } from "../../src/core/rules-view.js";
import type { RuleHitJson, TestJson } from "../../src/core/testmodel.js";
import { openPage, openRules, page, rowSelector } from "../helpers/rules.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

/** 直前に送った保存の中身 */
function savedSections(dom: DomPage): Sections {
  const saves = dom.posted.filter((message) => message.type === "save");
  assert.ok(saves.length > 0, "保存を送っていない");
  return saves[saves.length - 1].sections as Sections;
}

function hit(section: string, id: string, kind = "glob"): RuleHitJson {
  return { section, id, kind, written: "*", pattern: ".*", source: "file" };
}

/** 実行ファイルが返した判定。拡張ホストが `judged` で渡す形 */
function judged(rules: readonly RuleHitJson[]): { type: string; result: TestJson; hooks: [] } {
  const result: TestJson = {
    version: 1,
    root: "/ws",
    rules_path: ".ccnavi/common/rules.yml",
    known: true,
    tool: "Bash",
    subject: "x",
    resolved: "x",
    verdict: "deny",
    code: "",
    reason: "",
    degraded: "",
    fallback: "",
    rules,
    response: "",
  };
  return { type: "judged", result, hooks: [] };
}

test("CB-D01 既定は全部畳む。行の見出しを押すと開き、開いた行の id が state に入る。もう一度押すと畳む", async () => {
  const dom = await openRules();
  try {
    assert.equal(dom.all(".rule").length, 3);
    assert.equal(dom.all(".rule.open").length, 0);
    dom.click(dom.one(`${rowSelector("no-rm")} .row-head`));
    await dom.settle();
    assert.ok(dom.one(rowSelector("no-rm")).classList.contains("open"));
    assert.deepEqual((dom.state() as { open: string[] }).open, ["no-rm"]);
    // 開いた行の欄には値が入っている
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("no-rm")} input.f-id`).value, "no-rm");
    dom.click(dom.one(`${rowSelector("no-rm")} .row-head`));
    await dom.settle();
    assert.ok(!dom.one(rowSelector("no-rm")).classList.contains("open"));
    assert.deepEqual((dom.state() as { open: string[] }).open, []);
  } finally {
    await dom.close();
  }
});

test("CB-D08 id を打っている途中は控えを書き直さず、確定（change）したときに新しい id で控える", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one(`${rowSelector("git-push")} .row-head`));
    await dom.settle();
    assert.deepEqual((dom.state() as { open: string[] }).open, ["git-push"]);
    dom.type(dom.one(`${rowSelector("git-push")} input.f-id`), "git-pu");
    await dom.settle();
    assert.deepEqual((dom.state() as { open: string[] }).open, ["git-push"], "打っている途中は前の id のまま");
    dom.type(dom.one(`${rowSelector("git-pu")} input.f-id`), "git-push-2");
    await dom.settle();
    dom.change(dom.one(`${rowSelector("git-push-2")} input.f-id`));
    await dom.settle();
    assert.deepEqual((dom.state() as { open: string[] }).open, ["git-push-2"]);
    assert.equal(dom.one(`${rowSelector("git-push-2")} .sum .sum-id`).textContent, "git-push-2");
  } finally {
    await dom.close();
  }
});

test("CB-D09 土台は画面のスクリプトの例外を握りつぶさない。非同期の例外も拾い、1 度投げたら消す", async () => {
  const dom = await openRules();
  try {
    dom.one("#find").addEventListener("input", () => {
      throw new Error("わざと");
    }, { once: true });
    assert.throws(() => dom.type(dom.one("#find"), "x"), /わざと/);
    assert.equal(dom.errors.length, 0, "投げたら消える");
    dom.one("#find").addEventListener("input", async () => {
      await Promise.resolve();
      throw new Error("あとで");
    });
    dom.type(dom.one("#find"), "y");
    await assert.rejects(dom.settle(), /あとで/);
  } finally {
    await dom.close();
  }
});

test("CB-D0a 絞り込み中にタイプを畳んでも矢印は開いた向きのまま。足したルールのタイプは開く", async () => {
  const dom = await openRules();
  try {
    dom.type(dom.one("#find"), "git");
    await dom.settle();
    dom.click(dom.one('button[data-action="fold-section"][data-section="deny"]'));
    await dom.settle();
    assert.ok(dom.one('.rule-section[data-section="deny"]').classList.contains("folded"));
    assert.equal(dom.one('.rule-section[data-section="deny"] h2 > .twist').textContent, "▾");
    dom.type(dom.one("#find"), "");
    await dom.settle();
    assert.equal(dom.one('.rule-section[data-section="deny"] h2 > .twist').textContent, "▸");
    dom.click(dom.one('button[data-action="fold-section"][data-section="allow"]'));
    await dom.settle();
    dom.click(dom.one('button[data-action="add"][data-section="allow"]'));
    await dom.settle();
    assert.ok(!dom.one('.rule-section[data-section="allow"]').classList.contains("folded"));
    assert.equal(dom.one('.rule-section[data-section="allow"] h2 > .twist').textContent, "▾");
  } finally {
    await dom.close();
  }
});

test("CB-D0b 見た目のメッセージで body のクラスが付け替わり、開いている行と入力は消えない", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one(`${rowSelector("git-push")} .row-head`));
    await dom.settle();
    dom.type(dom.one(`${rowSelector("git-push")} input.f-id`), "git-push-x");
    await dom.settle();
    await dom.send({ type: "appearance", value: "claude-light" });
    assert.deepEqual(Array.from(dom.document.body.classList), ["ccnavi-claude-light"]);
    await dom.send({ type: "appearance", value: "claude-dark" });
    assert.deepEqual(Array.from(dom.document.body.classList), ["ccnavi-claude-dark"]);
    await dom.send({ type: "appearance", value: "vscode" });
    assert.deepEqual(Array.from(dom.document.body.classList), []);
    assert.ok(dom.one(rowSelector("git-push-x")).classList.contains("open"));
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("git-push-x")} input.f-id`).value, "git-push-x");
  } finally {
    await dom.close();
  }
});

test("CB-D02 state に控えた id の行は、読み直したあとも開いている", async () => {
  const dom = await openRules({}, { open: ["deps"], tab: "rules" });
  try {
    assert.ok(dom.one(rowSelector("deps")).classList.contains("open"));
    assert.equal(dom.all(".rule.open").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D03 コンテキストの欄は値があるルールだけ最初から開き、利用者が閉じれば描き直しても閉じたまま", async () => {
  const dom = await openRules();
  try {
    assert.ok(!dom.one(`${rowSelector("git-push")} details.more`).hasAttribute("open"));
    assert.ok(dom.one(`${rowSelector("no-rm")} details.more`).hasAttribute("open"));
    assert.ok(dom.one(`${rowSelector("deps")} details.more`).hasAttribute("open"));
    // 閉じてから、形式を変えて描き直す
    const more = dom.one(`${rowSelector("no-rm")} details.more`);
    more.removeAttribute("open");
    more.dispatchEvent(new dom.window.Event("toggle"));
    await dom.settle();
    dom.click(dom.one(`${rowSelector("no-rm")} .row-head`));
    await dom.settle();
    dom.change(dom.one(`${rowSelector("no-rm")} select.f-kind`), "regex");
    await dom.settle();
    assert.ok(!dom.one(`${rowSelector("no-rm")} details.more`).hasAttribute("open"));
    assert.ok(dom.one(rowSelector("no-rm")).classList.contains("open"), "描き直しても開いたまま");
  } finally {
    await dom.close();
  }
});

test("CB-D04 絞り込みは一致しない行を隠し、開いている行は隠さず、件数は一致した数、畳んだタイプの矢印は開いた向き", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one(`${rowSelector("deps")} .row-head`));
    await dom.settle();
    dom.click(dom.one('button[data-action="fold-section"][data-section="deny"]'));
    await dom.settle();
    assert.ok(dom.one('.rule-section[data-section="deny"]').classList.contains("folded"));
    dom.type(dom.one("#find"), "rm -rf");
    await dom.settle();
    const hidden = dom.all(".rule").map((rule) => `${rule.getAttribute("data-id")}:${rule.classList.contains("hidden-by-find")}`);
    assert.deepEqual(hidden, ["git-push:true", "no-rm:false", "deps:true"]);
    // 開いている deps は hidden-by-find でも表示は消えない（CSS の :not(.open)）
    assert.ok(dom.one("#tab-rules").classList.contains("finding"));
    assert.equal(dom.one('[data-count="deny"]').textContent, "1 / 2");
    assert.equal(dom.one('[data-count="ask"]').textContent, "0 / 1（開いたまま 1）");
    assert.equal(dom.one('.rule-section[data-section="deny"] h2 > .twist').textContent, "▾");
    // 空に戻すと元どおり
    dom.type(dom.one("#find"), "");
    await dom.settle();
    assert.equal(dom.all(".rule.hidden-by-find").length, 0);
    assert.equal(dom.one('[data-count="deny"]').textContent, "2");
    assert.equal(dom.one('.rule-section[data-section="deny"] h2 > .twist').textContent, "▸");
  } finally {
    await dom.close();
  }
});

test("CB-D05 ツールの選択肢で選ぶと、欄と行の要約が同じ操作の中で新しい値になる", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one(`${rowSelector("no-rm")} .row-head`));
    await dom.settle();
    dom.click(dom.one(`${rowSelector("no-rm")} input.f-match`));
    await dom.settle();
    assert.ok(dom.one(`${rowSelector("no-rm")} .picker`).classList.contains("open"));
    // 知らない名前は選択肢に出ないが、欄に書いてあれば選択肢として並ぶ
    assert.deepEqual(
      dom.all(`${rowSelector("no-rm")} .picker input[type=checkbox]`).map((box) => box.getAttribute("value")),
      [...KNOWN_TOOLS],
    );
    dom.click(dom.one(`${rowSelector("no-rm")} .picker input[value="Write"]`));
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("no-rm")} input.f-match`).value, "Bash|Write");
    assert.equal(dom.one(`${rowSelector("no-rm")} .sum .mono`).textContent, "Bash|Write");
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled, "編集したので保存できる");
  } finally {
    await dom.close();
  }
});

test("CB-D06 判定で当たった行はその場で開くが state には入らず、次の判定で畳まれる", async () => {
  const dom = await openRules();
  try {
    await dom.send(judged([hit("deny", "git-push")]));
    assert.ok(dom.one(rowSelector("git-push")).classList.contains("open"));
    assert.ok(dom.one(rowSelector("git-push")).classList.contains("hit"));
    assert.deepEqual(((dom.state() as { open?: string[] }) ?? {}).open ?? [], []);
    assert.equal((dom.state() as { tab: string }).tab, "judge");
    assert.ok(dom.one("#tab-judge").classList.contains("active"));
    await dom.send(judged([hit("deny", "no-rm")]));
    assert.ok(!dom.one(rowSelector("git-push")).classList.contains("open"));
    assert.ok(dom.one(rowSelector("no-rm")).classList.contains("open"));
    // 絞り込み中に 2 件当たっても、両方開き、件数は一致した行だけを数える
    dom.type(dom.one("#find"), "pyproject");
    await dom.settle();
    await dom.send(judged([hit("deny", "git-push"), hit("deny", "no-rm")]));
    assert.ok(dom.one(rowSelector("git-push")).classList.contains("open"));
    assert.ok(dom.one(rowSelector("no-rm")).classList.contains("open"));
    assert.equal(dom.one('[data-count="deny"]').textContent, "0 / 2（開いたまま 2）");
    assert.equal(dom.one('[data-count="ask"]').textContent, "1 / 1");
    // 畳んだタイプの中のルールが当たれば、タイプが開いて矢印もそれに合う
    dom.type(dom.one("#find"), "");
    await dom.settle();
    dom.click(dom.one('button[data-action="fold-section"][data-section="ask"]'));
    await dom.settle();
    assert.equal(dom.one('.rule-section[data-section="ask"] h2 > .twist').textContent, "▸");
    await dom.send(judged([hit("ask", "deps", "regex")]));
    assert.ok(!dom.one('.rule-section[data-section="ask"]').classList.contains("folded"));
    assert.equal(dom.one('.rule-section[data-section="ask"] h2 > .twist').textContent, "▾");
    assert.ok(dom.one(rowSelector("deps")).classList.contains("open"));
    // ヒットしたルールの表と、返すメッセージ・hook の見出しも出る
    assert.deepEqual(dom.all("#judge-result h3").map((head) => head.textContent), ["ヒットしたルール", "返すメッセージ", "このツールで実行される hook"]);
    assert.equal(dom.all("#judge-result table tbody tr").length, 1);
    assert.match(dom.one("#judge-result").textContent ?? "", /実行される hook はありません/);
  } finally {
    await dom.close();
  }
});

test("CB-D0c 刻みは畳んだ行のバッジに出る。欄に打てばバッジも変わり、保存はその文字を送る", async () => {
  const dom = await openRules();
  try {
    // 読んだ刻みは畳んだままでも見える。刻みが無い行はバッジを出さない（枠だけ置く）
    assert.equal(dom.one(`${rowSelector("no-rm")} .sum .sum-every`).textContent, "4 回ごと");
    assert.equal(dom.one(`${rowSelector("git-push")} .sum .sum-every`).textContent, "");
    // 刻みだけを直す。欄は「コンテキストの追加」の中にあり、刻みがあれば最初から開いている
    assert.ok(!dom.one(`${rowSelector("git-push")} details.more`).hasAttribute("open"));
    dom.click(dom.one(`${rowSelector("git-push")} .row-head`));
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("git-push")} input.f-every`).value, "");
    dom.type(dom.one(`${rowSelector("git-push")} input.f-every`), "3");
    await dom.settle();
    assert.equal(dom.one(`${rowSelector("git-push")} .sum .sum-every`).textContent, "3 回ごと");
    // 読めない値も打てる。画面は直さず、そのまま送る（止めるのは保存前の --lint）
    dom.type(dom.one(`${rowSelector("no-rm")} input.f-every`), "x");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedSections(dom).deny.map((rule) => [rule.id, rule.every]), [["git-push", "3"], ["no-rm", "x"]]);
  } finally {
    await dom.close();
  }
});

test("CB-D07 足したルールは開いて焦点が id に来る。タイプを移すと移った先でも開いたまま。保存は今の並びを送る", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one('button[data-action="add"][data-section="allow"]'));
    await dom.settle();
    const added = dom.one('[data-list="allow"] .rule');
    assert.ok(added.classList.contains("open"));
    assert.equal(dom.document.activeElement, dom.one('[data-list="allow"] .rule input.f-id'));
    dom.type(dom.one('[data-list="allow"] .rule input.f-id'), "new-one");
    await dom.settle();
    dom.change(dom.one('[data-list="allow"] .rule select.f-section'), "ask");
    await dom.settle();
    assert.equal(dom.all('[data-list="allow"] .rule').length, 0);
    const moved = dom.one(rowSelector("new-one"));
    assert.ok(moved.closest('[data-list="ask"]') !== null);
    assert.ok(moved.classList.contains("open"));
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedSections(dom).ask.map((rule) => rule.id), ["deps", "new-one"]);
  } finally {
    await dom.close();
  }
});

test("CB-D69 additionalContextFile で選んだ綴りは、拡張ホストが名指しした行の欄にだけ入る", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one(`${rowSelector("git-push")} .row-head`));
    await dom.settle();
    dom.click(dom.one(`${rowSelector("git-push")} .f-context-file + button`));
    await dom.settle();
    const asked = dom.posted.filter((message) => message.type === "pickFile");
    assert.equal(asked.length, 1);
    assert.equal(asked[0].field, "additionalContextFile");
    await dom.send({ type: "picked", key: asked[0].key, field: "additionalContextFile", path: "docs/note.md" });
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("git-push")} input.f-context-file`).value, "docs/note.md");
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("no-rm")} input.f-context-file`).value, "");
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled, "欄が埋まったので保存できる");
  } finally {
    await dom.close();
  }
});

test("CB-D70 再読込は押した時点でボタンを止め、やめたら戻る。中身が届けば編集は捨てて入れ替わる", async () => {
  const dom = await openRules();
  try {
    dom.type(dom.one(`${rowSelector("git-push")} input.f-id`), "打ちかけ");
    await dom.settle();
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
    dom.click(dom.one('.controls button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "reload").map((message) => message.dirty), [true]);
    assert.ok(dom.one<HTMLButtonElement>('.controls button[data-action="reload"]').disabled);
    await dom.send({ type: "cancelled" });
    assert.ok(!dom.one<HTMLButtonElement>('.controls button[data-action="reload"]').disabled);
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("打ちかけ")} input.f-id`).value, "打ちかけ");
    // 中身が届いたら編集は捨てる
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    assert.equal(dom.all(rowSelector("打ちかけ")).length, 0);
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled, "未保存が消えたので保存は押せない");
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
  } finally {
    await dom.close();
  }
});

test("CB-D71 ファイルが外で変わったら帯を出す。錠と操作の一言は届いたときに出る", async () => {
  const dom = await openRules();
  try {
    assert.ok(dom.one("#changed").classList.contains("hidden"));
    await dom.send({ type: "changed" });
    assert.ok(!dom.one("#changed").classList.contains("hidden"));
    await dom.send({ type: "lock", lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] } });
    assert.equal(dom.one("#lock").textContent, "作業中のチケットがある（i0001-02）");
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled, "錠が掛かっていれば保存は押せない");
    await dom.send({ type: "failed", message: "--lint が error を報告した" });
    assert.equal(dom.one("#status").textContent, "--lint が error を報告した");
    assert.ok(dom.one("#status").closest(".foot")?.classList.contains("error"));
  } finally {
    await dom.close();
  }
});

test("CB-D72 読み直せなかった画面から中身が届いたあとも、id の確定で開いた行を控える", async () => {
  const dom = await openPage({ kind: "error", error: "ルールファイルを読めない" });
  try {
    assert.match(dom.one(".load-error").textContent ?? "", /ルールファイルを読めない/);
    dom.click(dom.one('button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "reload").map((message) => message.dirty), [false]);
    assert.ok(dom.one<HTMLButtonElement>('button[data-action="reload"]').disabled, "押した時点で止める");
    // 中身が届いて一覧が出る。控えの受け口（id の確定）は、ここで張られていないと二度と張られない
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    dom.click(dom.one(`${rowSelector("deps")} .row-head`));
    await dom.settle();
    dom.type(dom.one(`${rowSelector("deps")} input.f-id`), "deps-2");
    await dom.settle();
    dom.change(dom.one(`${rowSelector("deps-2")} input.f-id`));
    await dom.settle();
    assert.deepEqual((dom.state() as { open: string[] }).open, ["deps-2"]);
  } finally {
    await dom.close();
  }
});

test("CB-T50 dry-run のときは止めないことを言い、enable と未設定（実行ファイルは enable と扱う）なら言わない", async () => {
  const banners = async (mode: string): Promise<string[]> => {
    const dom = await openRules({ mode });
    try {
      return dom.all(".banner.warn:not(.hidden)").map((banner) => banner.textContent ?? "");
    } finally {
      await dom.close();
    }
  };
  assert.match((await banners("dry-run")).join("\n"), /CCNAVI_MODE.*dry-run/);
  assert.deepEqual(await banners(""), []);
  assert.deepEqual(await banners("enable"), []);
});

test("CB-T51 保存できない理由と読み込みの苦情を出す", async () => {
  const locked = await openRules({ lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] } });
  try {
    assert.equal(locked.one("#lock").textContent, "作業中のチケットがある（i0001-02）");
    assert.ok(!locked.one("#lock").classList.contains("hidden"));
  } finally {
    await locked.close();
  }
  const open = await openRules();
  try {
    assert.ok(open.one("#lock").classList.contains("hidden"));
    assert.equal(open.all(".problems").length, 0);
  } finally {
    await open.close();
  }
  const broken = await openRules({ model: readRules("version: 1\ndeny: nope\n").model });
  try {
    assert.equal(broken.all(".problems li").length, 1);
  } finally {
    await broken.close();
  }
});

test("CB-T52 settings.json が無ければ hook の表にそう書く", async () => {
  const dom = await openRules({ hooks: [], hookFiles: { settings: false, settingsLocal: false } });
  try {
    assert.match(dom.one("#tab-hooks").textContent ?? "", /settings\.json がありません/);
    assert.equal(dom.all("#tab-hooks table").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-T69 タイプごとに畳むボタンを出す", async () => {
  const dom = await openRules();
  try {
    for (const section of ["deny", "ask", "allow"]) {
      assert.equal(dom.all(`button[data-action="fold-section"][data-section="${section}"]`).length, 1);
      assert.equal(dom.one(`.rule-section[data-section="${section}"] .section-name`).textContent, section);
    }
  } finally {
    await dom.close();
  }
});

test("CB-T71 match の候補と判定の試し打ちは、権限ルールの名前（括弧の中を除いたもの）で並ぶ", async () => {
  // 判定が対象を取り出せるツールだけ。WebSearch は取り出せないので載せない。
  assert.deepEqual([...KNOWN_TOOLS], [
    "Bash", "PowerShell", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "Skill", "Agent", "WebFetch",
  ]);
  const dom = await openRules();
  try {
    assert.deepEqual(dom.all("#tool option").map((option) => option.textContent), [...KNOWN_TOOLS]);
  } finally {
    await dom.close();
  }
});

test("CB-T112 ルール設定画面は注意を上部に出し、無ければ出さない", async () => {
  const dom = await openRules({ rulesPath: "projects/lib/.ccnavi/config/rules.yml", notices: ["実行ファイルはこのファイルを読めない: <理由>"] });
  try {
    const warned = dom.all(".banner.warn:not(.hidden)").map((banner) => banner.textContent ?? "");
    assert.deepEqual(warned, ["実行ファイルはこのファイルを読めない: <理由>"]);
    assert.equal(dom.all(".banner.warn script").length, 0, "文面から要素は生えない");
    assert.equal(dom.one(".toolbar .path").textContent, "projects/lib/.ccnavi/config/rules.yml");
  } finally {
    await dom.close();
  }
  const quiet = await openRules();
  try {
    assert.deepEqual(quiet.all(".banner.warn:not(.hidden)"), []);
  } finally {
    await quiet.close();
  }
});

test("CB-T120 一覧は 1 件 1 行で既定は畳み、絞り込み欄を持ち、開いた行を id で state に控える", async () => {
  const dom = await openRules();
  try {
    assert.equal(dom.all("#find").length, 1);
    assert.equal(dom.all('[data-list="deny"] .rule').length, 2);
    assert.equal(dom.all(".rule > .row-head").length, 3);
    assert.equal(dom.all(".rule > .row-body").length, 3, "本体は畳んでいても DOM にある（見せるかは CSS）");
    assert.equal(dom.all(".rule.open").length, 0);
    dom.click(dom.one(`${rowSelector("deps")} .row-head`));
    await dom.settle();
    assert.deepEqual((dom.state() as { open: string[] }).open, ["deps"]);
    // id が空の行は開いていても控えられない（次に開き直す手がかりが無い）
    dom.click(dom.one('button[data-action="add"][data-section="deny"]'));
    await dom.settle();
    const added = dom.all('[data-list="deny"] .rule').slice(-1)[0];
    assert.ok(added.classList.contains("open"));
    dom.click(added.querySelector(".row-head")!);
    await dom.settle();
    dom.click(dom.all('[data-list="deny"] .rule').slice(-1)[0].querySelector(".row-head")!);
    await dom.settle();
    assert.deepEqual((dom.state() as { open: string[] }).open, ["deps"]);
  } finally {
    await dom.close();
  }
});

test("CB-T124 欄名は日本語で、YAML のキー名は欄名の title に載せる", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one(`${rowSelector("git-push")} .row-head`));
    await dom.settle();
    const caps = new Map(dom.all(`${rowSelector("git-push")} .field > .cap`).map((cap) => [cap.textContent ?? "", cap.getAttribute("title")]));
    assert.equal(caps.get("ツール"), "YAML のキー: match");
    assert.equal(caps.get("メッセージ"), "YAML のキー: message");
    assert.equal(caps.get("additionalContext"), "YAML のキー: additionalContext");
    assert.equal(caps.get("additionalContextOnceFile"), "YAML のキー: additionalContextOnceFile");
    assert.equal(caps.get("every"), "YAML のキー: every");
    assert.equal(dom.one("#subject").closest("label")?.getAttribute("title"), "--test の subject");
  } finally {
    await dom.close();
  }
});

test("CB-D83 未保存の変更の有無は変わったときだけ拡張ホストへ伝え、切り替え中は「読み込み中」を出して中身が届けば描き直す。前の対象の判定は残さない", async () => {
  const dom = await openRules();
  try {
    // 入れ物に入れておいた「読み込み中」は、画面が組み上がると残らない
    assert.equal(dom.all("#ccnavi-loading").length, 0);
    await dom.send(judged([hit("deny", "git-push")]));
    assert.equal(dom.all("#judge-result").length, 1);
    assert.deepEqual(dom.posted.filter((message) => message.type === "dirty"), [], "開いた時点の「変更なし」は送らない");
    dom.type(dom.one(`${rowSelector("git-push")} input.f-id`), "打ちかけ");
    await dom.settle();
    dom.type(dom.one(`${rowSelector("打ちかけ")} input.f-id`), "打ちかけ2");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "dirty"), [{ type: "dirty", dirty: true }], "打ち続けても 1 度だけ");
    // 別の対象へ切り替わった。前の対象の編集は捨てて、読み込み中を出す
    await dom.send({ type: "data", data: { kind: "loading", text: "web のルールを読み込み中…" } });
    assert.equal(dom.one("#ccnavi-loading").textContent, "web のルールを読み込み中…");
    assert.equal(dom.all(".rule").length, 0);
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "dirty").map((message) => message.dirty),
      [true, false],
      "編集を捨てたことも伝える",
    );
    await dom.send({ type: "data", data: { kind: "page", page: page({ rulesPath: "projects/web/.ccnavi/config/rules.yml" }) } });
    assert.equal(dom.all("#ccnavi-loading").length, 0);
    assert.equal(dom.all(".rule").length, 3);
    // 前の対象のルールで出した判定は、切り替え先の結果として残さない
    assert.equal(dom.all("#judge-result").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D101 ルール設定の案内はタブを切り替えて中を指し、閉じたら始める前のタブに戻す。途中の切り替えは控えに書かない", async () => {
  const dom = await openRules({}, { tab: "hooks" });
  try {
    assert.ok(dom.one("#tab-hooks").classList.contains("active"));
    // 案内の入口は ? 1 文字で、ヘッダ（ツールバー）の最後の子。位置は Tour.css が 5 画面とも右上に揃える
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    await dom.send({ type: "tour" });
    await dom.settle();
    const titles: string[] = [];
    const tabs: string[] = [];
    while (dom.all(".tour").length > 0) {
      titles.push(dom.one("#tour-title").textContent ?? "");
      tabs.push(dom.one(".pane.active").id);
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    assert.deepEqual(titles, ["3 つのタブ", "ルール", "絞り込み", "判定を試す", "hook", "保存", "案内"]);
    assert.deepEqual(tabs, ["tab-rules", "tab-rules", "tab-rules", "tab-judge", "tab-hooks", "tab-hooks", "tab-hooks"]);
    assert.ok(dom.one("#tab-hooks").classList.contains("active"), "始める前のタブに戻っていない");
    assert.equal((dom.state() as { tab?: string }).tab, "hooks");
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D106 読み込み中に頼まれた案内はルールが出てから始め、別の対象へ切り替わって読み込み中になったら閉じて tourDone を返す", async () => {
  const dom = await openPage({ kind: "loading", text: "ルールを読み込み中…" });
  try {
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0, "指す先が無い読み込み中には出さない");
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "3 つのタブ");
    await dom.send({ type: "data", data: { kind: "loading", text: "ルールを読み込み中…" } });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 1);
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0, "人が始めていない案内が出直した");
  } finally {
    await dom.close();
  }
});
