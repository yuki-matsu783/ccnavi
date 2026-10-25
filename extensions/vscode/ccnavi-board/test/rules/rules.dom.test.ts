/**
 * ルール管理画面（React）を happy-dom で動かす。描くものも、押したときの動きもここで見る。
 *
 * 判定は実行ファイルの仕事なので、その結果（`judged` / `expectationsChecked`）は拡張ホストから届いたものとして送る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readRules } from "../../src/core/rules-doc.js";
import { KNOWN_TOOLS, type Sections } from "../../src/core/rules-view.js";
import { rulesSums } from "../../src/core/sums.js";
import type { RuleHitJson, TestJson } from "../../src/core/testmodel.js";
import { openPage, openRules, page, rowSelector } from "../helpers/rules.js";
import { checkHandoffLink } from "../helpers/handoff-link.js";
import type { DomPage } from "../helpers/dom.js";
import { sumsBoard } from "../helpers/sums.js";
import type { HTMLButtonElement, HTMLDetailsElement, HTMLInputElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };
import { loadTour } from "../helpers/tour.js";

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
    rules_path: ".ccnavi/config/common/rules.yml",
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

test("CB-D1227 判定結果は一時パスを出さず、項目名は日本語に英語を添える", async () => {
  const dom = await openRules();
  try {
    const sent = judged([{ id: "../../tmp/ccnavi-rules-aB3dE9/rules.yml:no-rm", section: "deny", source: "file", kind: "", written: "", pattern: "" } as RuleHitJson]);
    await dom.send({ ...sent, result: { ...sent.result, response: "[ccnavi] X (rule: ../../tmp/ccnavi-rules-aB3dE9/rules.yml:no-rm)" } });
    await dom.settle();
    const shown = dom.one("#judge-result").textContent ?? "";
    assert.ok(!shown.includes("ccnavi-rules-"), shown);
    assert.match(shown, /ツール（tool）/);
    assert.match(shown, /rules\.yml:no-rm/);
  } finally {
    await dom.close();
  }
});

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

test("CB-D08 id を打っている途中は state を書き直さず、確定（change）したときに新しい id で state に残す", async () => {
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

test("CB-D09 土台は画面のスクリプトの例外を握りつぶさない。非同期の例外も拾い、1 度投げたら削除", async () => {
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

test("CB-D0a 絞り込み中にタイプを畳んでも矢印は開いた向きのまま。追加したルールのタイプは開く", async () => {
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

test("CB-D0b 見た目のメッセージで body のクラスが書き換わり、開いている行と入力は消えない", async () => {
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

test("CB-D02 state に残した id の行は、再読み込みしたあとも開いている", async () => {
  const dom = await openRules({}, { open: ["deps"], tab: "rules" });
  try {
    assert.ok(dom.one(rowSelector("deps")).classList.contains("open"));
    assert.equal(dom.all(".rule.open").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D03 コンテキストの欄は値があるルールだけ最初から開き、ユーザが閉じれば描き直しても閉じたまま", async () => {
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
    assert.equal(dom.one('[data-count="ask"]').textContent, "0 / 1（検索条件外の展開中項目: 1 件）");
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

test("CB-D346 一覧にない新しいツール名を追加欄に打つと、選んだ状態で match に入る。形の違う名前は足さず理由を出す", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one(`${rowSelector("no-rm")} .row-head`));
    await dom.settle();
    dom.click(dom.one(`${rowSelector("no-rm")} input.f-match`));
    await dom.settle();
    const adder = `${rowSelector("no-rm")} .picker .add-tool`;
    dom.type(dom.one<HTMLInputElement>(`${adder} input`), "2bad name");
    dom.click(dom.one(`${adder} button`));
    await dom.settle();
    assert.match(dom.one(`${adder} .add-error`).textContent, /英字で始まり/);
    assert.ok(!dom.one(`${rowSelector("no-rm")} .picker`).textContent?.includes("2bad"), "形の違う名前は選択肢に出ない");

    dom.type(dom.one<HTMLInputElement>(`${adder} input`), "mcp__github__get_me");
    dom.click(dom.one(`${adder} button`));
    await dom.settle();
    assert.equal(dom.all(`${adder} .add-error`).length, 0, "直した名前は通る");
    const matched = dom.one<HTMLInputElement>(`${rowSelector("no-rm")} input.f-match`).value.split("|");
    assert.ok(matched.includes("mcp__github__get_me"), "match に入る");
    const box = dom.one<HTMLInputElement>(`${rowSelector("no-rm")} .picker input[value="mcp__github__get_me"]`);
    assert.equal(box.checked, true, "選択肢に並び、選んだ状態になる");
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled, "編集したので保存できる");
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

test("CB-D06 判定でヒットした行はその場で開くが state には入らず、次の判定で畳まれる", async () => {
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
    // 絞り込み中に 2 件ヒットしても、両方開き、件数は一致した行だけを数える
    dom.type(dom.one("#find"), "pyproject");
    await dom.settle();
    await dom.send(judged([hit("deny", "git-push"), hit("deny", "no-rm")]));
    assert.ok(dom.one(rowSelector("git-push")).classList.contains("open"));
    assert.ok(dom.one(rowSelector("no-rm")).classList.contains("open"));
    assert.equal(dom.one('[data-count="deny"]').textContent, "0 / 2（検索条件外の展開中項目: 2 件）");
    assert.equal(dom.one('[data-count="ask"]').textContent, "1 / 1");
    // 折りたたんだタイプの中のルールがヒットすれば、タイプが開いて矢印もそれに合う
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
    // 読んだ刻みは折りたたんだままでも見える。刻みが無い行はバッジを出さない（枠だけ置く）
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

test("CB-D124 「記録から候補を出す」は suggest を送り、届いた候補を下書きのまま判定タブに並べる", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one('button[data-action="suggest"]'));
    await dom.settle();
    assert.equal(dom.posted.filter((message) => message.type === "suggest").length, 1);
    assert.ok((dom.one('button[data-action="suggest"]') as unknown as HTMLButtonElement).disabled);
    const candidate = {
      kind: "rule",
      section: "ask",
      id: "suggest-npm-install",
      tool: "Bash",
      count: 6,
      summary: "ルールを追加する候補",
      layer: "common",
      rules_path: "/ws/.ccnavi/config/common/rules.yml",
      expectations: [],
      yaml: "# ルールを追加する候補\nrules:\n  ask: []\n",
    };
    await dom.send({
      type: "suggested",
      result: { version: 1, root: "/ws", rules_path: "", logs: ["/ws/logs/decisions.jsonl"], records: 9, candidates: [candidate], dropped: 2 },
    });
    assert.ok(dom.one("#tab-judge").classList.contains("active"));
    assert.ok(!(dom.one('button[data-action="suggest"]') as unknown as HTMLButtonElement).disabled);
    assert.equal(dom.all("#suggest-result .candidate").length, 1);
    assert.match(dom.one("#suggest-result").textContent ?? "", /除いたもの 2 件/);
    assert.match(dom.one('#suggest-result .candidate[data-id="suggest-npm-install"] pre').textContent ?? "", /^# ルールを追加する候補/);
    // 候補はルールに足さない（下書きを見せるだけ）
    assert.equal(dom.all('.rule[data-id="suggest-npm-install"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D07 追加したルールは開いて焦点が id に来る。タイプを移すと移った先でも開いたまま。保存は今の順序を送る", async () => {
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

test("CB-D69 additionalContextFile で選んだパスは、拡張ホストが名指しした行の欄にだけ入る", async () => {
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

test("CB-D70 再読み込みは押した時点でボタンを止め、やめたら戻る。中身が届けば編集は破棄して入れ替わる", async () => {
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
    // 中身が届いたら編集は破棄する
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
    await dom.send({ type: "lock", lock: { locked: true, reason: "作業中のチケットがある（i0001-02-02）", doing: ["i0001-02-02"] } });
    assert.equal(dom.one("#lock").textContent, "作業中のチケットがある（i0001-02-02）");
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled, "錠が掛かっていれば保存は押せない");
    await dom.send({ type: "failed", message: "--lint が error を報告した" });
    assert.equal(dom.one("#status").textContent, "--lint が error を報告した");
    assert.ok(dom.one("#status").closest(".foot")?.classList.contains("error"));
  } finally {
    await dom.close();
  }
});

test("CB-D1223 notice は赤にせずに一言を出す（期待する判定のファイルが無いときの案内）", async () => {
  const dom = await openRules();
  try {
    await dom.send({ type: "notice", message: "期待する判定のファイルがまだありません: x.yml。" });
    assert.equal(dom.one("#status").textContent, "期待する判定のファイルがまだありません: x.yml。");
    const foot = dom.one("#status").closest(".foot");
    assert.ok(foot, "足元の枠がある");
    assert.ok(!foot.classList.contains("error"));
  } finally {
    await dom.close();
  }
});

test("CB-D72 再読み込みできなかった画面から中身が届いたあとも、id の確定で開いた行を state に残す", async () => {
  const dom = await openPage({ kind: "error", error: "ルールファイルを読み込めない" });
  try {
    assert.match(dom.one(".load-error").textContent ?? "", /ルールファイルを読み込めない/);
    dom.click(dom.one('button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "reload").map((message) => message.dirty), [false]);
    assert.ok(dom.one<HTMLButtonElement>('button[data-action="reload"]').disabled, "押した時点で止める");
    // 中身が届いて一覧が出る。state に残す側（id の確定）は、ここで張られていないと二度と張られない
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

test("CB-T50 実行ファイルが答えたモードで帯を出す。dry-run と disable は止めないことを言い、enable と答えがないときは言わない", async () => {
  const banners = async (mode: string): Promise<string[]> => {
    const dom = await openRules({ mode });
    try {
      return dom.all(".banner.warn:not(.hidden)").map((banner) => banner.textContent ?? "");
    } finally {
      await dom.close();
    }
  };
  const dryRun = (await banners("dry-run")).join("\n");
  assert.match(dryRun, /CCNAVI_MODE.*dry-run/);
  assert.match(dryRun, /判定と記録をします/);
  const disable = (await banners("disable")).join("\n");
  assert.match(disable, /CCNAVI_MODE.*disable/);
  // disable は判定しない。dry-run と同じ「判定と記録」の文面にしない
  assert.match(disable, /判定しません/);
  assert.doesNotMatch(disable, /記録/);
  assert.deepEqual(await banners(""), []);
  assert.deepEqual(await banners("enable"), []);
});

test("CB-T51 保存できない理由と読み込みの苦情を出す", async () => {
  const locked = await openRules({ lock: { locked: true, reason: "作業中のチケットがある（i0001-02-02）", doing: ["i0001-02-02"] } });
  try {
    assert.equal(locked.one("#lock").textContent, "作業中のチケットがある（i0001-02-02）");
    assert.ok(locked.one("#lock").classList.contains("info"), "作業中は情報色");
    assert.ok(!locked.one("#lock").classList.contains("hidden"));
  } finally {
    await locked.close();
  }
  const open = await openRules();
  try {
    assert.ok(open.one("#lock").classList.contains("hidden"));
    assert.ok(!open.one("#lock").classList.contains("info"));
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

test("CB-T52 settings.json がなければ hook の表にそう書く", async () => {
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
    "Bash", "PowerShell", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "Skill", "Agent", "Task", "WebFetch",
  ]);
  const dom = await openRules();
  try {
    assert.deepEqual(dom.all("#tool-names option").map((option) => option.getAttribute("value")), [...KNOWN_TOOLS]);
  } finally {
    await dom.close();
  }
});

test("CB-T112 ルール管理画面は注意を上部に出し、なければ出さない", async () => {
  const dom = await openRules({ rulesPath: "projects/lib/.ccnavi/config/project/rules.yml", notices: ["実行ファイルはこのファイルを読み込めない: <理由>"] });
  try {
    const warned = dom.all(".banner.warn:not(.hidden)").map((banner) => banner.textContent ?? "");
    assert.deepEqual(warned, ["実行ファイルはこのファイルを読み込めない: <理由>"]);
    assert.equal(dom.all(".banner.warn script").length, 0, "文面から要素は生えない");
    assert.equal(dom.one(".toolbar .path").textContent, "projects/lib/.ccnavi/config/project/rules.yml");
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

test("CB-T120 一覧は 1 件 1 行で既定は畳み、絞り込み欄を持ち、開いた行を id で state に残す", async () => {
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
    // id が空の行は開いていても state に残せない（次に開き直す手がかりが無い）
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
    // 別の対象へ切り替わった。前の対象の編集は破棄して、読み込み中を出す
    await dom.send({ type: "data", data: { kind: "loading", text: "web のルールを読み込み中…" } });
    assert.equal(dom.one("#ccnavi-loading").textContent, "web のルールを読み込み中…");
    assert.equal(dom.all(".rule").length, 0);
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "dirty").map((message) => message.dirty),
      [true, false],
      "編集を破棄したことも伝える",
    );
    await dom.send({ type: "data", data: { kind: "page", page: page({ rulesPath: "projects/web/.ccnavi/config/project/rules.yml" }) } });
    assert.equal(dom.all("#ccnavi-loading").length, 0);
    assert.equal(dom.all(".rule").length, 3);
    // 前の対象のルールで出した判定は、切り替え先の結果として残さない
    assert.equal(dom.all("#judge-result").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D101 ルール管理の基本の案内は見本の上でタブを切り替えて中を指し、実際のタブは動かさない。途中の切り替えは state に書かない", async () => {
  const dom = await openRules({}, { tab: "hooks" });
  try {
    assert.ok(dom.one("#tab-hooks").classList.contains("active"));
    // 案内の入口は ? 1 文字で、ヘッダ（ツールバー）の最後の子。位置は Tour.css が 6 画面とも右上に揃える
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
    // 案内は見本の上のタブを動かす。最後の 2 手順は「ルール」のタブで保存と案内の入口を指す
    assert.deepEqual(tabs, ["tab-rules", "tab-rules", "tab-rules", "tab-judge", "tab-hooks", "tab-rules", "tab-rules"]);
    assert.ok(dom.one("#tab-hooks").classList.contains("active"), "実際のタブが動いた");
    assert.equal((dom.state() as { tab?: string }).tab, "hooks");
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D192 「?」は案内の一覧を開き、選んだ案内だけが始まる。基本の案内が先頭で、閉じたら tourDone を返す", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    const items = dom.all<HTMLButtonElement>('[data-action="tour-course"]');
    assert.deepEqual(
      items.map((item) => item.dataset.course),
      loadTour("rules").courses.map((course) => course.id),
    );
    assert.equal(items[0].dataset.course, "intro");
    assert.equal(dom.all(".tour").length, 0, "一覧を開いただけで案内が始まった");
    dom.click(items[1]);
    await dom.settle();
    assert.equal(dom.all(".tour-menu").length, 0);
    assert.equal(dom.all(".tour").length, 1);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D193 使い方ごとの案内は、全部の手順で指す先が画面にあり、実際のルールではなく見本を描く。実際の編集は動かない", async () => {
  const def = loadTour("rules");
  for (const course of def.courses) {
    const dom = await openRules({}, undefined, { measure: true });
    try {
      dom.click(dom.one('[data-action="tour"]'));
      await dom.settle();
      dom.click(dom.one(`[data-action="tour-course"][data-course="${course.id}"]`));
      await dom.settle();
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${course.id} の ${index + 1} 段目（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        if (step.target !== "") {
          assert.ok(dom.all(`[data-tour="${step.target}"]`).length >= 1, `${at}: 指す先 ${step.target} が画面にありません`);
          assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先がありません）`);
        }
        // 実際のルール（git-push の日本語メッセージの見本とは別の id）は出さず、見本の id が並ぶ
        const ids = dom.all(".rule").map((row) => row.getAttribute("data-id"));
        assert.ok(ids.includes("protect-env"), at);
        assert.ok(!ids.includes("deps"), `${at}: 実際のルールが出ている`);
        assert.ok(dom.all(".banner.tour-sample").length === 1, at);
        dom.click(dom.one('[data-action="tour-next"]'));
        await dom.settle();
      }
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の手順で閉じない`);
      assert.deepEqual(dom.all(".rule").map((row) => row.getAttribute("data-id")), ["git-push", "no-rm", "deps"], "閉じたあと実際のルールに戻らない");
      assert.equal(dom.all("#dirty:not(.hidden)").length, 0, "案内が未保存にした");
      assert.deepEqual(dom.posted.filter((message) => message.type === "dirty"), []);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D194 組み合わせ条件の案内は、見本の行を開いて図を出し、手順の scene が選ぶ点を替える", async () => {
  const dom = await openRules({}, undefined, { measure: true });
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="combo-when"]'));
    await dom.settle();
    const open: string[] = [];
    const picked: string[] = [];
    const tabs: string[] = [];
    while (dom.all(".tour").length > 0) {
      await dom.settle();
      open.push(String(dom.all(".rule.open").length));
      picked.push(dom.all(".cond-inspector").map((el) => el.getAttribute("data-key") ?? "").join(","));
      tabs.push(dom.one(".pane.active").id);
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    // 行を開く前（1 番目の手順）は閉じていて、開いてからは図の点を手順ごとに替える
    assert.equal(open[0], "0");
    assert.equal(open[1], "1");
    assert.deepEqual(picked.slice(2, 5), ["r.0", "r.1.0", "r.1"]);
    assert.ok(tabs.includes("tab-judge"), "判定を試すのタブに移らない");
  } finally {
    await dom.close();
  }
});

test("CB-D195 畳んだタイプがあっても、案内は見本の行を畳まずに指す。メニューから始めた案内を閉じると焦点は「?」へ戻る。メニューは Esc で閉じて「?」へ戻る", async () => {
  const dom = await openRules({}, undefined, { measure: true });
  try {
    dom.click(dom.one('[data-action="fold-section"][data-section="deny"]'));
    await dom.settle();
    assert.ok(dom.one('[data-section="deny"]').classList.contains("folded"));
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    // Esc: 一覧が閉じ、焦点は「?」
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.all(".tour-menu").length, 0);
    assert.equal(dom.document.activeElement?.getAttribute("data-action"), "tour");
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="combo-when"]'));
    await dom.settle();
    assert.equal(dom.one('[data-section="deny"]').classList.contains("folded"), false, "案内の間も畳んだままで、指す先が見えない");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.document.activeElement?.getAttribute("data-action"), "tour", "閉じたあと焦点が「?」に戻らない");
    assert.ok(dom.one('[data-section="deny"]').classList.contains("folded"), "閉じたあと畳み直らない");
  } finally {
    await dom.close();
  }
});

test("CB-D106 読み込み中に頼まれた案内はルールが出てから始め、別の対象へ切り替わって読み込み中になったら閉じて tourDone を返す", async () => {
  const dom = await openPage({ kind: "loading", text: "ルールを読み込み中…" });
  try {
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0, "指す先がない読み込み中には出さない");
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "3 つのタブ");
    await dom.send({ type: "data", data: { kind: "loading", text: "ルールを読み込み中…" } });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 1);
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0, "ユーザが始めていない案内が出直した");
  } finally {
    await dom.close();
  }
});

test("CB-D150 設定の切り替えの欄は、選んだ対象を種類と名前で送る。対象が 1 つだけでも出す", async () => {
  const targets = [
    { kind: "workspace", name: "", label: "共通の設定" },
    { kind: "self", name: "", label: "ワークスペース" },
    { kind: "project", name: "app:x", label: "プロジェクト app:x" },
  ];
  const dom = await openRules({ target: { kind: "workspace", name: "" }, targets });
  try {
    const select = dom.one<HTMLSelectElement>("select#target");
    assert.deepEqual(
      [...select.options].map((o) => o.textContent),
      ["共通の設定", "ワークスペース", "プロジェクト app:x"],
    );
    assert.equal(select.value, "workspace:");
    dom.change(select, "project:app:x");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "switchTarget"), [{ type: "switchTarget", kind: "project", name: "app:x" }]);
  } finally {
    await dom.close();
  }

  const single = await openRules({ target: { kind: "workspace", name: "" }, targets: targets.slice(0, 1) });
  try {
    assert.equal(single.all("select#target option").length, 1);
  } finally {
    await single.close();
  }
});

test("CB-D152 読み込みに失敗した画面にも設定の切り替えの欄を出し、共通の設定へ戻れる", async () => {
  const dom = await openPage({
    kind: "error",
    error: "ファイルを読み込めません",
    target: { kind: "project", name: "app" },
    targets: [
      { kind: "workspace", name: "", label: "共通の設定" },
      { kind: "self", name: "", label: "ワークスペース" },
      { kind: "project", name: "app", label: "プロジェクト app" },
    ],
  });
  try {
    const select = dom.one<HTMLSelectElement>("select#target");
    assert.equal(select.value, "project:app");
    dom.change(select, "workspace:");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "switchTarget"), [{ type: "switchTarget", kind: "workspace", name: "" }]);
  } finally {
    await dom.close();
  }
});


test("CB-T310 統合結果は読み取り専用の表で出し、編集する 1 本の欄とは別にする。選んだ統合結果の共通のルールと設定のルールが由来付きで並ぶ", async () => {
  const sums = rulesSums(sumsBoard());
  const dom = await openRules({ target: { kind: "project", name: "lib" }, sums });
  try {
    // 開いている対象（lib）の統合結果が最初に出る。折りたたみは閉じてあるので、押して開く
    const select = dom.one<HTMLSelectElement>("select#sum-select");
    assert.equal(select.value, "lib");
    assert.deepEqual([...select.options].map((o) => o.textContent), ["統合結果（共通 + ワークスペース）", "統合結果（共通 + プロジェクト lib）"]);
    assert.match(dom.one("#sum .hint").textContent, /パスを持たないツール（Bash など）が適用するのは、全プロジェクトの設定を合算した和ではありません/);
    const rows = (section: string) => dom.all(`#sum .sum-section[data-section="${section}"] tbody tr`).map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent));
    assert.deepEqual(rows("deny").map((r) => [r[0], r[1], r[3]]), [["guard-approved", "Write|Edit", "共通の設定"], ["lib:no-tmp", "Write", "lib"]]);
    assert.deepEqual(rows("ask").map((r) => r[0]), ["lib:deps"]);
    assert.equal(dom.all('#sum .sum-section[data-section="allow"] tbody tr').length, 0);
    assert.equal(dom.one('#sum [data-count="sum"]').textContent, "3 件（common + lib）");
    // 統合結果を切り替えても、拡張ホストへは何も送らない（見せるだけ。判定も合成もしない）
    dom.change(select, "self");
    await dom.settle();
    assert.deepEqual(rows("deny").map((r) => r[0]), ["guard-approved"]);
    assert.equal(dom.all("#sum button").length, 0, "統合結果に操作はありません");
    assert.deepEqual(dom.posted.filter((m) => m.type !== "ready"), []);
    // 編集する 1 本のルールの一覧は統合結果と混ざらない
    assert.equal(dom.all("#tab-rules .rule").length, 3);
  } finally {
    await dom.close();
  }
});

test("CB-T311 共通の設定を開いているときはワークスペースの統合結果が最初に出る。統合結果がなければ（ボードを読み込めない）欄ごと出さない。読み込めない設定は空として扱っていると言う", async () => {
  const sums = rulesSums(sumsBoard()).map((s) => (s.name === "lib" ? { ...s, unreadable: "YAML として読み込めない", deny: [], ask: [] } : s));
  const dom = await openRules({ target: { kind: "workspace", name: "" }, sums });
  try {
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "self");
    dom.change(dom.one("select#sum-select"), "lib");
    await dom.settle();
    assert.match(dom.one("#sum .banner.warn").textContent, /読み込めず、その設定を空として扱っています.*YAML として読み込めない/);
  } finally {
    await dom.close();
  }
  const none = await openRules({ sums: [] });
  try {
    assert.equal(none.all("#sum").length, 0);
  } finally {
    await none.close();
  }
  const absent = await openRules();
  try {
    assert.equal(absent.all("#sum").length, 0);
  } finally {
    await absent.close();
  }
});

test("CB-T312 ルールのファイルがないのは正常で、不備の帯は出さず、欄は触れて保存できる。エディタで開くだけが押せない", async () => {
  const dom = await openRules({ exists: false, model: readRules("").model });
  try {
    assert.equal(dom.all(".banner").filter((b) => !b.classList.contains("hidden")).length, 0);
    assert.ok(dom.one<HTMLButtonElement>('button[data-action="open-rules"]').disabled);
    dom.click(dom.one('button[data-action="add"][data-section="deny"]'));
    await dom.settle();
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
    assert.equal(dom.all(".rule").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D98 except は畳んだ行に件数、開いた行に中身を見せる", async () => {
  const text = `version: 1
deny:
  - id: workspace-root
    match: Write
    regex: '^{root}[\\\\/]'
    except:
      - regex: '^{root}[\\\\/]wip[\\\\/]handoff(?:[\\\\/]|$)'
      - glob: '{root}/.claude/worktrees/*'
    message: main では編集しない
`;
  const dom = await openRules({ model: readRules(text).model });
  try {
    const row = rowSelector("workspace-root");
    assert.equal(dom.one(`${row} .sum-except`).textContent, "除外 2");
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
    const shown = dom.all(`${row} .f-except code`).map((el) => el.textContent);
    assert.deepEqual(shown, ["regex: ^{root}[\\\\/]wip[\\\\/]handoff(?:[\\\\/]|$)", "glob: {root}/.claude/worktrees/*"]);
  } finally {
    await dom.close();
  }
});

const WHEN_RULES = `version: 1
deny:
  - id: workspace-root
    match: Write|Edit
    when:
      all:
        - regex: '^{root}[\\\\/]'
        - not:
            any:
              - regex: '^{root}[\\\\/]wip'
              - glob: '{root}/.claude/worktrees/**'
    message: main では編集しない
  - id: except-rule
    match: Write
    regex: '^{root}[\\\\/]src'
    except:
      - glob: '{root}/src/gen/**'
    message: 生成物以外は直さない
`;

/** 中が空の any を持つ組み合わせ条件（誤りの見本） */
const BROKEN_WHEN = `version: 1
deny:
  - id: workspace-root
    match: Write|Edit
    when:
      all:
        - regex: '^{root}[\\\\/]'
        - not:
            any: []
    message: main では編集しない
`;

/** 組み合わせ条件の点を押して選ぶ */
async function pickCond(dom: DomPage, id: string, key: string): Promise<void> {
  dom.click(dom.one(`${rowSelector(id)} .cond-node[data-key="${key}"]`));
  await dom.settle();
}

test("CB-D155 組み合わせ条件のルールは、畳んだ行に組み合わせ条件の要約、開いた行に左から右へ伸びる図を出す。誤りは件数と一覧で言う", async () => {
  const dom = await openRules({ model: readRules(WHEN_RULES).model }, undefined, { measure: true });
  try {
    const row = rowSelector("workspace-root");
    assert.equal(dom.one(`${row} .sum-when`).textContent, "all(regex ^{root}[\\\\/], not(any(2)))");
    assert.equal(dom.all(`${row} .sum-when-error`).length, 0);
    assert.equal(dom.all(`${row} .cond-node`).length, 0, "畳んだ行には図を置かない");
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
    assert.deepEqual(
      dom.all(`${row} .cond-node`).map((node) => `${node.getAttribute("data-key")} ${node.getAttribute("data-op")}`),
      ["r all", "r.0 regex", "r.1 not", "r.1.0 any", "r.1.0.0 regex", "r.1.0.1 glob"],
    );
    assert.equal(dom.all(`${row} .react-flow__edge`).length, 5);
    // 最初は最上位のノードが選ばれていて、最上位のノードは消せない
    assert.equal(dom.one(`${row} .cond-inspector`).getAttribute("data-key"), "r");
    assert.ok(dom.one<HTMLButtonElement>(`${row} .cond-inspector [data-op-action="remove"]`).disabled);
    assert.equal(dom.all(`${row} .cond-problems li`).length, 0);
    // パターンの欄と「組み合わせ条件に変換」は出さない
    assert.equal(dom.all(`${row} .f-pattern`).length, 0);
    assert.equal(dom.all(`${row} button[data-action="to-when"]`).length, 0);
  } finally {
    await dom.close();
  }
  const bad = await openRules({ model: readRules(BROKEN_WHEN).model }, undefined, { measure: true });
  try {
    const row = rowSelector("workspace-root");
    assert.equal(bad.one(`${row} .sum-when-error`).textContent, "組み合わせ条件に誤り 1");
    bad.click(bad.one(`${row} .row-head`));
    await bad.settle();
    assert.deepEqual(bad.all(`${row} .cond-problems li`).map((li) => li.textContent), ["any の中が空です（1 個以上）"]);
    assert.ok(bad.one(`${row} .cond-node[data-key="r.1.0"]`).classList.contains("broken"));
    // 誤りを押すとその点が選ばれ、子を足せば誤りが入れ替わる
    bad.click(bad.one(`${row} .cond-problems button`));
    await bad.settle();
    assert.equal(bad.one(`${row} .cond-inspector`).getAttribute("data-key"), "r.1.0");
    bad.click(bad.one(`${row} .cond-inspector button[data-add="glob"]`));
    await bad.settle();
    assert.deepEqual(bad.all(`${row} .cond-problems li`).map((li) => li.textContent), ["glob が空です"]);
    assert.equal(bad.one(`${row} .cond-inspector`).getAttribute("data-key"), "r.1.0.0", "追加した子が選ばれる");
  } finally {
    await bad.close();
  }
});

test("CB-D156 図で組み合わせ条件を直して保存する（個別条件の文字・glob ⇄ regex・all ⇄ any・子の追加・not で包む・削除）", async () => {
  const dom = await openRules({ model: readRules(WHEN_RULES).model }, undefined, { measure: true });
  try {
    const row = rowSelector("workspace-root");
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
    // 個別条件の文字を変え、glob → regex
    await pickCond(dom, "workspace-root", "r.1.0.1");
    dom.type(dom.one(`${row} .cond-inspector .f-cond-pattern`), "{root}/.git/**");
    await dom.settle();
    dom.change(dom.one(`${row} .cond-inspector select.f-cond-op`), "regex");
    await dom.settle();
    assert.equal(dom.one(`${row} .cond-node[data-key="r.1.0.1"]`).getAttribute("data-op"), "regex");
    // any → all にして、子（glob）を足して文字を入れる
    await pickCond(dom, "workspace-root", "r.1.0");
    dom.change(dom.one(`${row} .cond-inspector select.f-cond-op`), "all");
    await dom.settle();
    dom.click(dom.one(`${row} .cond-inspector button[data-add="glob"]`));
    await dom.settle();
    dom.type(dom.one(`${row} .cond-inspector .f-cond-pattern`), "*.md");
    await dom.settle();
    // 本体を not で包む
    await pickCond(dom, "workspace-root", "r.0");
    dom.click(dom.one(`${row} .cond-inspector [data-op-action="wrap"]`));
    await dom.settle();
    assert.equal(dom.one(`${row} .cond-node[data-key="r.0"]`).getAttribute("data-op"), "not");
    assert.equal(dom.one(`${row} .cond-node[data-key="r.0.0"]`).getAttribute("data-op"), "regex");
    // 子を消す
    await pickCond(dom, "workspace-root", "r.1.0.0");
    dom.click(dom.one(`${row} .cond-inspector [data-op-action="remove"]`));
    await dom.settle();
    assert.equal(dom.one(`${row} .cond-inspector`).getAttribute("data-key"), "r.1.0", "削除したら親が選ばれる");
    assert.equal(dom.one(`${row} .sum-when`).textContent, "all(not(regex ^{root}[\\\\/]), not(all(2)))");
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedSections(dom).deny[0].when, {
      op: "all",
      children: [
        { op: "not", child: { op: "regex", pattern: "^{root}[\\\\/]" } },
        {
          op: "not",
          child: {
            op: "all",
            children: [
              { op: "regex", pattern: "{root}/.git/**" },
              { op: "glob", pattern: "*.md" },
            ],
          },
        },
      ],
    });
  } finally {
    await dom.close();
  }
});

test("CB-D157 glob / regex と除外のルールは「組み合わせ条件に変換」で組み合わせ条件になり、除外は組み合わせ条件の中へ移る。判定を試すも組み合わせ条件のまま送る", async () => {
  const dom = await openRules({ model: readRules(WHEN_RULES).model }, undefined, { measure: true });
  try {
    const row = rowSelector("except-rule");
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
    assert.equal(dom.all(`${row} .f-except code`).length, 1);
    dom.click(dom.one(`${row} button[data-action="to-when"]`));
    await dom.settle();
    assert.equal(dom.all(`${row} .f-pattern`).length, 0);
    assert.equal(dom.all(`${row} .f-except`).length, 0, "除外は組み合わせ条件の中へ移ったので欄を出さない");
    assert.deepEqual(
      dom.all(`${row} .cond-node`).map((node) => `${node.getAttribute("data-key")} ${node.getAttribute("data-op")}`),
      ["r all", "r.0 regex", "r.1 not", "r.1.0 any", "r.1.0.0 glob"],
    );
    assert.equal(dom.one(`${row} .sum-when`).textContent, "all(regex ^{root}[\\\\/]src, not(any(1)))");
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
    dom.click(dom.one("#tab-judge"));
    await dom.settle();
    dom.type(dom.one("#subject"), "/ws/src/a.ts");
    await dom.settle();
    dom.click(dom.one('button[data-action="judge"]'));
    await dom.settle();
    const judge = dom.posted.filter((m) => m.type === "judge").pop();
    assert.ok(judge);
    const sent = (judge.sections as Sections).deny[1];
    assert.equal(sent.dropExcept, true);
    assert.equal(sent.excepts, undefined);
    assert.deepEqual(sent.when, {
      op: "all",
      children: [
        { op: "regex", pattern: "^{root}[\\\\/]src" },
        { op: "not", child: { op: "any", children: [{ op: "glob", pattern: "{root}/src/gen/**" }] } },
      ],
    });
    // 書き戻すと except が消え、when が入る（拡張ホストは送られた形をそのまま書く）
    const text = readRules(WHEN_RULES).apply(judge.sections as Sections);
    assert.ok(!text.includes("except:"), text);
    assert.deepEqual(readRules(text).model.sections.deny[1].when, sent.when);
  } finally {
    await dom.close();
  }
});

test("CB-D158 except が組み合わせ条件へそのまま移せない形なら、「組み合わせ条件に変換」を押せず、理由をボタンの説明に出す", async () => {
  const text = `version: 1
deny:
  - id: bad-except
    match: Write
    glob: '*'
    except: '*.md'
    message: m
  - id: group-except
    match: Write
    glob: '*'
    except:
      - any: [{glob: a}]
    message: m
  - id: both-patterns
    match: Write
    glob: '*.md'
    regex: 'x'
    message: m
`;
  const dom = await openRules({ model: readRules(text).model }, undefined, { measure: true });
  try {
    for (const [id, why] of [
      ["bad-except", "except の 1 件目が読み込めません（except の値はリストです）"],
      ["group-except", "except の 1 件目が glob / regex 1 つのマップではありません"],
      ["both-patterns", "glob と regex を両方指定しています"],
    ]) {
      const row = rowSelector(id);
      dom.click(dom.one(`${row} .row-head`));
      await dom.settle();
      const button = dom.one<HTMLButtonElement>(`${row} button[data-action="to-when"]`);
      assert.equal(button.disabled, true, id);
      assert.ok(button.title.startsWith(why), button.title);
    }
    const ok = rowSelector("except-rule");
    const fine = await openRules({ model: readRules(WHEN_RULES).model }, undefined, { measure: true });
    try {
      fine.click(fine.one(`${ok} .row-head`));
      await fine.settle();
      assert.equal(fine.one<HTMLButtonElement>(`${ok} button[data-action="to-when"]`).disabled, false);
    } finally {
      await fine.close();
    }
  } finally {
    await dom.close();
  }
});

test("CB-D159 最上位のノードが個別条件の組み合わせ条件は、all / any で包んで子を追加できる。最上位のノードの削除ボタンは包む道を案内する", async () => {
  const text = `version: 1
deny:
  - id: leaf-root
    match: Write
    when:
      glob: '*.pem'
    message: m
`;
  const dom = await openRules({ model: readRules(text).model }, undefined, { measure: true });
  try {
    const row = rowSelector("leaf-root");
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
    const remove = dom.one<HTMLButtonElement>(`${row} .cond-inspector [data-op-action="remove"]`);
    assert.equal(remove.disabled, true);
    assert.match(remove.title, /all \/ any \/ not で包んで/);
    assert.equal(dom.all(`${row} .cond-inspector button[data-add]`).length, 0, "個別条件には子を追加できない");
    dom.click(dom.one(`${row} .cond-inspector [data-op-action="wrap-any"]`));
    await dom.settle();
    assert.equal(dom.one(`${row} .cond-node[data-key="r"]`).getAttribute("data-op"), "any");
    assert.equal(dom.one(`${row} .cond-node[data-key="r.0"]`).getAttribute("data-op"), "glob");
    assert.equal(dom.one(`${row} .cond-inspector`).getAttribute("data-key"), "r", "包んだ演算子が選ばれる");
    dom.click(dom.one(`${row} .cond-inspector button[data-add="glob"]`));
    await dom.settle();
    dom.type(dom.one(`${row} .cond-inspector .f-cond-pattern`), "*.key");
    await dom.settle();
    // 子を all で包むこともできる
    await pickCond(dom, "leaf-root", "r.0");
    dom.click(dom.one(`${row} .cond-inspector [data-op-action="wrap-all"]`));
    await dom.settle();
    assert.equal(dom.one(`${row} .cond-node[data-key="r.0"]`).getAttribute("data-op"), "all");
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedSections(dom).deny[0].when, {
      op: "any",
      children: [
        { op: "all", children: [{ op: "glob", pattern: "*.pem" }] },
        { op: "glob", pattern: "*.key" },
      ],
    });
  } finally {
    await dom.close();
  }
});

/** 案内が今いる手順（0 始まり）。`startCourse` で 0 に戻し、`toStep` で進めたり戻したりする */
const position = new WeakMap<DomPage, number>();

/** 「?」の一覧から使い方ごとの案内を始める */
async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
  position.set(dom, 0);
}

/** 案内の n 番目の手順（0 始まり）へ移る。吹き出しのタイトルが、定義の手順のタイトルと同じであることも見る */
async function toStep(dom: DomPage, id: string, index: number): Promise<void> {
  const steps = loadTour("rules").courses.find((course) => course.id === id)!.steps;
  let at = position.get(dom) ?? 0;
  while (at !== index) {
    dom.click(dom.one(at < index ? '[data-action="tour-next"]' : '[data-action="tour-back"]'));
    await dom.settle();
    at += at < index ? 1 : -1;
  }
  position.set(dom, at);
  await dom.settle();
  assert.equal(dom.one("#tour-title").textContent, steps[index].title);
}

test("CB-D300 ユースケースの一覧は、基本の案内の後ろに、追加したユースケースが定義の順で並び、タイトルと要約が出る", async () => {
  const dom = await openRules();
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    const items = dom.all<HTMLButtonElement>('[data-action="tour-course"]');
    const courses = loadTour("rules").courses;
    assert.ok(items.length >= 10);
    for (const [i, course] of courses.entries()) {
      assert.equal(items[i].dataset.course, course.id);
      assert.ok(items[i].textContent?.includes(course.title), course.id);
    }
    for (const id of ["add-rule", "context-every", "check-expectations", "suggest-rule", "except-to-when", "read-sum", "find-fold-move", "save-error"]) {
      assert.ok(items.some((item) => item.dataset.course === id), id);
    }
  } finally {
    await dom.close();
  }
});

test("CB-D301 案内の間の統合結果は見本を出し、手順の scene が枠を開いて統合結果を替える。閉じたあとは実際の統合結果に戻り、案内で選んだものを持ち越さない", async () => {
  const sums = rulesSums(sumsBoard());
  const dom = await openRules({ target: { kind: "project", name: "lib" }, sums }, undefined, { measure: true });
  try {
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "lib");
    assert.equal(dom.one<HTMLDetailsElement>("#sum").open, false);
    await startCourse(dom, "read-sum");
    await toStep(dom, "read-sum", 0);
    assert.equal(dom.one<HTMLDetailsElement>("#sum").open, true, "統合結果の枠が開かない");
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "web");
    assert.deepEqual(
      dom.all('#sum .sum-section[data-section="deny"] tbody tr').map((tr) => tr.querySelector("td")?.textContent),
      ["git-push", "no-rm-rf", "web:no-prod-deploy"],
    );
    assert.equal(dom.one('#sum [data-count="sum"]').textContent, "5 件（common + web）");
    await toStep(dom, "read-sum", 4);
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "self");
    assert.ok(dom.all('#sum .sum-section[data-section="deny"] tbody tr').some((tr) => tr.querySelector("td")?.textContent === "self:protect-secrets"));
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "lib", "閉じたあと案内で選んだ統合結果が残る");
    assert.equal(dom.one<HTMLDetailsElement>("#sum").open, false);
    assert.equal(dom.one('#sum [data-count="sum"]').textContent, "3 件（common + lib）");
    assert.deepEqual(dom.posted.filter((message) => message.type !== "ready" && message.type !== "tourDone"), []);
  } finally {
    await dom.close();
  }
});

test("CB-D302 絞り込みと畳みの案内は、scene の find で件数を「一致 / 全体」にし、fold で名指しのタイプだけを畳む。閉じたら元に戻る", async () => {
  const dom = await openRules({}, undefined, { measure: true });
  try {
    await startCourse(dom, "find-fold-move");
    await toStep(dom, "find-fold-move", 1);
    assert.equal(dom.one<HTMLInputElement>("#find").value, "git");
    assert.match(dom.one('[data-count="deny"]').textContent ?? "", /^1 \/ \d+/);
    await toStep(dom, "find-fold-move", 2);
    assert.equal(dom.one<HTMLInputElement>("#find").value, "");
    assert.ok(dom.one('[data-section="allow"]').classList.contains("folded"));
    assert.equal(dom.one('[data-section="deny"]').classList.contains("folded"), false);
    assert.equal(dom.one('[data-section="ask"]').classList.contains("folded"), false);
    // 次の段は畳まない（段ごとに全部を書き直す）
    await toStep(dom, "find-fold-move", 3);
    assert.equal(dom.all(".rule-section.folded").length, 0);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".rule-section.folded").length, 0, "案内が畳みを残した");
    assert.equal(dom.one<HTMLInputElement>("#find").value, "");
  } finally {
    await dom.close();
  }
});

test("CB-D303 保存前の検証エラーの案内は、その手順にだけ見本の報告を下の欄に出し、前後の手順と閉じたあとには残さない。実際の操作の一言も出さない", async () => {
  const dom = await openRules({}, undefined, { measure: true });
  try {
    await startCourse(dom, "save-error");
    assert.equal(dom.one("#status").textContent, "");
    await toStep(dom, "save-error", 1);
    assert.match(dom.one("#status").textContent ?? "", /設定の検証（--lint）でエラーが見つかりました/);
    assert.match(dom.one("#status").textContent ?? "", /error: db-migrate: ask に message がある/);
    assert.ok(dom.one("footer").classList.contains("error"));
    await toStep(dom, "save-error", 2);
    assert.match(dom.one("#status").textContent ?? "", /error: db-migrate/, "検証エラーを読む段の続きで消えた");
    await toStep(dom, "save-error", 4);
    assert.equal(dom.one("#status").textContent, "", "エラーの段を過ぎても残っている");
    assert.equal(dom.one("footer").classList.contains("error"), false);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.one("#status").textContent, "");
  } finally {
    await dom.close();
  }
});

test("CB-D304 一括確認と候補の案内は、その手順から結果を出す。一致しない行に印があり、閉じたあとは結果が残らない", async () => {
  const dom = await openRules({}, undefined, { measure: true });
  try {
    await startCourse(dom, "check-expectations");
    assert.equal(dom.all("#expectations-result").length, 0, "押す前に結果が出ている");
    await toStep(dom, "check-expectations", 2);
    assert.equal(dom.one(".pane.active").id, "tab-judge");
    assert.match(dom.one('[data-tour="expectations-counts"]').textContent ?? "", /allow 1\/2/);
    assert.match(dom.one('[data-tour="expectations-counts"]').textContent ?? "", /不一致 1 件/);
    assert.equal(dom.all('[data-tour="expectation-ng"]').length, 1);
    assert.match(dom.one('[data-tour="expectation-ng"]').textContent ?? "", /\.env\.example/);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all("#expectations-result").length, 0, "案内の結果が残った");

    await startCourse(dom, "suggest-rule");
    assert.equal(dom.all("#suggest-result").length, 0);
    await toStep(dom, "suggest-rule", 1);
    assert.equal(dom.all("#suggest-result .candidate").length, 2);
    assert.equal(dom.all('[data-tour="suggest-candidate"]').length, 1);
    assert.match(dom.one("#suggest-result").textContent ?? "", /expectations:/);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all("#suggest-result").length, 0, "案内の候補が残った");
    assert.deepEqual(dom.posted.filter((message) => message.type !== "ready" && message.type !== "tourDone"), []);
  } finally {
    await dom.close();
  }
});

test("CB-D305 ルールの欄の案内は、scene の open と more で行と「コンテキストの追加」を開き、判定の結果は scene の judge で見本を出す", async () => {
  const dom = await openRules({}, undefined, { measure: true });
  try {
    await startCourse(dom, "context-every");
    await toStep(dom, "context-every", 0);
    assert.equal(dom.all(".rule.open").length, 0);
    assert.match(dom.one('[data-id="db-migrate"] .sum-every').textContent ?? "", /3 回ごと/);
    await toStep(dom, "context-every", 1);
    assert.deepEqual(dom.all(".rule.open").map((row) => row.getAttribute("data-id")), ["db-migrate"]);
    assert.equal(dom.one<HTMLDetailsElement>('[data-id="db-migrate"] details.more').open, true);
    assert.equal(dom.one<HTMLInputElement>('[data-id="db-migrate"] .f-every').value, "3");
    assert.equal(dom.one('[data-tour="rule-db-migrate-every"]').tagName, "DIV");
    await toStep(dom, "context-every", 4);
    assert.deepEqual(dom.all(".rule.open").map((row) => row.getAttribute("data-id")), ["deps-change"]);
    assert.equal(dom.all('[data-tour="rule-deps-change-once"]').length, 1);
    await toStep(dom, "context-every", 6);
    assert.equal(dom.one(".pane.active").id, "tab-judge");
    assert.match(dom.one("#judge-result .response").textContent ?? "", /migrate でないかを確認する/);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all("#judge-result").length, 0, "案内の判定が残った");
  } finally {
    await dom.close();
  }
});

test("CB-D306 案内の間は、ルールを追加する・変換するなどの編集が効かない（行が増えず、未保存にならず、拡張ホストへ何も送らない）", async () => {
  const dom = await openRules({}, undefined, { measure: true });
  try {
    await startCourse(dom, "add-rule");
    await toStep(dom, "add-rule", 0);
    const before = dom.all(".rule").length;
    dom.click(dom.one('[data-tour="add-deny"]'));
    await dom.settle();
    assert.equal(dom.all(".rule").length, before, "案内の間にルールが追加された");
    dom.click(dom.one('[data-tour="add-deny"]'));
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(dom.all(".rule").map((row) => row.getAttribute("data-id")), ["git-push", "no-rm", "deps"]);

    await startCourse(dom, "except-to-when");
    await toStep(dom, "except-to-when", 2);
    const toWhen = dom.one<HTMLButtonElement>('[data-tour="rule-protect-secrets-to-when"]');
    assert.equal(toWhen.disabled, false);
    dom.click(toWhen);
    await dom.settle();
    assert.equal(dom.all('[data-id="protect-secrets"] [data-action="to-when"]').length, 1, "案内の間に組み合わせ条件へ変換された");
    assert.match(dom.one('[data-tour="rule-protect-secrets-except"]').textContent ?? "", /glob: \*\*\/secrets\/README\.md/);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 0);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "dirty"), []);
  } finally {
    await dom.close();
  }
});

test("CB-D320 案内の間は、案内の外の操作（開く・選ぶ・再読み込みする・判定・タブ・畳み・追加）が拡張ホストへも実際の state へも届かない", async () => {
  const dom = await openRules();
  try {
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 1);
    // 案内の覆いは押せないので、ボタンへ直にクリックを流して送信口とハンドラの守りを見る
    const selectors = [
      '[data-action="open-rules"]',
      '[data-action="reload"]',
      '[data-action="expectations"]',
      '[data-action="suggest"]',
      '[data-action="judge"]',
      '[data-action="fold-section"]',
      '[data-action="add"]',
      '[data-tab="hooks"]',
    ];
    for (const selector of selectors) {
      for (const el of dom.all(selector)) {
        dom.click(el);
      }
    }
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type !== "ready"),
      [],
      "案内の間に拡張ホストへ送った",
    );
    assert.equal((dom.state() as { tab?: string } | undefined)?.tab, undefined, "案内の間に実際のタブを書いた");
  } finally {
    await dom.close();
  }
});

test("CB-D391 見出しに「取り込み待ちを開く」があり、押すと openHandoff を送る。実行ファイルが handoff を持たなければ無効で、理由を出す", async () => {
  await checkHandoffLink((handoff) => openRules({ handoff }));
});

test("CB-D742 図のある手順は、本文の下に時系列の図を出し、focus の矢印だけを光らせてほかを薄くする。値は等幅で出る", async () => {
  const def = loadTour("rules");
  const course = def.courses.find((c) => c.id === "how-it-works")!;
  const dom = await openRules({}, undefined, { measure: true });
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="how-it-works"]'));
    await dom.settle();
    for (const [index, step] of course.steps.entries()) {
      await dom.settle();
      const diagram = dom.one(".tour-bubble .tour-diagram");
      const spec = def.diagrams[step.diagram];
      assert.equal(diagram.getAttribute("data-diagram"), step.diagram, `${index + 1} 番目`);
      assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length);
      assert.equal(dom.all(".tour-bubble .tour-diagram-actor").length, spec.actors.length);
      const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
      assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((id) => step.focus.includes(id)), `${index + 1} 番目の光る矢印`);
      assert.equal(dom.all(".tour-bubble .tour-diagram-step.dim").length, step.focus.length === 0 ? 0 : spec.steps.length - lit.length);
      assert.equal(dom.all(".tour-bubble code.tour-diagram-value").length, spec.steps.filter((entry) => entry.value !== "").length);
      if (index < course.steps.length - 1) {
        dom.click(dom.one('[data-action="tour-next"]'));
      }
    }
  } finally {
    await dom.close();
  }
});
