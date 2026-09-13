import { test } from "node:test";
import assert from "node:assert/strict";
import { BUILTIN_RISK_TEXT, KINDS, readRisk } from "../src/core/risk-doc.js";
import { KIND_LABELS, renderRiskPage, type RiskPage } from "../src/core/risk-render.js";

const RISK = `version: 1
factors:
  - id: ci
    points: 35
    glob: ".github/**"
    message: "<b>CI</b> に触った"
`;

function page(overrides: Partial<RiskPage> = {}): RiskPage {
  return {
    root: "/ws",
    riskPath: ".claude/ccnavi/risk.yml",
    exists: true,
    ticketControl: "enable",
    model: readRisk(RISK).model,
    lock: { locked: false, reason: "", doing: [] },
    ...overrides,
  };
}

test("CB-T80 リスク管理画面は外部資源を読まず、nonce で自分のスタイルとスクリプトだけを許す", () => {
  const html = renderRiskPage(page(), { nonce: "N0NCE" });
  assert.match(html, /default-src 'none'/);
  assert.match(html, /style-src 'nonce-N0NCE'; script-src 'nonce-N0NCE'/);
  assert.doesNotMatch(html, /https?:\/\//);
  assert.match(html, /<script nonce="N0NCE" type="application\/json" id="page">/);
  assert.match(html, /<title>ccnavi リスク管理<\/title>/);
});

test("CB-T81 埋め込む配点は JSON で、文面の < は実体にして script を閉じさせない", () => {
  const html = renderRiskPage(page(), { nonce: "n" });
  assert.match(html, /\\u003cb>CI\\u003c\/b>/);
  assert.doesNotMatch(html, /<b>CI<\/b>/);
  // 当て方の一覧と組み込みの閾値も埋め込む
  for (const kind of KINDS) {
    assert.match(html, new RegExp(`"kind":"${kind}","label":"${KIND_LABELS[kind].label}"`));
  }
  assert.match(html, /"builtinLevels":\{"medium":20,"high":40,"critical":70\}/);
});

test("CB-T82 ファイルが無ければ組み込みだと言って作るボタンを出し、あれば出さない", () => {
  const missing = renderRiskPage(page({ exists: false, model: readRisk(BUILTIN_RISK_TEXT).model }), { nonce: "n" });
  assert.match(missing, /\.claude\/ccnavi\/risk\.yml が無い。実行ファイルは組み込みの配点で数えている/);
  assert.match(missing, /data-action="create"/);
  assert.match(missing, /data-action="open-risk" disabled/);
  assert.match(missing, /"exists":false/);
  const present = renderRiskPage(page(), { nonce: "n" });
  assert.doesNotMatch(present, /data-action="create"/);
  assert.match(present, /"exists":true/);
});

test("CB-T83 チケット制御が disable なら配点が効かないと言い、enable なら言わない", () => {
  assert.match(renderRiskPage(page({ ticketControl: "disable" }), { nonce: "n" }), /チケット制御が <code>disable<\/code>/);
  assert.doesNotMatch(renderRiskPage(page(), { nonce: "n" }), /CCNAVI_TICKET_CONTROL/);
});

test("CB-T84 保存できない理由と読み込みの苦情を出す", () => {
  const locked = renderRiskPage(
    page({ lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] } }),
    { nonce: "n" },
  );
  assert.match(locked, /<p id="lock" class="lock">作業中のチケットがある（i0001-02）<\/p>/);
  const open = renderRiskPage(page(), { nonce: "n" });
  assert.match(open, /<p id="lock" class="lock hidden"><\/p>/);
  const broken = renderRiskPage(page({ model: readRisk("version: 1\nfactors: nope\n").model }), { nonce: "n" });
  assert.match(broken, /<ul class="problems">/);
  assert.match(broken, /factors が並びではない/);
});

// 画面の中のスクリプトは文字列なので、tsc は見ない。壊れても画面が黙って動かなくなるだけ。
test("CB-T85 画面に埋める script は構文として通る", () => {
  const html = renderRiskPage(page(), { nonce: "n" });
  const body = html.split('<script nonce="n">')[1].split("</script>")[0];
  assert.doesNotThrow(() => new Function(body));
});
