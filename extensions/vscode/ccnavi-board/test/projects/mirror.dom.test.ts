/**
 * プロジェクト管理画面（React）のミラーの表示を happy-dom で動かす。カードの状態とずれの中身、「共通設定を同期」と
 * 「すべて同期」が送るもの、押せないとき（同期中・古い実行ファイル・写すものが無い）、最終確認の時刻、案内の間は送らないこと。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

import { parseMirrorList, type MirrorList, type MirrorStatus } from "../../src/core/mirrormodel.js";
import type { MirrorData } from "../../src/core/projects-view.js";
import { cardSelector, openPage, page, row } from "../helpers/projects.js";

function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

/** 実行ファイルとの取り決めの例と同じ結果 */
function list(overrides: Record<string, unknown> = {}): MirrorList {
  const parsed = parseMirrorList(
    JSON.stringify({
      version: 1,
      ok: true,
      error: "",
      common_empty: false,
      projects: [
        { name: "app", state: "synced", changes: [], error: "", expect: "" },
        {
          name: "web",
          state: "differs",
          changes: [
            { rel: ".ccnavi/config/common/rules.yml", action: "update" },
            { rel: ".ccnavi/config/common/templates/a.yml", action: "create" },
            { rel: ".ccnavi/config/common/old.yml", action: "delete" },
          ],
          error: "",
          expect: "web-sha",
        },
        { name: "x", state: "error", changes: [], error: "書き込めません", expect: "" },
      ],
      expect: "all-sha",
      ...overrides,
    }),
  );
  assert.ok(parsed.ok);
  return parsed.value;
}

function mirror(status: MirrorStatus = { kind: "ok", list: list() }, busy = false, checkedAt = "14:05"): MirrorData {
  return { status, busy, checkedAt };
}

const ROWS = [row({ name: "app", rel: "projects/app" }), row({ name: "web", rel: "projects/web" }), row({ name: "x", rel: "projects/x" })];

function open(data: MirrorData) {
  return openPage({ kind: "page", page: page(ROWS), mirror: data });
}

test("CB-D734 カードにミラーの状態が出る。ずれの中身は畳んであり、開くと種類とパスが出る。最終確認の時刻はヘッダに出る", async () => {
  const dom = await open(mirror());
  try {
    const state = (name: string): string => text(dom.one(`${cardSelector(name)} .mirror-state`));
    assert.equal(state("app"), "同期済み");
    assert.equal(state("web"), "ずれ 3 件");
    assert.equal(state("x"), "調べられません 書き込めません");
    const details = dom.one(`${cardSelector("web")} details.mirror-changes`);
    assert.equal(details.hasAttribute("open"), false);
    assert.deepEqual(
      dom.all(`${cardSelector("web")} .mirror-changes li`).map((li) => text(li)),
      ["更新 .ccnavi/config/common/rules.yml", "追加 .ccnavi/config/common/templates/a.yml", "削除 .ccnavi/config/common/old.yml"],
    );
    assert.equal(dom.all(`${cardSelector("app")} details.mirror-changes`).length, 0);
    assert.equal(text(dom.one(".summary .mirror-checked")), "ミラーの最終確認 14:05");
  } finally {
    await dom.close();
  }
});

test("CB-D735 「共通設定を同期」はずれのある行だけ押せて、その名前と行の expect を送る。「すべて同期」は件数を出し、名前を空にして全体の expect を送る", async () => {
  const dom = await open(mirror());
  try {
    const button = (name: string): HTMLButtonElement => dom.one<HTMLButtonElement>(`${cardSelector(name)} button[data-action="mirror-sync"]`);
    assert.equal(button("app").disabled, true);
    assert.equal(button("x").disabled, true);
    assert.equal(button("web").disabled, false);
    assert.equal(text(button("web")), "共通設定を同期");
    const all = dom.one<HTMLButtonElement>('button[data-action="mirror-sync-all"]');
    assert.equal(text(all), "すべて同期（1 件）");
    assert.equal(all.disabled, false);
    dom.click(button("web"));
    await dom.settle();
    dom.click(all);
    await dom.settle();
    assert.deepEqual(dom.posted.slice(1), [
      { type: "mirrorSync", names: ["web"], expect: "web-sha" },
      { type: "mirrorSync", names: [], expect: "all-sha" },
    ]);
  } finally {
    await dom.close();
  }
});

test("CB-D736 同期中はどのボタンも押せない。終われば（busy が偽の中身が届けば）戻る", async () => {
  const dom = await open(mirror(undefined, true));
  try {
    const disabled = (): boolean[] => dom.all<HTMLButtonElement>('button[data-action^="mirror-sync"]').map((b) => b.disabled);
    assert.deepEqual(disabled(), [true, true, true, true]);
    assert.ok(dom.one('button[data-action="mirror-sync-all"]').className.includes("busy"));
    await dom.send({ type: "data", data: { kind: "page", page: page(ROWS), mirror: mirror() } });
    assert.deepEqual(disabled(), [false, true, false, true]);
  } finally {
    await dom.close();
  }
});

test("CB-D737 古い実行ファイル・調べられない・写すものが無いときは帯に理由を出し、ボタンは押せない", async () => {
  const cases: [MirrorStatus, RegExp][] = [
    [{ kind: "unsupported", reason: "mirror の機能を持ちません（古い版です）" }, /ミラーのずれを調べられません。mirror の機能を持ちません（古い版です）/],
    [{ kind: "failed", error: "60 秒で中断しました" }, /調べられませんでした。60 秒で中断しました/],
    [{ kind: "ok", list: list({ ok: false, error: "共通レイヤーを読み込めません" }) }, /調べられませんでした。共通レイヤーを読み込めません/],
    [{ kind: "ok", list: list({ common_empty: true }) }, /写すものがありません/],
  ];
  for (const [status, banner] of cases) {
    const dom = await open(mirror(status));
    try {
      assert.match(text(dom.one(".mirror-banner")), banner);
      const buttons = dom.all<HTMLButtonElement>('button[data-action^="mirror-sync"]');
      assert.ok(buttons.length > 0);
      assert.ok(buttons.every((b) => b.disabled), status.kind);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D738 ミラーをまだ調べていない中身（mirror が無い）では、ミラーの欄もボタンも出さない。調べている間は「調べています」", async () => {
  const none = await openPage({ kind: "page", page: page(ROWS) });
  try {
    assert.equal(none.all(".mirror-field").length, 0);
    assert.equal(none.all('button[data-action="mirror-sync-all"]').length, 0);
    assert.equal(none.all(".mirror-checked").length, 0);
  } finally {
    await none.close();
  }
  const waiting = await open(mirror({ kind: "waiting" }, false, ""));
  try {
    assert.equal(text(waiting.one(`${cardSelector("web")} .mirror-state`)), "調べています…");
    assert.equal(waiting.all(".mirror-checked").length, 0);
    assert.equal(waiting.one<HTMLButtonElement>('button[data-action="mirror-sync-all"]').disabled, true);
  } finally {
    await waiting.close();
  }
});
