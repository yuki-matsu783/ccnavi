/**
 * 計画編集タブの帳面。タブ（`workflow-panel.ts`）が書き、ボード（`board-panel.ts`）が読む。
 *
 * 画面どうしは互いを import しない（`screens.ts`）ので、ボードの承認がタブの様子を知る道はここだけにする。
 * 持つのは 2 つ。
 *
 * - **未保存のタブがある親。** その親を含む一覧は承認を打たない（`approval-machine.ts` のガード）。未保存の順序の
 *   まま承認して、直したつもりの順序と違うものが通らないように。タブが閉じた・破棄した・保存したら外す
 * - **タブが最後に保存したときの `source_sha`**（親と子の提案をまとめたハッシュ）。承認の一覧を開くときに、一覧の
 *   計画の `source_sha` と比べ、違えば「保存したあとに提案が変わった」と出す。エージェントが `after` を書き戻して
 *   ユーザが保存した順序を黙って戻しても、ユーザが気づけるように。承認して承認待ちから無くなった親は忘れる
 *
 * 拡張ホストの中だけの持ち物で、残さない（VS Code を閉じれば消える）。VS Code の API には触れない。
 */

export interface WorkflowTabs {
  /** 未保存のタブがある親（識別子の順） */
  readonly unsaved: readonly string[];
  /** 親 → タブが最後に保存したときの `source_sha` */
  readonly saved: Readonly<Record<string, string>>;
}

const unsaved = new Set<string>();
const saved = new Map<string, string>();
const listeners = new Set<() => void>();

function changed(): void {
  for (const listener of [...listeners]) {
    listener();
  }
}

/** タブが未保存になった・未保存でなくなった（閉じた・破棄した・保存した） */
export function markUnsaved(ticket: string, dirty: boolean): void {
  if (unsaved.has(ticket) === dirty) {
    return;
  }
  if (dirty) {
    unsaved.add(ticket);
  } else {
    unsaved.delete(ticket);
  }
  changed();
}

/** タブが提案を書き換えた。`sha` は書いたあとに読み直したハッシュ */
export function rememberSaved(ticket: string, sha: string): void {
  if (saved.get(ticket) === sha) {
    return;
  }
  saved.set(ticket, sha);
  changed();
}

/** 承認した親を忘れる（あとで出す改版の提案を、前の保存と比べない） */
export function forgetSaved(tickets: readonly string[]): void {
  let any = false;
  for (const ticket of tickets) {
    any = saved.delete(ticket) || any;
  }
  if (any) {
    changed();
  }
}

/**
 * 忘れてよい保存のハッシュ（識別子の順）。承認待ちの親の提案（`todoParents`）に無い親のぶん。承認された・
 * 取り下げられた親を、あとで出す改版の提案と比べない。ボードを読み直したときに呼ぶ
 */
export function staleSaved(saved: Readonly<Record<string, string>>, todoParents: readonly string[]): string[] {
  const todo = new Set(todoParents);
  return Object.keys(saved)
    .filter((ticket) => !todo.has(ticket))
    .sort();
}

/** 今の帳面のコピー */
export function workflowTabs(): WorkflowTabs {
  return { unsaved: [...unsaved].sort(), saved: Object.fromEntries(saved) };
}

/** 帳面が変わったら呼ぶ。戻り値で止める */
export function onWorkflowTabs(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 帳面を空にする。テストのためだけにある */
export function resetWorkflowTabs(): void {
  unsaved.clear();
  saved.clear();
  listeners.clear();
}
