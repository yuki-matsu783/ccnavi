/**
 * 承認のオーバーレイの遷移。「いまの状態 ＋ 入力 → 次の状態 ＋ やること」だけをここに置く。
 *
 * 承認は取り返しがつかない（承認済みチケットが置かれ、コミットと push がターミナルに送られる）。
 * 連打・承認中の再入・古いボードからの承認は現実に起きるので、「この状態ではこれを受けない」を
 * 書き落とさないことが要る。散らばっていると書き落とすので、ガードをこの 1 ファイルに集めた。
 *
 * VS Code の API には触れない。外へ出る仕事（実行ファイルを呼ぶ・ターミナルに送る・クリップボードに
 * 入れる・新しいセッションで開く・ユーザに言う）は `ApprovalEffect` として返すだけで、実際に行うのは
 * 呼ぶ側（`board-panel.ts`）。`core/screen-host.ts` の `Surface` と同じ形で、単体で試せる。
 *
 * 呼ぶ側の段取りは 3 行で、順を変えない。
 *
 * 1. `approvalStep(いまの状態, 入力)` を呼ぶ
 * 2. 返った `state` を持ち直し、`redraw` が真なら画面へ送り直す
 * 3. 返った `effects` を順に行う（返事が要るもの＝一覧と承認の結果は、返ってきたらまた 1 へ）
 *
 * 描き直しが先、やることが後。 逆にすると、承認の文を渡すときに画面が古いまま残る。
 *
 * ## 状態（閉じているのを含めて 10）
 *
 * | 状態 | 何をしている |
 * |---|---|
 * | 無し（`overlay` が `undefined`） | 閉じている |
 * | `loading` | 承認待ちの一覧を読んでいる |
 * | `preview` | 一覧と本文を見せた。押されるまで何も置かない |
 * | `approving` | 承認を打っている。ここでは閉じない（Esc も受け付けない。画面の側も同じ） |
 * | `done` | 承認した。渡す文がある |
 * | `error` | 読めなかった |
 * | `prompt` | 承認以外で渡すプロンプト（残った指摘を決めた結果） |
 * | `decideLoading` | 残った指摘を読んでいる（［レビュー完了］を押した） |
 * | `decidePreview` | 残った指摘（0 件なら「未解決なし」）を見せた。押されるまで何も置かない |
 * | `deciding` | 選んだ行き先を置いている。ここでは閉じない |
 *
 * ## ガード（消すと承認が正しく動かなくなる順）
 *
 * 承認のほかに、残った指摘を決める流れと計画編集タブのぶんがある。数は数えて書かない。足したときに古くなるため。
 *
 * | ガード | 消すとどうなる |
 * |---|---|
 * | `approving` の間は閉じない | 承認を打っている最中に閉じられ、結果をユーザが見ないまま次へ進む |
 * | 承認を打つのは `preview` のときだけ | 二重に打てる。実行ファイルが承認のときに読み直した中身のダイジェストと照合するので 2 本目は止まるが、止まる前提で連打させない |
 * | 承認の途中（`loading`・`preview`・`approving`）は二重に開かない | 見せている一覧が、読み直しの途中の別の一覧になってしまう |
 * | 一覧を受けるのは、それを頼んだ状態のときだけ | 閉じたあとに返ってきた一覧が、勝手にオーバーレイを開く |
 * | 文を渡せるのは `done` と `prompt` のときだけ | 文の無い状態で「コピー」が通る |
 * | `done` の上に［レビュー完了］の読み込みを被せない | 承認のプロンプトが、渡す前に消える |
 * | 絞り込みで見えている承認待ちが、いまのボードでも承認待ちか | 古いボードの識別子で承認が通る |
 * | `deciding` の間は閉じない | 行き先を置いている最中に閉じられ、続きの子が起きたことをユーザが見ない |
 * | 行き先を置くのは `decidePreview` のときだけ | 二重に置ける。続きの子が 2 本起きる |
 * | 行き先が見せた指摘の全部に 1 つずつ付いているか | 古い画面から届いた選択で、見せていない指摘の扱いが決まる |
 * | 残った指摘を読むのは、ユーザのレビュー待ちのフェーズだけ | 依頼していないフェーズで、実行ファイルの前提の誤りをユーザが読むことになる |
 * | 読み取りの結果が頼んだフェーズのものでなければ開かない | 別のフェーズの指摘に、対応方針を選ばせる |
 * | 相談のプロンプトを組むのは、1 件以上の指摘を見せているとき（`decidePreview`）だけ | 閉じたあと・置いている最中・0 件の画面で、指摘の無いプロンプトが出る |
 * | 相談のコピーの結果を受けるのは、見せている画面（`decidePreview`）のときだけ | 閉じたあとに返った結果が、別の画面の上に出る |
 * | 一覧を受けるのは、それを頼んだ状態のときだけ（残った指摘も同じ） | 閉じたあとに返ってきた一覧が、勝手にオーバーレイを開く |
 * | 承認と残った指摘は互いの途中に被さらない | 見せている一覧が、別の一覧になってしまう |
 * | 決めた結果のプロンプトの上に、次の「レビュー完了」を被せない（決め直しを出す場合を除く） | 続きの子の識別子と次の 2 手を渡す前に、プロンプトが消える |
 * | 計画編集タブに未保存の変更がある親を含む一覧は、承認を打たない | 未保存の順序のまま承認され、直したつもりの順序と違うものが通る |
 * | 承認を打つ直前にも、未保存のタブがある親が打つ識別子に入っていないかを見る（`blockedBeforeApprove`） | 押したあとにタブで線を変えた一覧が、そのまま承認される |
 * | 「計画を編集」でタブを開くのは、一覧を見せているとき（preview）だけ | 承認を打っている最中に閉じられ、結果を受ける場所が消える |
 * | 「計画を編集」で開くのは、一覧の図にある親だけ | 古い画面から届いた識別子で、承認と関係の無いタブが開く |
 *
 * これらは `test/shared/approval-machine.test.ts` が見る。同じファイルの変異テストが、
 * ガードを 1 つ消したらテストが失敗することまで見るので、ガードを足したらそちらにも足す。
 */
import type { ApprovalOverlay } from "./board-view.js";
import { changedSinceSave, partialMessage, unsavedParents, unsavedText, type ApproveOutcome, type ApprovePreview, type PreviewParse } from "./approvemodel.js";
import { choicesProblem, type DecideOutcome, type DecidePreviewParse } from "./decidemodel.js";
import type { PhaseChip } from "./board.js";
import { consultPrompt, PUSH_APPROVED_SCRIPT } from "./commands.js";

/**
 * 承認のオーバーレイの持ち物。`overlay` が画面へ渡るぶんで、残りは拡張ホストの中だけに保持する。
 *
 * 画面の中に持たないのは、監視の更新でボードが入れ替わってもオーバーレイが消えないようにするため
 * （`board-view.ts` の `ApprovalOverlay`）。
 */
export interface ApprovalState {
  /** いま被せているオーバーレイ。無ければ閉じている */
  readonly overlay?: ApprovalOverlay;
  /**
   * そのオーバーレイが見せている一覧の絞り（ボードの絞り込みで見えている識別子）。空なら全部。
   * 読み直しにも承認にも同じ絞りを通す。忘れると、絞って見せたつもりのオーバーレイが
   * 承認待ち全部になってしまう
   */
  readonly only: readonly string[];
  /**
   * 食い違い（`mismatch`）で一覧を読み直している最中。見せている `overlay` は `approving` のままで、
   * 返ってきた一覧にこの `notice` をつけて `preview` に切り替える。
   * `dropped` は、絞りが通らなかったので絞りを外して読み直したか（外したあとは、もう外さない）。
   *
   * 不変条件: これがあるとき `overlay` は必ず `approving`。 置くのは `answered` の食い違いの枝
   * 1 か所だけで、そこは `overlay` を持ち越す。`approving` の間は閉じられないので、外れるのは
   * 一覧が返って `preview` か `error` に差し替わるときだけ。型では持っていないので、
   * `recheck` を置く枝を増やすならここを読み直すこと
   */
  readonly recheck?: { readonly notice: string; readonly dropped: boolean };
}

/** 閉じている状態 */
export const CLOSED: ApprovalState = { only: [] };

/** ユーザが押したことと、外から返ってきたこと。どちらも「入力」として同じ関数から入れる */
export type ApprovalInput =
  /**
   * 「承認」を押した。`filtered` はボードが絞り込まれているか、`tickets` はそのとき見えている
   * 承認待ち（カードの「この 1 件を承認」は、そのカードの識別子だけが入る）。
   * `pending` はいまのボードの承認待ち。送られてきた識別子と突き合わせる
   */
  | {
      readonly kind: "approve";
      readonly tickets: readonly string[];
      readonly filtered: boolean;
      readonly pending: readonly string[];
    }
  /**
   * 一覧（`--agree --preview --json`）が返った。`saved` は計画編集タブが最後に保存したときの
   * `source_sha`（親ごと。`core/workflow-tabs.ts`）。一覧の計画と比べて、保存したあとに変わった親を覚える
   */
  | { readonly kind: "previewed"; readonly result: PreviewParse; readonly saved: Readonly<Record<string, string>> }
  /**
   * 「この N 件を承認する」を押した。`unsaved` は**押した時点の**、計画編集タブに未保存の変更がある親
   * （呼ぶ側が帳面から引いて渡す）。`digest` は押した画面に出ていた一覧のダイジェスト
   */
  | {
      readonly kind: "confirm";
      readonly tickets: readonly string[];
      readonly digest: string;
      readonly unsaved: readonly string[];
    }
  /** 一覧の図の「計画を編集」を押した。オーバーレイを閉じてから、その親のタブを開く */
  | { readonly kind: "editWorkflow"; readonly ticket: string }
  /**
   * 承認（`user agree --yes`）の結果が返った。食い違い（`mismatch`）もここに入る。
   * `carrier` は承認の push の sh が置いてあるか（呼ぶ側が見て渡す）
   */
  | { readonly kind: "approved"; readonly outcome: ApproveOutcome; readonly carrier: boolean }
  /** 「やめる」「閉じる」を押した（画面の Esc も同じ） */
  | { readonly kind: "cancel" }
  /** 承認のプロンプト・決めた結果のプロンプトを渡した */
  | { readonly kind: "handOver"; readonly how: "promptCopy" | "promptOpen" | "promptRun" }
  /**
   * ［レビュー完了］を押した。`tree` と `chip` はボードから引くので、無いことがある（古いボード）
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
  /** 「この対応方針を確定」（0 件なら「レビュー済みにする」）を押した */
  | { readonly kind: "decideConfirm"; readonly choices: Readonly<Record<string, string>> }
  /**
   * 「エージェントに相談するプロンプトをコピー」を押した。`root` は文面に書くワークスペースルート
   * （呼ぶ側が realpath で解く）。オーバーレイは閉じず、選びかけの対応方針も変えない
   */
  | { readonly kind: "consult"; readonly root: string }
  /** 相談のプロンプトをクリップボードに入れた結果。入らなかったときは理由 */
  | { readonly kind: "consultCopied"; readonly error: string | undefined }
  /** 行き先を置いた結果が返った。食い違い（`mismatch`）もここに入る */
  | { readonly kind: "decided"; readonly outcome: DecideOutcome };

/** 外へ出る仕事。行うのは呼ぶ側（`board-panel.ts`） */
export type ApprovalEffect =
  /** 承認待ちの一覧を読む（`--agree --preview --json`）。返ったら `previewed` で戻す */
  | { readonly kind: "loadPreview"; readonly only: readonly string[] }
  /** 承認を打つ（`user agree --yes … --digest …`）。返ったら `approved` で戻す */
  | {
      readonly kind: "approve";
      readonly tickets: readonly string[];
      readonly digest: string;
      readonly only: readonly string[];
    }
  /**
   * 承認の push の sh をターミナルに送る。`paths` はこの承認で書いた・消したパスで、sh にはこれだけを
   * コミット・push させる（同じ置き場の承認と無関係な変更を巻き込まない）。空なら古い実行ファイルで、絞らずに送る
   */
  | { readonly kind: "carry"; readonly paths: readonly string[] }
  /**
   * 残った指摘を読む（`decide <N> --preview`）。返ったら `decidePreviewed` で戻す。
   * `parent`・`phase`・`mr` は頼んだもの（読み取りの検証が、返ってきた JSON と照らす）
   */
  | {
      readonly kind: "loadDecide";
      readonly tree: string;
      readonly parent: string;
      readonly phase: number;
      readonly mr: number | null;
    }
  /** 行き先を置く（`ccnavi-user-decide.sh decide <N> --choices … --digest …`）。返ったら `decided` で戻す */
  | {
      readonly kind: "decide";
      readonly tree: string;
      readonly phase: number;
      readonly choices: Readonly<Record<string, string>>;
      readonly digest: string;
    }
  /** プロンプトをクリップボードに入れる。`what` は何のプロンプトかの呼び名（伝える文面に出す） */
  | { readonly kind: "copy"; readonly prompt: string; readonly what: string }
  /**
   * 相談のプロンプトをクリップボードに入れる。オーバーレイは閉じない。返ったら `consultCopied` で戻す
   * （できたら短く知らせ、できなければ失敗と言う）
   */
  | { readonly kind: "copyConsult"; readonly prompt: string }
  /** プロンプトを埋めて新しいセッションで開く */
  | { readonly kind: "openSession"; readonly prompt: string }
  /** プロンプトを `claude -p` でボードのターミナルから実行する（止めるのはターミナルの Ctrl+C。結果は取り込まない） */
  | { readonly kind: "runClaude"; readonly prompt: string }
  /** 親の計画編集タブを開く（`screens().workflow`） */
  | { readonly kind: "openWorkflow"; readonly ticket: string }
  /** ユーザに言う（警告） */
  | { readonly kind: "warn"; readonly text: string }
  /** ボードを読み直す */
  | { readonly kind: "refresh" };

export interface ApprovalStep {
  readonly state: ApprovalState;
  /** オーバーレイが変わったので画面へ送り直す。やることより先に行う */
  readonly redraw: boolean;
  readonly effects: readonly ApprovalEffect[];
}

/** 状態も画面もそのまま。受けない入力のほか、画面を変えずに済ませる（`warn` / `refresh`）ときも使う */
function stay(state: ApprovalState, ...effects: ApprovalEffect[]): ApprovalStep {
  return { state, redraw: false, effects };
}

/** オーバーレイを差し替える。`redraw` は差し替わったかで決まる（同じものなら描き直さない） */
function move(state: ApprovalState, next: ApprovalState, ...effects: ApprovalEffect[]): ApprovalStep {
  return { state: next, redraw: next.overlay !== state.overlay, effects };
}

/** いまの状態と入力から、次の状態とやることを決める。外には出ない */
export function approvalStep(state: ApprovalState, input: ApprovalInput): ApprovalStep {
  switch (input.kind) {
    case "approve":
      return opened(state, input);
    case "previewed":
      return previewed(state, input.result, input.saved);
    case "confirm":
      return confirmed(state, input.tickets, input.digest, input.unsaved);
    case "editWorkflow":
      return editWorkflow(state, input.ticket);
    case "approved":
      return answered(state, input.outcome, input.carrier);
    case "cancel":
      // 承認を打っている最中と、行き先を置いている最中は閉じない。終わるまで、結果を受ける場所を残す
      return closable(state) ? move(state, CLOSED) : stay(state);
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
    case "consult":
      return consulted(state, input.root);
    case "consultCopied":
      return consultCopied(state, input.error);
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
      return stay(state, { kind: "warn", text: "絞り込みで見えている承認待ちがありません" });
    }
    // 1 つでもいまのボードで承認待ちでなければ、ボードが古い。外して送ると「見せた 2 件の
    // つもりが 1 件」になるので、削らずに止める
    const pending = new Set(input.pending);
    if (!input.tickets.every((id) => pending.has(id))) {
      return stay(
        state,
        { kind: "warn", text: "表示が古く、承認待ちが変わっています。チケット管理画面を更新したので、もう一度承認してください" },
        { kind: "refresh" },
      );
    }
    only = input.tickets;
  }
  // 承認の途中（読み込み中・一覧・承認中）は二重に開かない。
  // 読めなかった・承認したプロンプトの上には開ける
  const kind = state.overlay?.kind;
  if (kind === "loading" || kind === "preview" || kind === "approving") {
    return stay(state);
  }
  // 残った指摘を決める途中にも開かない。見せている指摘が、承認待ちの一覧になってしまう
  if (busyDeciding(state)) {
    return stay(state);
  }
  return move(state, { overlay: { kind: "loading" }, only }, { kind: "loadPreview", only });
}

/** 一覧が返った。頼んだのが「開く」なのか「食い違いの読み直し」なのかで行き先が変わる */
function previewed(state: ApprovalState, result: PreviewParse, saved: Readonly<Record<string, string>>): ApprovalStep {
  const recheck = state.recheck;
  if (recheck !== undefined) {
    // 絞りが通らない（その識別子がもう承認待ちに無い、親の改版が承認待ちに入った）ときだけ絞りを
    // 外し、いま何が承認待ちなのかを全部見せる。外すのは 1 度だけ
    if (!result.ok && state.only.length > 0 && !recheck.dropped) {
      return move(
        state,
        { overlay: state.overlay, only: [], recheck: { notice: recheck.notice, dropped: true } },
        { kind: "loadPreview", only: [] },
      );
    }
    return move(state, {
      overlay: result.ok
        ? { kind: "preview", preview: result.value, notice: recheck.notice, ...changedOf(result.value, saved) }
        : { kind: "error", error: result.error },
      only: state.only,
    });
  }
  // 頼んだときのまま（`loading`）でなければ受けない。閉じたあとに返ってきた一覧で開き直さない
  if (state.overlay?.kind !== "loading") {
    return stay(state);
  }
  return move(state, {
    overlay: result.ok ? { kind: "preview", preview: result.value, ...changedOf(result.value, saved) } : { kind: "error", error: result.error },
    only: state.only,
  });
}

/** 保存したあとに提案が変わった親。無ければ欄を持たない */
function changedOf(preview: ApprovePreview, saved: Readonly<Record<string, string>>): { readonly changedSinceSave?: readonly string[] } {
  const changed = changedSinceSave(preview, saved);
  return changed.length === 0 ? {} : { changedSinceSave: changed };
}

/** 押した画面の一覧と、手元の最新の一覧が違ったときに言うこと */
export const SHOWN_STALE =
  "内容が更新されました（押した画面の一覧は最新ではありませんでした）。何も承認していません。最新の内容を表示し直したので、見直してからもう一度承認してください";

/**
 * 「この N 件を承認する」。見せた識別子とダイジェストをそのまま渡す。実行ファイルが一覧と本文の一致を
 * 確かめ、違えば何も置かずに `mismatch` を返す。
 * 渡すダイジェストは、押した画面に出ていたもの（`digest`）。読み直しで一覧が差し替わった直後、画面が
 * 描き直す前に押されると、手元の一覧（`state`）は画面に出ていない新しい中身になっている。そのときは
 * 打たずに、今の一覧を表示し直す
 */
function confirmed(
  state: ApprovalState,
  tickets: readonly string[],
  digest: string,
  unsaved: readonly string[],
): ApprovalStep {
  // 承認を打てるのは、一覧を見せているときだけ。承認中に押し直しても 2 本目は出ない
  if (state.overlay?.kind !== "preview" || tickets.length === 0) {
    return stay(state);
  }
  const preview = state.overlay.preview;
  if (digest !== preview.digest) {
    return move(state, {
      overlay: { ...state.overlay, notice: SHOWN_STALE },
      only: state.only,
    });
  }
  // 計画編集タブに未保存の変更がある親を含む一覧は打たない（画面のボタンも押せないが、ここでも止める）
  const blocked = unsavedParents(preview, unsaved);
  if (blocked.length > 0) {
    return stay(state, { kind: "warn", text: unsavedText(blocked) });
  }
  return move(
    state,
    { overlay: { kind: "approving", preview }, only: state.only },
    { kind: "approve", tickets, digest: preview.digest, only: state.only },
  );
}

/**
 * 承認の結果。ここには「この状態でなければ受けない」を置かない。
 * 承認済みチケットは既に置かれていることがあり、受けずに捨てるとユーザに届かない
 */
function answered(state: ApprovalState, outcome: ApproveOutcome, carrier: boolean): ApprovalStep {
  if (outcome.ok) {
    const count = outcome.value.approved.length;
    // 承認の push の 1 行は、文を渡すのを待たずにターミナルへ出す。承認と同じ時点で出しておく
    const carried = count > 0 && carrier;
    // 承認できたら読み直す。監視（`core/watch.ts`）だけに頼らない。 承認は承認済みチケットを
    // `.ccnavi/approved/doing/` に書いてから提案を消すので、ふつうはその置き場の監視が拾って
    // 読み直る。拾えないのは、`files.watcherExclude` でそこを外したとき、監視が使えない
    // ファイルシステムのとき（置き場は既定に固定で、監視するパス `core/watch.ts` とずれない）。
    // 読み直しの途中でもう 1 回頼まれた分は呼ぶ側が 1 回にまとめる（`board-panel.ts` の `again`）ので、
    // 監視と重なっても画面はちらつかない。1 件も置かれていないなら読み直さない（何も動いていない）。
    //
    // 承認の push の sh が無ければ送らずに言う。送って `No such file` を見せるより、何をすればよいかが先に分かる
    const effects: ApprovalEffect[] =
      count === 0
        ? []
        : [
            carried
              ? { kind: "carry", paths: outcome.value.written }
              : {
                  kind: "warn",
                  text:
                    `承認済みチケットはまだコミットされていません。${PUSH_APPROVED_SCRIPT} がないので、` +
                    "導入スクリプト（scripts/ccnavi-setup.sh）で配ってください",
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
      ? "内容が更新されました（表示したあとに提案ファイルが書き換えられました）。何も承認していません。最新の内容を表示し直したので、見直してからもう一度承認してください"
      : "内容が更新されました（表示したあとに承認待ちの提案が増えたか減りました）。何も承認していません。最新の一覧を表示し直したので、見直してからもう一度承認してください";
    return move(
      state,
      { overlay: state.overlay, only: state.only, recheck: { notice, dropped: false } },
      { kind: "loadPreview", only: state.only },
    );
  }
  if ("partial" in outcome) {
    // 途中で止まった。置けたぶんは残っているので、その分だけコミット・pushを送り、何が書けて
    // 何が書けなかったかをオーバーレイで言う。1 件も置けていなければ何も送らない
    const placed = outcome.partial.placed.length > 0;
    return move(
      state,
      { overlay: { kind: "error", error: partialMessage(outcome.partial, carrier && placed) }, only: state.only },
      ...(placed
        ? [
            ...(carrier ? [{ kind: "carry", paths: outcome.partial.written } as const] : []),
            { kind: "refresh" } as const,
          ]
        : []),
    );
  }
  return move(state, { overlay: { kind: "error", error: outcome.error }, only: state.only });
}

/**
 * プロンプトを Claude Code に渡す。プロンプトは拡張ホストが持っている分を使う（画面から届いたものは使わない）。
 * 渡したらオーバーレイを閉じる
 */
function handedOver(state: ApprovalState, how: "promptCopy" | "promptOpen" | "promptRun"): ApprovalStep {
  const overlay = state.overlay;
  // 渡すプロンプトがあるのは、承認した（done）ときと決めた結果（prompt）のときだけ
  if (overlay?.kind !== "done" && overlay?.kind !== "prompt") {
    return stay(state);
  }
  const what = overlay.kind === "done" ? "承認のプロンプト" : (overlay.what ?? "プロンプト");
  const prompt = overlay.prompt;
  return move(
    state,
    CLOSED,
    how === "promptCopy"
      ? { kind: "copy", prompt, what }
      : how === "promptOpen"
        ? { kind: "openSession", prompt }
        : { kind: "runClaude", prompt },
  );
}

/**
 * 「計画を編集」。オーバーレイを**閉じてから**タブを開く（開いたままにしない）。タブで保存すると提案が
 * 書き換わり、この一覧はダイジェストが変わってもう承認できないので、承認はタブを保存したあとに開き直す
 */
function editWorkflow(state: ApprovalState, ticket: string): ApprovalStep {
  const overlay = state.overlay;
  if (overlay?.kind !== "preview") {
    return stay(state);
  }
  if (!overlay.preview.plans.some((plan) => plan.ticket === ticket)) {
    return stay(state);
  }
  return move(state, CLOSED, { kind: "openWorkflow", ticket });
}

/**
 * 承認を打つ直前の確かめ（`board-panel.ts` が `approve` のやることを行う前に呼ぶ）。打つ識別子のうち、
 * 計画編集タブに未保存の変更がある親（識別子の順）。空でなければ打たない。遷移が「押した時点」で見た
 * あとに未保存になったものを、ここで拾う。子の識別子は親のタブと取り違えない（識別子が違う）
 */
export function blockedBeforeApprove(tickets: readonly string[], unsaved: readonly string[]): string[] {
  const held = new Set(unsaved);
  return tickets.filter((ticket) => held.has(ticket)).sort();
}

/** 閉じてよいか。承認を打っている最中と、行き先を置いている最中は閉じない */
function closable(state: ApprovalState): boolean {
  const kind = state.overlay?.kind;
  return kind !== "approving" && kind !== "deciding";
}

/** 取り返せないプロンプト（残った指摘を決めた結果）を見せているか */
function keptPrompt(state: ApprovalState): boolean {
  return state.overlay?.kind === "prompt" && state.overlay.keep === true;
}

/** 残った指摘を決める途中（読み込み中・一覧・置いている最中）か */
function busyDeciding(state: ApprovalState): boolean {
  const kind = state.overlay?.kind;
  return kind === "decideLoading" || kind === "decidePreview" || kind === "deciding";
}

/**
 * ［レビュー完了］。残った指摘を読む。被せてよいのは、閉じている・読めなかった・前のプロンプト（取り返せないものを除く）の
 * 上だけで、承認の途中と承認したプロンプトの上、決める途中には被せない
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
      text: `親 ${parent} のワークツリーかフェーズ ${phase} がないので、未解決（Unresolved）の指摘を読み込めません`,
    });
  }
  // 残った指摘を決めるのは、依頼を出してからユーザが見ている間だけ。待ちでなければ依頼の記録が無い
  if (chip.reviewWaiting !== true) {
    return stay(
      state,
      { kind: "warn", text: `親 ${parent} のフェーズ ${chip.label} はユーザのレビュー待ちではありません。チケット管理画面を更新しました` },
      { kind: "refresh" },
    );
  }
  return move(
    state,
    { overlay: { kind: "decideLoading", parent, phase, tree, mr: chip.mrNumber }, only: state.only },
    { kind: "loadDecide", tree, parent, phase, mr: chip.mrNumber },
  );
}

/** 読み取れなかったときの言い方。件数は出さず、理由だけを言う */
function unreadable(reason: string): string {
  return `指摘を読み取れませんでした（${reason}）`;
}

/**
 * 残った指摘の読み取りが返った。頼んだとき（`decideLoading`）のままでなければ受けない。
 * 読み取れなかったときは件数を出さず、確定のボタンも出さない（エラーのオーバーレイ）。
 * 検証の本体は `parseDecidePreview`（頼んだ親・フェーズ・MR の番号との照合を含む）で、ここでも頼んだフェーズかを見る
 */
function decidePreviewed(state: ApprovalState, result: DecidePreviewParse): ApprovalStep {
  const overlay = state.overlay;
  if (overlay?.kind !== "decideLoading") {
    return stay(state);
  }
  if (!result.ok) {
    return move(state, { overlay: { kind: "error", error: unreadable(result.error) }, only: state.only });
  }
  // 頼んだフェーズの一覧か。別のフェーズの答えで開かない
  if (result.value.parent !== overlay.parent || result.value.phase !== overlay.phase) {
    return move(state, {
      overlay: { kind: "error", error: unreadable("頼んだフェーズと違う一覧が返りました。チケット管理画面を更新してから、もう一度レビュー完了を押してください") },
      only: state.only,
    });
  }
  return move(state, {
    overlay: { kind: "decidePreview", preview: result.value, tree: overlay.tree, notice: overlay.notice },
    only: state.only,
  });
}

/**
 * 「この対応方針を確定」（0 件なら「レビュー済みにする」）。見せた指摘の全部に行き先が 1 つずつ付いているときだけ送る。
 * ダイジェストは見せた一覧のもの。実行ファイルが今の指摘と比べ、違えば何も置かない
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
 * 「エージェントに相談するプロンプトをコピー」。1 件以上の指摘を見せているとき（`decidePreview`）だけ組む。
 * 置いている最中（`deciding`）と 0 件の画面では組まない。オーバーレイは閉じず、選びかけの対応方針にも触れない
 * （描き直しもしない。結果は `consultCopied` で知らせる）
 */
function consulted(state: ApprovalState, root: string): ApprovalStep {
  const overlay = state.overlay;
  if (overlay?.kind !== "decidePreview" || overlay.preview.threads.length === 0) {
    return stay(state);
  }
  const { preview, tree } = overlay;
  const prompt = consultPrompt({ root, parent: preview.parent, phase: preview.phase, mrUrl: preview.mr.url, parentTree: tree });
  return stay(state, { kind: "copyConsult", prompt });
}

/**
 * 相談のプロンプトをクリップボードに入れた結果。見せている画面（`decidePreview`）のときだけ受ける。
 * できたら短く知らせ、できなければ失敗と言う（画面の中に出す。選びかけの対応方針は保たれる）
 */
function consultCopied(state: ApprovalState, error: string | undefined): ApprovalStep {
  if (state.overlay?.kind !== "decidePreview") {
    return stay(state);
  }
  const overlay = state.overlay;
  const consult =
    error === undefined
      ? { ok: true, text: "相談のプロンプトをコピーしました。エージェントに貼って送ってください" }
      : { ok: false, text: `コピーできませんでした（${error}）` };
  return move(state, { overlay: { ...overlay, consult }, only: state.only });
}

/**
 * 置いた結果。承認の結果と同じく「この状態でなければ受けない」を置かない。 置かれたものは
 * 戻らないので、受けずに捨てるとユーザに届かない。
 * 表示は実行ファイルの答えに従う。`reviewed` が真ならレビュー済み。偽で `pending_extra` があれば、
 * 見せたあとに指摘が追加されたのでレビュー済みにしていない、と言う（続きの子が起きていなければ、決め直しへ戻れる）
 */
function decided(state: ApprovalState, outcome: DecideOutcome): ApprovalStep {
  if (outcome.ok) {
    const value = outcome.value;
    const effects: ApprovalEffect[] = [{ kind: "refresh" }];
    if (value.warning !== "") {
      effects.unshift({ kind: "warn", text: value.warning });
    }
    const extra = value.pending_extra.length;
    const issued = value.issue_url ? ` issue に回した分: ${value.issue_url}` : "";
    const handover = " Claude Code に伝えるプロンプトを用意しました。コピーして進行中のセッションに貼るか、新しいセッションで開いてください。送るときは自分で Enter を押してください。";
    const overlay = state.overlay;
    const where = overlay?.kind === "deciding" ? { parent: overlay.preview.parent, phase: overlay.preview.phase } : { parent: value.parent, phase: value.phase };
    let title: string;
    let note: string;
    if (value.followup) {
      title = `フェーズ ${value.phase} の未解決（Unresolved）指摘の対応方針を決めました`;
      note =
        `直す指摘を含む追加対応用の子チケット ${value.followup} を作成しました。フェーズを再開しました。` +
        // fix のスレッドはホスト上で未解決のまま残る。keep・issue のときに sh が返信して resolve する仕組みは対象外
        "修正を確認したら、ユーザがホスト上で該当スレッドを resolve する。エージェントは fix を理由に resolve しない。" +
        (extra > 0 ? `表示した後に追加された指摘 ${extra} 件は判断待ちです（今回の判断の対象外です）。` : "");
    } else if (value.reviewed) {
      title = `フェーズ ${value.phase} をレビュー済みにしました`;
      note = "フェーズはレビュー済みになりました。";
    } else if (extra > 0) {
      title = "表示した後に指摘が追加されたため、レビュー済みにしていません";
      note = `今回の判断は適用済みです。追加された指摘 ${extra} 件は判断待ちです（フェーズ ${value.phase} はレビュー済みにしていません）。指摘を読み直して、決め直してください。`;
    } else {
      title = `フェーズ ${value.phase} は、まだレビュー済みになっていません`;
      note = "実行ファイルの答えでは、レビュー済みになっていません。チケット管理画面を更新して、フェーズの状態を確かめてください。";
    }
    const retry = !value.followup && !value.reviewed && extra > 0;
    return move(
      state,
      {
        overlay: {
          kind: "prompt",
          title,
          note: `${note}${issued}${handover}`,
          prompt: value.prompt,
          what: "対応方針のプロンプト",
          // 続きの子が起きたときのプロンプトは取り返せない。決め直しへ戻れるとき（子が起きていない）は被せてよい
          keep: !retry,
          ...(retry ? { retry: where } : {}),
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
          mr: preview.mr.number,
          notice: "表示した指摘と今の指摘が違います（増えたか、変更されました）。見直してから決めてください",
        },
        only: state.only,
      },
      { kind: "loadDecide", tree, parent: preview.parent, phase: preview.phase, mr: preview.mr.number },
    );
  }
  const error = "mismatch" in outcome ? "表示した指摘と今の指摘が違います。チケット管理画面を更新したので、もう一度レビュー完了を押して決め直してください" : outcome.error;
  return move(state, { overlay: { kind: "error", error }, only: state.only }, { kind: "refresh" });
}
