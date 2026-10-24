/**
 * ボード画面の案内の見本（`tours/board.yml` の `sample:`）の検査。見本は実行ファイルの JSON と同じ形で書き、
 * 画面は `tour-board.ts` で実際の読み取り（`parseBoardJson`）と組み立て（`buildBoard`）を通す。
 * 使い方ごとの案内が見せたい状態が見本に揃っていることと、YAML の `scene` / `target` が見本と合うことを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { archivedTitleOf, COLUMNS, type Board, type Card } from "../../src/core/board.js";
import { parseBoardJson } from "../../src/core/model.js";
import { OVERLAY_NAMES, parseBoardSample } from "../../src/core/tour-board.js";
import { INTRO_ID } from "../../src/core/tour-def.js";
import { boardPage } from "../helpers/board.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("board");
const sample = parseBoardSample(def.sample, "/ws", "2026-10-12");
const cards: readonly Card[] = sample.board.columns.flatMap((column) => column.cards);
const card = (id: string): Card => {
  const found = cards.find((c) => c.id === id);
  assert.ok(found, `見本に ${id} がありません`);
  return found;
};

function idsOf(board: Board, state: string): string[] {
  return board.columns.find((column) => column.state === state)!.cards.map((c) => c.id);
}

test("CB-T420 ボードの見本は、実行ファイルの出力と同じ形を buildBoard に通した列とカードを持ち、どれも見本と分かる題で、root と時刻は渡したものを使う", () => {
  assert.deepEqual(idsOf(sample.board, "todo"), ["sample-a", "sample-a-01-01", "sample-b-03-01"]);
  assert.deepEqual(idsOf(sample.board, "doing"), ["sample-b", "sample-b-02-01", "sample-c", "sample-c-01-01", "sample-e"]);
  assert.deepEqual(idsOf(sample.board, "done"), ["sample-b-01-01"]);
  assert.deepEqual(idsOf(sample.board, "cancelled"), ["sample-f"]);
  assert.deepEqual(idsOf(sample.board, "archived"), ["sample-g", "sample-i"]);
  assert.ok(cards.every((c) => c.title.startsWith("（見本）")));
  assert.equal(sample.board.root, "/ws");
  assert.equal(sample.board.generatedAt, "2026-10-12");
  assert.deepEqual(sample.board.projects, ["web"]);
  assert.deepEqual(sample.board.pendingApproval, ["sample-a", "sample-a-01-01", "sample-b-03-01"]);
  // 集計は置き場のカードだけ（アーカイブは数えない）
  assert.equal(sample.board.totalCount, 10);
  assert.equal(sample.board.remainingCount, 8);
  assert.equal(sample.board.issueCount, 1);
  assert.equal(sample.board.archivedCount, 2);
  assert.deepEqual(
    sample.board.parents.map((p) => p.id),
    ["sample-a", "sample-b", "sample-c", "sample-e", "sample-f"],
  );
});

/** 読み取りは知らない鍵を黙って捨てる。見本の綴り違い（`worktee:` など）を、捨てられる前に見つける */
function lostKeys(raw: unknown, parsed: unknown, at: string, free: ReadonlySet<string>): string[] {
  if (Array.isArray(raw)) {
    return raw.flatMap((item, i) => lostKeys(item, (parsed as unknown[] | undefined)?.[i], `${at}[${i}]`, free));
  }
  if (typeof raw !== "object" || raw === null) {
    return [];
  }
  const got = (parsed ?? {}) as Record<string, unknown>;
  return Object.entries(raw).flatMap(([key, value]) => {
    if (!(key in got)) {
      return [`${at}.${key}`];
    }
    return free.has(key) ? [] : lostKeys(value, got[key], `${at}.${key}`, free);
  });
}

test("CB-T421 ボードの見本の鍵はどれも読み取りが読む（綴り違いが既定値へ黙って化けない）", () => {
  const raw = (def.sample as { board: Record<string, unknown> }).board;
  const parsed = parseBoardJson(JSON.stringify({ version: 1, root: "/ws", generated_at: "x", ...raw }));
  assert.ok(parsed.ok);
  // 中身を実行ファイルが決める自由な欄（計画の項・印・統合結果・リスクの内訳）は、鍵を数えない
  const free = new Set(["plan", "marks", "states", "risk", "judge", "feedback", "ready", "close_early"]);
  assert.deepEqual(lostKeys(raw, parsed.ok ? parsed.board : undefined, "board", free), []);
});

test("CB-T422 見本には、使い方ごとの案内が見せる状態が揃っている（札の各種・レビュー待ち・HIGH・不備・要確認・先行待ち・取り消し・フェーズの進み・フロー・計画編集・マージ状況）", () => {
  // 承認待ち: 1 件ずつ承認する操作と、「計画編集」の入口を持つ。札は「承認待ち」
  assert.ok(card("sample-a").pendingApproval && card("sample-a").actions.some((a) => a.kind === "approve"));
  assert.ok(card("sample-a").workflow);
  assert.equal(card("sample-a").copyStatus, "none");
  assert.equal(card("sample-a").status.label, "承認待ち");
  assert.equal(card("sample-b-03-01").status.label, "承認待ち");
  // レビュー待ち: 親のフェーズ行に［MR を開く］［レビュー完了］、子のカードはあなたの番に同じ操作。札はどちらも「レビュー待ち」
  const chip = card("sample-c").phases[0];
  assert.ok(chip.reviewWaiting && chip.gateClosed && chip.status === "レビュー待ち" && chip.actions.some((a) => a.kind === "review"));
  assert.equal(card("sample-c").mrNumber, 12);
  assert.ok(card("sample-c").mrUrl.startsWith("https://") && !card("sample-c").mrUrl.includes("#"));
  assert.equal(card("sample-c").status.label, "レビュー待ち");
  assert.ok(card("sample-c-01-01").gateClosed && card("sample-c-01-01").reviewWaiting);
  assert.equal(card("sample-c-01-01").status.label, "レビュー待ち");
  assert.ok(card("sample-c-01-01").actions.some((a) => a.kind === "review"));
  // HIGH のリスク・不備・書き込み停止（札は要確認）
  assert.equal(card("sample-c-01-01").riskLevel, "HIGH");
  assert.ok(card("sample-e").issues.length === 1 && !card("sample-e").worktreeExists && card("sample-e").copyStatus === "open");
  assert.equal(card("sample-e").status.label, "要確認");
  // 先行待ち
  assert.deepEqual(card("sample-b-03-01").predecessorsUnmet.map((p) => p.ticket), ["sample-b-02-01"]);
  // 作業中・完了・取り消しとアーカイブ
  assert.equal(card("sample-b").status.label, "作業中");
  assert.equal(card("sample-b-02-01").status.label, "作業中");
  assert.equal(card("sample-b-01-01").status.label, "完了");
  assert.ok(card("sample-f").cancelledAt !== "");
  assert.equal(card("sample-f").status.label, "取り消し");
  assert.equal(card("sample-g").column, "archived");
  // フェーズが進んだ親: 終了・進行中・未着手（フェーズ行は「レビュー省略・進行中・これから」と言う）
  assert.deepEqual(card("sample-b").phases.map((p) => p.state), ["ended", "active", "planned"]);
  assert.deepEqual(card("sample-b").phases.map((p) => p.status), ["レビュー省略", "進行中", "これから"]);
  assert.ok(card("sample-b").stage !== "");
  // フロー: 作成・着手中の閲覧・終了済みの閲覧
  assert.equal(card("sample-a-01-01").flow?.exists, false);
  assert.equal(card("sample-a-01-01").flow?.locked, false);
  assert.equal(card("sample-b-02-01").flow?.lock_kind, "doing");
  assert.equal(card("sample-b-01-01").flow?.exists, true);
  // 要対応のカードと、そうでないカードがある（「要対応のみ」で絞れて、何かが残る）
  assert.ok(cards.some((c) => c.attention) && cards.some((c) => !c.attention && c.column !== "archived"));
  // プロジェクトの絞り込みで残るカードと隠れるカードがある
  assert.ok(cards.some((c) => c.project === "web") && cards.some((c) => c.project === ""));
  // どのカードからもファイルは開かない（見本は実際のファイルを指さない。退避のカードだけ見本の路を持つ）
  assert.ok(cards.filter((c) => c.column !== "archived").every((c) => c.openPath === ""));
  // 動いた表示は、見本の中の作業中のカードに付く
  assert.deepEqual(sample.moved, [{ id: "sample-b-02-01", from: "todo", to: "doing" }]);
  assert.equal(card("sample-b-02-01").column, "doing");
  // マージ状況の帯の見本: 5 つの行（マージ待ち・マージ済み・閉じた・確認できない・記録が不完全）。題は退避の同じ親から引ける
  assert.deepEqual(sample.merge?.rows.map((r) => r.state), ["open", "merged", "closed", "unknown", "incomplete"]);
  assert.equal(archivedTitleOf(sample.board, "", "sample-i"), "（見本）API の応答にキャッシュを付ける");
  assert.equal(archivedTitleOf(sample.board, "", "sample-h"), undefined);
});

test("CB-T423 承認・残った指摘・渡す文のオーバーレイの見本は、実行ファイルの JSON の読み取りを通り、見本のチケットと合う", () => {
  assert.deepEqual([...OVERLAY_NAMES].sort(), Object.keys(sample.overlays).sort());
  const approval = sample.overlays.approval;
  assert.equal(approval.kind, "preview");
  if (approval.kind === "preview") {
    assert.deepEqual(approval.preview.batch.map((b) => b.ticket), sample.board.pendingApproval);
    assert.ok(approval.preview.plans.length >= 1 && approval.preview.plans[0].items.length >= 3);
    assert.ok(approval.preview.text.includes("■ 全体計画"));
    assert.equal(approval.preview.rejected.length, 0);
  }
  const decide = sample.overlays.decide;
  assert.equal(decide.kind, "decidePreview");
  if (decide.kind === "decidePreview") {
    // 残った指摘のある親・フェーズは、見本のレビュー待ちのフェーズ
    const chip = card(decide.preview.parent).phases.find((p) => p.number === decide.preview.phase);
    assert.ok(chip?.reviewWaiting);
    assert.ok(decide.preview.threads.length >= 2 && decide.preview.digest.startsWith("v2."));
    assert.equal(decide.preview.mr.number, card(decide.preview.parent).mrNumber);
  }
  assert.equal(sample.overlays.done.kind, "done");
});

test("CB-T424 見本の読み取りは、列でない名前・オブジェクトでない見本・壊れた承認の一覧を、読めたことにしない", () => {
  const raw = def.sample as Record<string, unknown>;
  assert.throws(() => parseBoardSample({ ...raw, moved: [{ id: "x", to: "dooing" }] }, "/ws", "t"), /列の名前/);
  assert.throws(() => parseBoardSample([], "/ws", "t"), /オブジェクト/);
  const overlays = raw.overlays as Record<string, unknown>;
  assert.throws(() => parseBoardSample({ ...raw, overlays: { ...overlays, decide: { ...(overlays.decide as object), digest: "short" } } }, "/ws", "t"), /digest/);
  // マージ状況の見本の状態は、帯が描ける 6 つのどれかだけ
  assert.throws(() => parseBoardSample({ ...raw, merge: { rows: [{ parent: "x", state: "pending" }] } }, "/ws", "t"), /state/);
  assert.throws(() => parseBoardSample({ ...raw, merge: { rows: [{ state: "open" }] } }, "/ws", "t"), /parent/);
});

/** 案内の `scene` が読む鍵（`board.yml` の先頭のコメントと `board/App.tsx` が読むもの） */
const SCENE_KEYS = ["overlay", "project", "parent", "attention", "archived", "fold", "details", "merge"];

test("CB-T425 ボードの案内の scene は、見本にある絞り込み・列・オーバーレイの名前だけを指し、絞り込んでも見本のカードが残る（綴り違いが既定へ黙って戻らない）", () => {
  const parents = new Set([...sample.board.parents, ...sample.board.archivedParents].map((p) => p.id));
  const states = new Set(COLUMNS.map((c) => c.state));
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順`;
      const scene = step.scene;
      for (const key of Object.keys(scene)) {
        assert.ok(SCENE_KEYS.includes(key), `${at}: scene の鍵 ${key} を画面は読まない`);
      }
      assert.ok(scene.overlay === undefined || (OVERLAY_NAMES as readonly string[]).includes(String(scene.overlay)), `${at}: overlay ${String(scene.overlay)}`);
      assert.ok(scene.project === undefined || scene.project === "" || sample.board.projects.includes(String(scene.project)), `${at}: project ${String(scene.project)} が見本にありません`);
      assert.ok(scene.parent === undefined || parents.has(String(scene.parent)), `${at}: parent ${String(scene.parent)} が見本にありません`);
      assert.ok(scene.attention === undefined || typeof scene.attention === "boolean", `${at}: attention は真偽`);
      assert.ok(scene.archived === undefined || typeof scene.archived === "boolean", `${at}: archived は真偽`);
      assert.ok(scene.merge === undefined || (typeof scene.merge === "boolean" && sample.merge !== undefined), `${at}: merge は真偽で、見本に merge がある`);
      for (const id of String(scene.details ?? "").split(",").filter((item) => item.trim() !== "")) {
        assert.ok(cards.some((c) => c.id === id.trim()), `${at}: details の ${id} のカードが見本にありません`);
      }
      for (const state of String(scene.fold ?? "").split(",").filter((item) => item.trim() !== "")) {
        assert.ok(states.has(state.trim() as never), `${at}: fold の ${state} は列の名前でない`);
      }
      // 絞り込んだ結果、見えるカードが 1 枚も無いと、案内が指す先を失う
      const visible = cards.filter(
        (c) =>
          (scene.archived === true || c.column !== "archived") &&
          (scene.project === undefined || c.project === scene.project) &&
          (scene.parent === undefined || c.family === scene.parent) &&
          (scene.attention !== true || c.attention),
      );
      assert.ok(visible.length >= 1, `${at}: 絞り込みで見本のカードが 1 枚も残らない`);
    }
  }
});

/** 画面が付ける印（`data-tour`）の固定の名前。`board.yml` の先頭のコメントと同じ一覧 */
const FIXED_TARGETS = new Set([
  "summary", "filter-project", "filter-parent", "filter-attention", "filter-archived", "refresh", "merge-check", "approve", "tour", "board",
  "merge-band", "merge-retire", "merge-abandon",
  "status", "turn", "details", "notes", "badge-blocked", "badge-preds", "badge-worktree", "badge-risk", "badge-seen", "badge-issue", "badge-unsent",
  "approve-one", "review-done", "mr", "phases", "phase-review", "moved", "flow-create", "flow-edit", "flow-locked", "workflow-button",
  "approval-batch", "approval-plan", "approve-confirm", "approval-prompt", "approval-handover", "edit-workflow", "decide-threads", "decide-consult", "decide-confirm",
]);

test("CB-T426 ボードの案内の指す先は、画面が付ける印か、見本にあるカード・列の印だけで、基本の案内は今の題と順序、最後は案内の入口を指す", () => {
  const states = new Set(COLUMNS.map((c) => c.state as string));
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
      // 指す先を持たないのは、画面の外の見た目を画像で見せる手順だけ（吹き出しが中ほどに出る）
      assert.ok(step.target !== "" || step.image !== "", `${at}: 指す先がない（吹き出しが中ほどに出る）`);
      if (step.target === "") {
        continue;
      }
      if (step.target.startsWith("card-")) {
        assert.ok(cards.some((c) => `card-${c.id}` === step.target), `${at}: ${step.target} のカードが見本にありません`);
      } else if (step.target.startsWith("column-")) {
        assert.ok(states.has(step.target.slice("column-".length)), `${at}: ${step.target} の列がありません`);
      } else {
        assert.ok(FIXED_TARGETS.has(step.target), `${at}: 指す先 ${step.target} は画面が付ける印にない`);
      }
    }
  }
  const intro = def.courses[0];
  assert.equal(intro.id, INTRO_ID);
  assert.deepEqual(intro.steps.map((s) => s.title), ["集計", "絞り込み", "列", "状態の札", "あなたの番", "詳細", "承認", "更新", "マージ状況を確認", "案内"]);
  assert.equal(intro.steps.at(-1)?.target, "tour");
});

test("CB-T427 使い方ごとの案内は 1 本 4〜9 手順（マージまでの流れだけは 28 手順まで）で、「〜したいとき」の題と 1 行の説明を持つ。案内の鍵は重ならない", () => {
  const useCases = def.courses.slice(1);
  assert.ok(useCases.length >= 3 && useCases.length <= 9);
  // マージまでの流れ（to-merge）は基本の案内のすぐ次に出る
  assert.deepEqual(
    useCases.map((c) => c.id),
    ["to-merge", "approve", "review", "merge", "attention", "progress", "filter", "closed", "flow"],
  );
  for (const course of useCases) {
    assert.ok(course.steps.length >= 4 && course.steps.length <= (course.id === "to-merge" ? 28 : 9), `${course.id}: ${course.steps.length} 手順`);
    assert.match(course.title, /たいとき$/, `${course.id}: 利用者の目的の題でない`);
    assert.ok(course.summary.length > 0 && course.summary.length <= 60, `${course.id}: 説明が長い`);
  }
  assert.equal(new Set(def.courses.map((c) => c.id)).size, def.courses.length);
});

test("CB-T1172 マージまでの流れの案内は、issue の作り方（GitLab）・Claude Code の入力欄・MR の作成・GitLab の差分とスレッド・マージの画像を、この順で持つ", () => {
  const course = def.courses.find((c) => c.id === "to-merge");
  assert.ok(course, "to-merge の案内がありません");
  const withImage = course.steps.filter((step) => step.image !== "");
  assert.deepEqual(withImage.map((step) => step.image), ["gitlab-issues-menu", "gitlab-issues-list", "gitlab-new-issue", "claude-code-prompt", "gitlab-new-mr", "gitlab-line-comment", "gitlab-start-thread", "gitlab-pending-review", "gitlab-mr-approval", "gitlab-mark-ready", "gitlab-mr-merge", "gitlab-merged"]);
  assert.ok(withImage.every((step) => step.alt !== "" && step.target === ""));
  // 作業を頼む → 承認 → 作業 → レビュー → マージ → 片付けの順に、画面の部品を指す
  const targets = course.steps.map((step) => step.target).filter((target) => target !== "");
  assert.deepEqual(
    targets.filter((target) => ["approval-batch", "decide-threads", "decide-confirm", "column-archived", "merge-check", "merge-retire"].includes(target)),
    ["approval-batch", "decide-threads", "decide-confirm", "column-archived", "merge-check", "merge-retire"],
  );
});

test("CB-T1174 ボードの入れ物の CSP は、画像を data: だけに許し、外の URL は許さない", () => {
  const csp = /<meta http-equiv="Content-Security-Policy" content="([^"]*)"/.exec(boardPage({ kind: "board", board: sample.board }))?.[1] ?? "";
  assert.match(csp, /default-src 'none'/);
  assert.deepEqual(csp.match(/img-src [^;]*/g), ["img-src data:"]);
});
