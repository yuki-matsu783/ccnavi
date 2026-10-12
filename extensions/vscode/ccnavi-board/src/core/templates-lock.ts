// テンプレートの編集を錠が止めるか。画面と拡張ホストで同じ規則を使う
import type { Lock } from "./lock.js";
import type { EditRequest } from "./templates-view.js";

/** 編集を止める理由。止めなければ空。下書きだけを触る操作は効力が無いので止めない。common は全体の錠、project は層の錠（無ければ全体の錠）。move は行き先の錠も見る */
export function editLockReason(request: EditRequest, lock: Lock, projectLocks: Readonly<Record<string, Lock>>): string {
  if (request.op === "save" || request.op === "delete-draft" || request.on === "draft") {
    return "";
  }
  const subject = request.on === "common" ? lock : (projectLocks[request.layer] ?? lock);
  if (subject.locked) {
    return subject.reason;
  }
  if (request.op === "move") {
    const destination = request.to === "common" ? lock : (projectLocks[request.toLayer] ?? lock);
    if (destination.locked) {
      return destination.reason;
    }
  }
  return "";
}
