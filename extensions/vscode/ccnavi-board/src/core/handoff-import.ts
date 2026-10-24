/**
 * 取り込み待ち画面の「取り込む」の手順。実行ファイルを呼ぶ部分は `HandoffDeps` として受け取り、
 * ここでは呼ぶ順と、呼ぶ前に確かめることだけを持つ。実行ファイルが無くても試せる（呼び出しの層を分けてある）。
 *
 * 順は次のとおり。どこかではじかれたら実行ファイルの取り込みは呼ばず、理由を返す。
 *
 * 1. 頼まれた対象が、画面に見せた一覧にあり、取り込める（`board` が真・状態が new か changed・error 無し）こと
 * 2. 作業中のチケットが無いこと。錠は押した時点で取り直す（キャッシュした錠は、チケットが動いてから更新されるまでの隙を通すため）。
 *    掛けるのは `template` と `layer-config` の種類だけ
 * 3. 取り込みたいパスだけを指して見るだけをもう一度呼び、返った `expect` を使うこと。拡張はハッシュを自分で組み直さない。
 *    見せた一覧のあとで下書きか取り込み先が変わっていれば（対象ごとの `entry_sha` が違えば）、取り込まない
 * 4. `expect` を渡して取り込みを呼ぶ
 *
 * VS Code の API にも node にも触れない。
 */
import type { HandoffEntry, HandoffList, HandoffResult } from "./handoff-view.js";
import { blockReason, entryOf, refusalReason, samePath, terminalCommand } from "./handoffmodel.js";
import type { Lock } from "./lock.js";

/** 見るだけの呼び出しの答え。`ccnavi.ts` の `RunResult` と同じ形 */
export type ViewAnswer = { readonly ok: true; readonly value: HandoffList } | { readonly ok: false; readonly error: string };

/** 実行ファイルへの窓口。本物は `ccnavi.ts`、試験では差し替える */
export interface HandoffDeps {
  /** `user handoff [<パス>…] --json`（見るだけ）。パスを渡すと、そのパスが `selected` になり、`expect` はその集合のものになる */
  view(paths: readonly string[]): Promise<ViewAnswer>;
  /** `user handoff <パス…> --yes --expect <ハッシュ> --via board --json`。失敗は `{ok:false}` の結果で返す（投げない） */
  apply(paths: readonly string[], expect: string): Promise<HandoffResult>;
  /** 見ているツリーに掛ける錠を今の状態で取り直す。確かめられなければ掛かった錠を返す */
  lock(): Promise<Lock>;
}

/** 取り込みの手順の結果 */
export interface ImportOutcome {
  readonly result: HandoffResult;
  /** 実行ファイルの取り込みを呼んだか。呼んだなら、成否によらず一覧を読み直す（書けたかが分からない失敗があるため） */
  readonly attempted: boolean;
}

function refuse(error: string): ImportOutcome {
  return { result: { ok: false, error }, attempted: false };
}

/**
 * `shown`（画面に見せた一覧）の `paths` を取り込む。
 * 頼んだ対象は大文字小文字の違いを無視して引くが、実行ファイルに渡すのは一覧にあるとおりの表記。
 */
export async function importSelected(deps: HandoffDeps, shown: HandoffList, paths: readonly string[]): Promise<ImportOutcome> {
  if (paths.length === 0) {
    return refuse("取り込む対象が選ばれていません");
  }
  // 一覧にあるとおりの表記に直す。同じ対象を 2 回指していたら 1 つにする
  const entries: HandoffEntry[] = [];
  for (const path of paths) {
    const found = entryOf(shown.entries, path);
    if (found === undefined) {
      return refuse(`一覧にない対象です: ${path}`);
    }
    if (!entries.some((e) => samePath(e.path, found.path))) {
      entries.push(found);
    }
  }

  // 錠は押した時点で取り直す
  const lock = await deps.lock();
  for (const entry of entries) {
    const reason = blockReason(entry, lock);
    if (reason !== "") {
      return refuse(`${entry.path}: ${reason}`);
    }
  }

  // 取り込みたいパスだけを指して見るだけをもう一度呼ぶ。返った expect を使う
  const wanted = entries.map((e) => e.path);
  const viewed = await deps.view(wanted);
  if (!viewed.ok) {
    return refuse(viewed.error);
  }
  const chosen = viewed.value.entries.filter((e) => e.selected);
  if (chosen.length !== entries.length) {
    return refuse("取り込む対象が、見せた一覧と違います。更新して読み直してください");
  }
  for (const before of entries) {
    const now = entryOf(chosen, before.path);
    if (now === undefined) {
      return refuse(`${before.path}: 見せたあとに一覧から外れました。更新して読み直してください`);
    }
    if (now.entry_sha !== before.entry_sha) {
      return refuse(`${before.path}: 見せたあとに下書きか取り込み先が変わりました。更新して読み直してください`);
    }
    const reason = blockReason(now, lock);
    if (reason !== "") {
      return refuse(`${now.path}: ${reason}`);
    }
  }
  if (!viewed.value.ok) {
    return refuse("選んだ対象に、検査を通らないものがあります。更新して読み直してください");
  }
  const expect = viewed.value.expect;
  if (expect === "") {
    return refuse("実行ファイルが expect を返しませんでした。実行ファイルを更新してください");
  }

  return { result: await deps.apply(wanted, expect), attempted: true };
}

/** 端末で取り込む案内の答え。`command` が空なら `error` に理由 */
export interface TerminalGuide {
  readonly command: string;
  readonly error: string;
}

/**
 * `board` が偽の対象（`defense`）を端末で取り込むときの、打つコマンドを作る。
 * ボードは取り込まない。`expect` は、そのパスだけを指して見るだけをもう一度呼んだ答えのもの。
 * 取り込めない状態（conflict・rejected・error・unchanged）の対象には、コマンドを出さず理由を返す。
 */
export async function terminalGuide(
  deps: Pick<HandoffDeps, "view">,
  shown: HandoffList,
  path: string,
  workspace = "",
): Promise<TerminalGuide> {
  const entry = entryOf(shown.entries, path);
  if (entry === undefined) {
    return { command: "", error: `一覧にない対象です: ${path}` };
  }
  if (entry.board) {
    return { command: "", error: "この対象はボードから取り込めます（端末のコマンドは出しません）" };
  }
  const state = entry.state;
  if (state !== "new" && state !== "changed") {
    return { command: "", error: refusalOfState(entry) };
  }
  if (entry.errors.length > 0) {
    return { command: "", error: "検査で error があります。直してから取り込みます" };
  }
  const viewed = await deps.view([entry.path]);
  if (!viewed.ok) {
    return { command: "", error: viewed.error };
  }
  const now = entryOf(viewed.value.entries.filter((e) => e.selected), entry.path);
  if (now === undefined || viewed.value.entries.filter((e) => e.selected).length !== 1) {
    return { command: "", error: "取り込む対象が、見せた一覧と違います。更新して読み直してください" };
  }
  if (now.entry_sha !== entry.entry_sha) {
    return { command: "", error: "見せたあとに下書きか取り込み先が変わりました。更新して読み直してください" };
  }
  if (!viewed.value.ok || viewed.value.expect === "") {
    return { command: "", error: "検査を通らないので、取り込めません" };
  }
  return { command: terminalCommand(entry.path, viewed.value.expect, viewed.value.tree || shown.tree, workspace), error: "" };
}

/** 状態が取り込める形でない `board` 偽の対象の、取り込めない理由（`refusalReason` は「端末で取り込む」を先に言うので、状態の分だけ取り出す） */
function refusalOfState(entry: HandoffEntry): string {
  return refusalReason({ ...entry, board: true });
}
