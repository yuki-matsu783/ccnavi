/**
 * 承認のオーバーレイの遷移。**「いまの状態 ＋ 入力 → 次の状態 ＋ やること」だけ**をここに置く。
 *
 * 承認は取り返しがつかない（承認済みチケットが置かれ、コミットと push が端末に送られる）。
 * 連打・承認中の再入・古いボードからの承認は現実に起きるので、「この状態ではこれを受けない」を
 * 書き落とさないことが要る。**散らばっていると書き落とす**ので、見張りをこの 1 ファイルに集めた
 * （issue #92、ADR-0068）。
 *
 * VS Code の API には触れない。外へ出る仕事（実行ファイルを呼ぶ・端末に送る・クリップボードに
 * 入れる・新しいセッションで開く・人に言う）は `ApprovalEffect` として返すだけで、**実際に行うのは
 * 呼ぶ側**（`board-panel.ts`）。`core/screen-host.ts` の `Surface` と同じ形で、単体で試せる。
 *
 * 呼ぶ側の段取りは 3 行で、順を変えない。
 *
 * 1. `approvalStep(いまの状態, 入力)` を呼ぶ
 * 2. 返った `state` を持ち直し、`redraw` が真なら画面へ送り直す
 * 3. 返った `effects` を順に行う（返事が要るもの＝一覧と承認の結果は、返ってきたらまた 1 へ）
 *
 * **描き直しが先、やることが後。** 逆にすると、承認の文を渡すときに画面が古いまま残る。
 *
 * ## 状態（7 つ）
 *
 * | 状態 | 何をしている |
 * |---|---|
 * | 無し（`overlay` が `undefined`） | 閉じている |
 * | `loading` | 承認待ちの一覧を読んでいる |
 * | `preview` | 一覧と本文を見せた。押されるまで何も置かない |
 * | `approving` | 承認を打っている。**ここでは閉じない**（Esc も効かない。画面の側も同じ） |
 * | `done` | 承認した。渡す文がある |
 * | `error` | 読めなかった |
 * | `prompt` | 承認以外で渡す文（レビュー済みの連絡、残った指摘を決めた結果） |
 * | `decideLoading` | 残った指摘を読んでいる |
 * | `decidePreview` | 残った指摘を見せた。押されるまで何も置かない |
 * | `deciding` | 選んだ行き先を置いている。**ここでは閉じない** |
 *
 * ## 見張り（消すと承認が壊れる順）
 *
 * | 見張り | 消すとどうなる |
 * |---|---|
 * | `approving` の間は閉じない | 承認を打っている最中に閉じられ、結果を人が見ないまま次へ進む |
 * | 承認を打つのは `preview` のときだけ | 二重に打てる。実行ファイルの指紋の照合（ADR-0043）は 2 本目を止めるが、止まる前提で連打させない |
 * | 承認の途中（`loading`・`preview`・`approving`）は二重に開かない | 見せている一覧が、読み直しの途中の別の一覧に化ける |
 * | 一覧を受けるのは、それを頼んだ状態のときだけ | 閉じたあとに返ってきた一覧が、勝手にオーバーレイを開く |
 * | 文を渡せるのは `done` と `prompt` のときだけ | 文の無い状態で「コピー」が通る |
 * | `done` の上にレビュー済みの連絡を被せない | 承認の文が、渡す前に消える |
 * | 絞り込みで見えている承認待ちが、いまのボードでも承認待ちか | 古いボードの識別子で承認が通る |
 * | `deciding` の間は閉じない | 行き先を置いている最中に閉じられ、続きの子が起きたことを人が見ない |
 * | 行き先を置くのは `decidePreview` のときだけ | 二重に置ける。続きの子が 2 本起きる |
 * | 行き先が見せた指摘の全部に 1 つずつ付いているか | 古い画面から届いた選択で、見せていない指摘の扱いが決まる |
 * | 残った指摘を読むのは、人のレビュー待ちのフェーズだけ | 依頼していないフェーズで、実行ファイルの前提の誤りを人が読むことになる |
 * | 一覧を受けるのは、それを頼んだ状態のときだけ（残った指摘も同じ） | 閉じたあとに返ってきた一覧が、勝手にオーバーレイを開く |
 * | 承認と残った指摘は互いの途中に被さらない | 見せている一覧が、別の一覧に化ける |
 * | 決めた結果の文の上に、連絡も次の「決める」も被せない | 続きの子の識別子と次の 2 手を渡す前に、文が消える |
 *
 * これらは `test/shared/approval-machine.test.ts` が見る。**同じファイルの変異テストが、
 * 見張りを 1 つ消したらテストが落ちることまで見る**ので、見張りを足したらそちらにも足す。
 */
import type { ApprovalOverlay } from "./board-view.js";
import type { ApproveOutcome, PreviewParse } from "./approvemodel.js";
import { choicesProblem, type DecideOutcome, type DecidePreviewParse } from "./decidemodel.js";
import type { PhaseChip } from "./board.js";
import { PUSH_APPROVED_SCRIPT, reviewedPrompt } from "./commands.js";

/**
 * 承認のオーバーレイの持ち物。`overlay` が画面へ渡るぶんで、残りは拡張ホストの中だけの控え。
 *
 * 画面の中に持たないのは、監視の更新でボードが入れ替わってもオーバーレイが消えないようにするため
 * （`board-view.ts` の `ApprovalOverlay`）。
 */
export interface ApprovalState {
  /** いま被せているオーバーレイ。無ければ閉じている */
  readonly overlay?: ApprovalOverlay;
  /**
   * そのオーバーレイが見せている一覧の絞り（ボードの絞り込みで見えている識別子）。空なら全部。
   * 読み直しにも承認にも同じ絞りを通す。**忘れると、絞って見せたつもりのオーバーレイが
   * 承認待ち全部に化ける**
   */
  readonly only: readonly string[];
  /**
   * 食い違い（`mismatch`）で一覧を読み直している最中。見せている `overlay` は `approving` のままで、
   * 返ってきた一覧にこの `notice` を添えて `preview` に切り替える。
   * `dropped` は、絞りが通らなかったので絞りを外して読み直したか（外したあとは、もう外さない）。
   *
   * **不変条件: これがあるとき `overlay` は必ず `approving`。** 置くのは `answered` の食い違いの枝
   * 1 か所だけで、そこは `overlay` を持ち越す。`approving` の間は閉じられないので、外れるのは
   * 一覧が返って `preview` か `error` に差し替わるときだけ。**型では持っていない**ので、
   * `recheck` を置く枝を増やすならここを読み直すこと
   */
  readonly recheck?: { readonly notice: string; readonly dropped: boolean };
}

/** 閉じている状態 */
export const CLOSED: ApprovalState = { only: [] };

/** 人が押したことと、外から返ってきたこと。どちらも「入力」として同じ口から入れる */
export type ApprovalInput =
  /**
   * 「承認」を押した。`filtered` はボードが絞り込まれているか、`tickets` はそのとき見えている
   * 承認待ち（カードの「この 1 件を承認」は、そのカードの識別子だけが入る）。
   * `pending` は**いまのボードの**承認待ち。送られてきた識別子と突き合わせる
   */
  | {
      readonly kind: "approve";
      readonly tickets: readonly string[];
      readonly filtered: boolean;
      readonly pending: readonly string[];
    }
  /** 一覧（`--approve --preview --json`）が返った */
  | { readonly kind: "previewed"; readonly result: PreviewParse }
  /** 「この N 件を承認する」を押した */
  | { readonly kind: "confirm"; readonly tickets: readonly string[] }
  /**
   * 承認（`--approve --yes`）の結果が返った。食い違い（`mismatch`）もここに入る。
   * `carrier` は承認済みチケットを運ぶ sh が置いてあるか（呼ぶ側が見て渡す）
   */
  | { readonly kind: "approved"; readonly outcome: ApproveOutcome; readonly carrier: boolean }
  /** 「やめる」「閉じる」を押した（画面の Esc も同じ） */
  | { readonly kind: "cancel" }
  /**
   * 「レビュー済み連絡」を押した。`tree` は親のワークツリー、`chip` はそのフェーズ。
   * どちらもボードから引くので、無いことがある（古いボード）。`root` は文面に書くワークスペースルート
   */
  | {
      readonly kind: "reviewed";
      readonly parent: string;
      readonly phase: number;
      readonly tree: string | undefined;
      readonly chip: PhaseChip | undefined;
      readonly root: string;
    }
  /** 承認の文・レビュー済みの連絡の文を渡した */
  | { readonly kind: "handOver"; readonly how: "promptCopy" | "promptOpen" }
  /**
   * フェーズの「決める」を押した。`tree` と `chip` はボードから引くので、無いことがある（古いボード）
   */
  | {
      readonly kind: "decide";
      readonly parent: string;
      readonly phase: number;
      readonly tree: string | undefined;
      readonly chip: PhaseChip | undefined;
    }
  /** 残った指摘の一覧（`decide <N> --preview`）が返った */
  | { readonly kind: "decidePreviewed"; readonly result: DecidePreviewParse }
  /** 「この行き先で決める」を押した */
  | { readonly kind: "decideConfirm"; readonly choices: Readonly<Record<string, string>> }
  /** 行き先を置いた結果が返った。食い違い（`mismatch`）もここに入る */
  | { readonly kind: "decided"; readonly outcome: DecideOutcome };

/** 外へ出る仕事。**行うのは呼ぶ側**（`board-panel.ts`） */
export type ApprovalEffect =
  /** 承認待ちの一覧を読む（`--approve --preview --json`）。返ったら `previewed` で戻す */
  | { readonly kind: "loadPreview"; readonly only: readonly string[] }
  /** 承認を打つ（`--approve --yes … --digest …`）。返ったら `approved` で戻す */
  | {
      readonly kind: "approve";
      readonly tickets: readonly string[];
      readonly digest: string;
      readonly only: readonly string[];
    }
  /** 承認済みチケットを運ぶ sh を端末に送る */
  | { readonly kind: "carry" }
  /** 残った指摘を読む（`decide <N> --preview`）。返ったら `decidePreviewed` で戻す */
  | { readonly kind: "loadDecide"; readonly tree: string; readonly phase: number }
  /** 行き先を置く（`decide <N> --choices … --digest …`）。返ったら `decided` で戻す */
  | {
      readonly kind: "decide";
      readonly tree: string;
      readonly phase: number;
      readonly choices: Readonly<Record<string, string>>;
      readonly digest: string;
    }
  /** 文をクリップボードに入れる。`what` は何の文かの呼び名（伝える文面に出す） */
  | { readonly kind: "copy"; readonly prompt: string; readonly what: string }
  /** 文を埋めて新しいセッションで開く */
  | { readonly kind: "openSession"; readonly prompt: string }
  /** 人に言う（警告） */
  | { readonly kind: "warn"; readonly text: string }
  /** ボードを読み直す */
  | { readonly kind: "refresh" };

export interface ApprovalStep {
  readonly state: ApprovalState;
  /** オーバーレイが変わったので画面へ送り直す。やることより先に行う */
  readonly redraw: boolean;
  readonly effects: readonly ApprovalEffect[];
}

/** 状態も画面もそのまま。受けない入力のほか、**画面を変えずに済ませる**（`warn` / `refresh`）ときも使う */
function stay(state: ApprovalState, ...effects: ApprovalEffect[]): ApprovalStep {
  return { state, redraw: false, effects };
}

/** オーバーレイを差し替える。`redraw` は差し替わったかで決まる（同じものなら描き直さない） */
function move(state: ApprovalState, next: ApprovalState, ...effects: ApprovalEffect[]): ApprovalStep {
  return { state: next, redraw: next.overlay !== state.overlay, effects };
}

/** いまの状態と入力から、次の状態とやることを決める。**外には出ない** */
export function approvalStep(state: ApprovalState, input: ApprovalInput): ApprovalStep {
  switch (input.kind) {
    case "approve":
      return opened(state, input);
    case "previewed":
      return previewed(state, input.result);
    case "confirm":
      return confirmed(state, input.tickets);
    case "approved":
      return answered(state, input.outcome, input.carrier);
    case "cancel":
      // 承認を打っている最中と、行き先を置いている最中は閉じない。終わるまで、結果を受ける場所を残す
      return closable(state) ? move(state, CLOSED) : stay(state);
    case "reviewed":
      return reviewed(state, input);
    case "handOver":
      return handedOver(state, input.how);
    case "decide":
      return decideOpened(state, input);
    case "decidePreviewed":
      return decidePreviewed(state, input.result);
    case "decideConfirm":
      return decideConfirmed(state, input.choices);
    case "decided":
      return decided(state, input.outcome);
  }
}

/** 「承認」。絞りを決めてから一覧を読む */
function opened(
  state: ApprovalState,
  input: { readonly tickets: readonly string[]; readonly filtered: boolean; readonly pending: readonly string[] },
): ApprovalStep {
  let only: readonly string[] = [];
  if (input.filtered) {
    if (input.tickets.length === 0) {
      return stay(state, { kind: "warn", text: "絞り込みで見えている承認待ちが無い" });
    }
    // 1 つでもいまのボードで承認待ちでなければ、ボードが古い。落として送ると「見せた 2 件の
    // つもりが 1 件」になるので、削らずに止める
    const pending = new Set(input.pending);
    if (!input.tickets.every((id) => pending.has(id))) {
      return stay(
        state,
        { kind: "warn", text: "ボードが古く、承認待ちが変わっている。更新してから承認する" },
        { kind: "refresh" },
      );
    }
    only = input.tickets;
  }
  // 承認の途中（読み込み中・一覧・承認中）は二重に開かない。
  // 読めなかった・承認した文・レビュー済みの連絡の上には開ける
  const kind = state.overlay?.kind;
  if (kind === "loading" || kind === "preview" || kind === "approving") {
    return stay(state);
  }
  // 残った指摘を決める途中にも開かない。見せている指摘が、承認待ちの一覧に化ける
  if (busyDeciding(state)) {
    return stay(state);
  }
  return move(state, { overlay: { kind: "loading" }, only }, { kind: "loadPreview", only });
}

/** 一覧が返った。頼んだのが「開く」なのか「食い違いの読み直し」なのかで行き先が変わる */
function previewed(state: ApprovalState, result: PreviewParse): ApprovalStep {
  const recheck = state.recheck;
  if (recheck !== undefined) {
    // 絞りが通らない（その識別子がもう承認待ちに無い、親の改版が承認待ちに入った）ときだけ絞りを
    // 外し、いま何が承認待ちなのかを全部見せる。**外すのは 1 度だけ**
    if (!result.ok && state.only.length > 0 && !recheck.dropped) {
      return move(
        state,
        { overlay: state.overlay, only: [], recheck: { notice: recheck.notice, dropped: true } },
        { kind: "loadPreview", only: [] },
      );
    }
    return move(state, {
      overlay: result.ok
        ? { kind: "preview", preview: result.value, notice: recheck.notice }
        : { kind: "error", error: result.error },
      only: state.only,
    });
  }
  // 頼んだときのまま（`loading`）でなければ受けない。閉じたあとに返ってきた一覧で開き直さない
  if (state.overlay?.kind !== "loading") {
    return stay(state);
  }
  return move(state, {
    overlay: result.ok ? { kind: "preview", preview: result.value } : { kind: "error", error: result.error },
    only: state.only,
  });
}

/**
 * 「この N 件を承認する」。見せた識別子と指紋をそのまま渡す。実行ファイルが一覧と本文の一致を
 * 確かめ、違えば何も置かずに `mismatch` を返す（ADR-0043）
 */
function confirmed(state: ApprovalState, tickets: readonly string[]): ApprovalStep {
  // 承認を打てるのは、一覧を見せているときだけ。承認中に押し直しても 2 本目は出ない
  if (state.overlay?.kind !== "preview" || tickets.length === 0) {
    return stay(state);
  }
  const preview = state.overlay.preview;
  return move(
    state,
    { overlay: { kind: "approving", preview }, only: state.only },
    { kind: "approve", tickets, digest: preview.digest, only: state.only },
  );
}

/**
 * 承認の結果。**ここには「この状態でなければ受けない」を置かない。**
 * 承認済みチケットは既に置かれていることがあり、受けずに捨てると人に届かない
 */
function answered(state: ApprovalState, outcome: ApproveOutcome, carrier: boolean): ApprovalStep {
  if (outcome.ok) {
    const count = outcome.value.approved.length;
    // 運ぶ 1 行は、文を渡すのを待たずに端末へ出す。承認と同じ時点で出しておく
    const carried = count > 0 && carrier;
    // 承認できたら読み直す。**監視（`core/watch.ts`）だけに頼らない。** 承認は承認済みチケットを
    // `.ccnavi/approved/doing/` に書いてから提案を消すので、ふつうはその置き場の監視が拾って
    // 読み直る。拾えないのは、その置き場が監視の綴りと違うとき（`CCNAVI_TICKETS_APPROVED` が
    // 既定と違う。監視の綴りは `core/watch.ts` に固定してあり、この env を読まない）、
    // `files.watcherExclude` でそこを外したとき、監視の効かないファイルシステムのとき。
    // 読み直しの途中でもう 1 回頼まれた分は呼ぶ側が 1 回に畳む（`board-panel.ts` の `again`）ので、
    // 監視と重なっても画面はちらつかない。**1 件も置かれていないなら読み直さない**（何も動いていない）。
    //
    // 運ぶ sh が無ければ送らずに言う。送って `No such file` を見せるより、何をすればよいかが先に分かる
    const effects: ApprovalEffect[] =
      count === 0
        ? []
        : [
            carried
              ? { kind: "carry" }
              : {
                  kind: "warn",
                  text:
                    `承認済みチケットはまだコミットされていない。${PUSH_APPROVED_SCRIPT} が無いので、` +
                    "導入スクリプト（scripts/ccnavi-setup.sh）で配る",
                },
            { kind: "refresh" },
          ];
    return move(
      state,
      { overlay: { kind: "done", count, prompt: outcome.value.prompt, carried }, only: [] },
      ...effects,
    );
  }
  if ("mismatch" in outcome) {
    // まず同じ絞りで読み直す。カードの「この 1 件を承認」で全部の一覧に切り替わると、1 件のつもりで
    // 押し続けて全部を承認しかねない。見せているのは `approving` のままで、返ったら差し替える
    const sameIds = outcome.mismatch.expected.join(",") === outcome.mismatch.current.join(",");
    const notice = sameIds
      ? "見せた承認画面と今の本文が違った（提案の中身が変わった）。見直してから承認する"
      : "見せた一覧と今の一覧が違った（提案が増えたか減った）。見直してから承認する";
    return move(
      state,
      { overlay: state.overlay, only: state.only, recheck: { notice, dropped: false } },
      { kind: "loadPreview", only: state.only },
    );
  }
  return move(state, { overlay: { kind: "error", error: outcome.error }, only: state.only });
}

/**
 * 「レビュー済み連絡」。マーカーは置かない。レビューを終えたことを Claude Code に伝える文を組み、
 * 承認の文と同じ 2 ボタンで渡す。`confirm` を打つのは文を受けたエージェント
 */
function reviewed(
  state: ApprovalState,
  input: {
    readonly parent: string;
    readonly phase: number;
    readonly tree: string | undefined;
    readonly chip: PhaseChip | undefined;
    readonly root: string;
  },
): ApprovalStep {
  // 承認のオーバーレイ（読み込み中・一覧・承認中・承認した文）の上には被せない。承認した文は
  // 取り返せないので、渡し終えるか閉じるまで消さない。前の連絡（prompt）と読めなかった（error）は
  // 差し替えてよい
  const kind = state.overlay?.kind;
  if (kind !== undefined && kind !== "error" && kind !== "prompt") {
    return stay(state);
  }
  // 決めた結果の文（取り返せない）の上にも被せない
  if (keptPrompt(state)) {
    return stay(state);
  }
  const { parent, phase, tree, chip } = input;
  if (tree === undefined || chip === undefined) {
    return stay(state, {
      kind: "warn",
      text: `親 ${parent} のワークツリーかフェーズ ${phase} が無いので、レビュー済みの連絡を組めない`,
    });
  }
  // ボタンが出る条件（人のレビュー待ち）を受け側でも持つ。待ちでなければ confirm の前提（依頼のマーカー）が無い
  if (!chip.reviewWaiting) {
    return stay(
      state,
      {
        kind: "warn",
        text: `親 ${parent} のフェーズ ${chip.label} は人のレビュー待ちではない。ボードを更新する`,
      },
      { kind: "refresh" },
    );
  }
  return move(state, {
    overlay: {
      kind: "prompt",
      title: `フェーズ ${chip.label} のレビュー済み連絡`,
      note:
        "レビューを終えたことを Claude Code に伝える文を用意した。コピーして進行中のセッションに貼るか、" +
        "新しいセッションで開く。送るときは自分で Enter を押す。マーカーはエージェントが confirm を打って置く。",
      prompt: reviewedPrompt(input.root, parent, phase, chip.label, tree, chip.mrUrl),
    },
    only: state.only,
  });
}

/**
 * 文を Claude Code に渡す。文は拡張ホストが持っている分を使う（画面から届いた文は使わない）。
 * 渡したらオーバーレイを閉じる
 */
function handedOver(state: ApprovalState, how: "promptCopy" | "promptOpen"): ApprovalStep {
  const overlay = state.overlay;
  // 渡す文があるのは、承認した（done）ときとレビュー済みの連絡（prompt）のときだけ
  if (overlay?.kind !== "done" && overlay?.kind !== "prompt") {
    return stay(state);
  }
  const what = overlay.kind === "done" ? "承認の文" : (overlay.what ?? "レビュー済みの連絡の文");
  const prompt = overlay.prompt;
  return move(
    state,
    CLOSED,
    how === "promptCopy" ? { kind: "copy", prompt, what } : { kind: "openSession", prompt },
  );
}

/** 閉じてよいか。承認を打っている最中と、行き先を置いている最中は閉じない */
function closable(state: ApprovalState): boolean {
  const kind = state.overlay?.kind;
  return kind !== "approving" && kind !== "deciding";
}

/** 取り返せない文（残った指摘を決めた結果）を見せているか */
function keptPrompt(state: ApprovalState): boolean {
  return state.overlay?.kind === "prompt" && state.overlay.keep === true;
}

/** 残った指摘を決める途中（読み込み中・一覧・置いている最中）か */
function busyDeciding(state: ApprovalState): boolean {
  const kind = state.overlay?.kind;
  return kind === "decideLoading" || kind === "decidePreview" || kind === "deciding";
}

/**
 * フェーズの「決める」。残った指摘を読む。被せてよいのはレビュー済みの連絡と同じ場所（閉じている・
 * 読めなかった・前の連絡）だけで、承認の途中と承認した文の上、決める途中には被せない
 */
function decideOpened(
  state: ApprovalState,
  input: {
    readonly parent: string;
    readonly phase: number;
    readonly tree: string | undefined;
    readonly chip: PhaseChip | undefined;
  },
): ApprovalStep {
  const kind = state.overlay?.kind;
  const coverable = kind === undefined || kind === "error" || (kind === "prompt" && !keptPrompt(state));
  if (!coverable) {
    return stay(state);
  }
  const { parent, phase, tree, chip } = input;
  if (tree === undefined || chip === undefined) {
    return stay(state, {
      kind: "warn",
      text: `親 ${parent} のワークツリーかフェーズ ${phase} が無いので、未解決（Unresolved）の指摘を読めません`,
    });
  }
  // 残った指摘を決めるのは、依頼を出してから人が見ている間だけ。待ちでなければ依頼の記録が無い
  if (chip.reviewWaiting !== true) {
    return stay(
      state,
      { kind: "warn", text: `親 ${parent} のフェーズ ${chip.label} は人のレビュー待ちではない。ボードを更新する` },
      { kind: "refresh" },
    );
  }
  return move(
    state,
    { overlay: { kind: "decideLoading", parent, phase, tree }, only: state.only },
    { kind: "loadDecide", tree, phase },
  );
}

/** 残った指摘の一覧が返った。頼んだとき（`decideLoading`）のままでなければ受けない */
function decidePreviewed(state: ApprovalState, result: DecidePreviewParse): ApprovalStep {
  const overlay = state.overlay;
  if (overlay?.kind !== "decideLoading") {
    return stay(state);
  }
  // 頼んだフェーズの一覧か。別のフェーズの答えで開かない
  if (result.ok && (result.value.parent !== overlay.parent || result.value.phase !== overlay.phase)) {
    return move(state, {
      overlay: { kind: "error", error: "頼んだフェーズと違う一覧が返った。ボードを更新してから決め直す" },
      only: state.only,
    });
  }
  return move(state, {
    overlay: result.ok
      ? { kind: "decidePreview", preview: result.value, tree: overlay.tree, notice: overlay.notice }
      : { kind: "error", error: result.error },
    only: state.only,
  });
}

/**
 * 「この行き先で決める」。見せた指摘の全部に行き先が 1 つずつ付いているときだけ送る。
 * 指紋は見せた一覧のもの。実行ファイルが今の指摘と比べ、違えば何も置かない
 */
function decideConfirmed(state: ApprovalState, choices: Readonly<Record<string, string>>): ApprovalStep {
  const overlay = state.overlay;
  if (overlay?.kind !== "decidePreview") {
    return stay(state);
  }
  const problem = choicesProblem(overlay.preview, choices);
  if (problem !== undefined) {
    return stay(state, { kind: "warn", text: problem });
  }
  const { preview, tree } = overlay;
  return move(
    state,
    { overlay: { kind: "deciding", preview, tree }, only: state.only },
    { kind: "decide", tree, phase: preview.phase, choices, digest: preview.digest },
  );
}

/**
 * 置いた結果。**承認の結果と同じく「この状態でなければ受けない」を置かない。** 置かれたものは
 * 戻らないので、受けずに捨てると人に届かない
 */
function decided(state: ApprovalState, outcome: DecideOutcome): ApprovalStep {
  if (outcome.ok) {
    const value = outcome.value;
    const effects: ApprovalEffect[] = [{ kind: "refresh" }];
    if (value.warning !== "") {
      effects.unshift({ kind: "warn", text: value.warning });
    }
    const note = value.followup
      ? `直す指摘を載せた続きの子チケット ${value.followup} を起こしました。フェーズは開き直りました。`
      : "フェーズはレビュー済みになりました。";
    const issued = value.issue_url ? ` issue に回した分: ${value.issue_url}` : "";
    return move(
      state,
      {
        overlay: {
          kind: "prompt",
          title: value.followup
            ? `フェーズ ${value.phase} の未解決（Unresolved）の指摘の対応方針を決めました`
            : `フェーズ ${value.phase} をレビュー済みにしました`,
          note:
            `${note}${issued} Claude Code に伝える文を用意した。コピーして進行中のセッションに貼るか、` +
            "新しいセッションで開く。送るときは自分で Enter を押す。",
          prompt: value.prompt,
          what: "残った指摘を決めた文",
          keep: true,
        },
        only: state.only,
      },
      ...effects,
    );
  }
  const overlay = state.overlay;
  if ("mismatch" in outcome && overlay?.kind === "deciding") {
    // 見せた指摘と今の指摘が違った。何も置かれていないので、読み直して見せ直す
    const { preview, tree } = overlay;
    return move(
      state,
      {
        overlay: {
          kind: "decideLoading",
          parent: preview.parent,
          phase: preview.phase,
          tree,
          notice: "見せた指摘と今の指摘が違った（増えたか、書き換わった）。見直してから決める",
        },
        only: state.only,
      },
      { kind: "loadDecide", tree, phase: preview.phase },
    );
  }
  const error = "mismatch" in outcome ? "見せた指摘と今の指摘が違った。ボードを更新してから決め直す" : outcome.error;
  return move(state, { overlay: { kind: "error", error }, only: state.only }, { kind: "refresh" });
}
