/**
 * ボード画面の本体。列とカード、絞り込み、承認のオーバーレイ。
 *
 * 見せる中身は拡張ホストが渡す（`BoardData`）。承認のオーバーレイも、動いた表示も、
 * 決めて覚えるのは拡張ホストで、ここは渡された分を出すだけ。画面が自分で持つのは、ユーザが触って
 * 決めるもの（絞り込み・折りたたんだ列・列の幅・「更新」を押したか）だけ。判定はしない。
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type JSX } from "react";

import type { Board, BoardColumn, Card } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import type { BoardData, ToBoard } from "../../core/board-view.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { OVERLAY_NAMES, parseBoardSample, type OverlayName } from "../../core/tour-board.js";
import { useHostMessages } from "../useHostMessages.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import boardTour from "../tours/board.yml";
import { mutePosts, post } from "./post.js";
import { Approval } from "./Approval.js";
import { CardItem } from "./Card.js";
import { MergeBand } from "./MergeBand.js";
import { detailsKey, EMPTY, loadState, saveState, type ViewState } from "./state.js";
import { FILTER_LABELS } from "./text.js";

/** 列の最小の幅（px）。ドラッグでもこれより狭くしない。CSS の min-width と同じ値 */
const MIN_WIDTH = 220;

/**
 * 案内（`tours/board.yml`）。案内の間は今のチケットではなく、案内用の見本（`core/tour-board.ts`）を描く。
 * 読み込み時に形を確かめる（壊れていればテストが先に落とす）。
 */
const TOUR = tourOf(boardTour);
const NO_SCENE: Scene = {};

/** 手順の `scene` の文字列を `,` で割る */
function sceneList(value: Scene[string] | undefined): readonly string[] {
  return typeof value === "string" && value !== "" ? value.split(",").map((item) => item.trim()) : [];
}

/**
 * プロジェクトの絞り込みの候補。「すべて」と、プロジェクトがあれば「ワークスペース（プロジェクト外）」（空）と各プロジェクト。
 * プロジェクトが無いボードでは欄を出さないので、候補も「すべて」だけ。覚えていた値がここに無ければ使わない
 * （欄が無いまま「絞り込み中」になると、ユーザには解除する手立てが無い）
 */
function projectOptions(board: Board | undefined): readonly string[] {
  return board === undefined || board.projects.length === 0 ? [EMPTY.project] : [EMPTY.project, "", ...board.projects];
}

export function App({ initial }: { readonly initial: BoardData }): JSX.Element {
  const [data, setData] = useState<BoardData>(initial);
  const [view, setView] = useState<ViewState>(() => {
    const saved = loadState();
    // プロジェクト管理画面から「このプロジェクトで絞って開く」で来たとき。候補に無ければ触らない
    const asked = initial.filter;
    const board = initial.kind === "board" ? initial.board : undefined;
    return asked !== undefined && projectOptions(board).includes(asked) ? { ...saved, project: asked } : saved;
  });
  // 「更新」は押した時点で非活性にして回り記号を出す。活性に戻すのは、拡張ホストが読み直しを終えて
  // 次の中身を渡したとき。読み直しが失敗しても中身は届く（エラーの画面になる）ので、ここで戻す経路は要らない。
  // 実行ファイルが返らない場合は期限（ccnavi.ts）で打ち切る。
  const [refreshing, setRefreshing] = useState(false);
  // 「マージ状況を確認」を押した直後から、拡張ホストの答え（確認中）が届くまで押せなくする
  const [mergePending, setMergePending] = useState(false);

  const board = data.kind === "board" ? data.board : undefined;
  // 受け取る側（メッセージ）はいまのボードを知らないので、描くたびに ref へ入れておく
  const boardRef = useRef<Board | undefined>(board);
  boardRef.current = board;
  // 初回の案内はボードが出てから始める。承認のオーバーレイが出ている間は、その下を指しても見えないので待つ
  /**
   * 案内の手順ごとの画面の様子（絞り込み・畳んだ列・出すオーバーレイ）。見本の上だけに働き、実際の絞り込みや
   * 畳んだ列（`view`）には触れないので、閉じたあとに戻すものは無い
   */
  const [scene, setScene] = useState<Scene>(NO_SCENE);
  const tour = useTour(board !== undefined && data.approval === undefined, {
    onStart: () => setScene(NO_SCENE),
    onEnd: () => {
      setScene(NO_SCENE);
      post({ type: "tourDone" });
    },
  });
  const requestTour = tour.request;
  const touring = tour.touring;
  // 案内の間は、見本の上の操作を拡張ホストへ送らない（`tourDone` だけ返す）。描いたあとに切り替える
  useLayoutEffect(() => {
    mutePosts(touring);
    return () => mutePosts(false);
  }, [touring]);
  const courses = coursesOf(TOUR, setScene);
  /**
   * 案内の間は、実際のチケットではなく見本のボードを描く（`tour-board.ts`）。見本は描くだけで、
   * 絞り込みの state や承認の件数の元にはしない
   */
  const sample = useMemo(
    () => (touring && board !== undefined ? parseBoardSample(TOUR.sample, board.root, board.generatedAt) : undefined),
    [touring, board?.root, board?.generatedAt],
  );
  const shown = sample?.board ?? board;
  /** 案内の間に画面の state を動かさない（見本の上の押す・入れるは何も起こさない） */
  const change = (next: (now: ViewState) => ViewState): void => {
    if (!touring) {
      setView(next);
    }
  };

  // 拡張ホストが指定した絞り込み。候補に無ければ何もしない（いまの絞り込みを外さない）
  const pickProject = (value: string): void => {
    if (!projectOptions(boardRef.current).includes(value)) {
      return;
    }
    setView((now) => ({ ...now, project: value }));
  };
  useHostMessages<ToBoard>((message) => {
    if (message.type === "data" && message.data !== undefined) {
      // 中身だけ。開いた直後の絞り込みは 1 枚目の HTML に埋まっていて、2 枚目以降は filter のメッセージで届く
      setData(message.data);
      setRefreshing(false);
      setMergePending(false);
    } else if (message.type === "filter" && typeof message.project === "string") {
      pickProject(message.project);
    } else if (message.type === "tour") {
      requestTour();
    }
  }, () => post({ type: "ready" }));

  // 覚えていた値が候補に無ければ（その親が消えた等）「すべて」のまま。覚え直すのも、外した後の値
  const project = projectOptions(board).includes(view.project) ? view.project : EMPTY.project;
  // アーカイブを表示しているときは、退避した親も絞り込みの候補に入る（選んでいた親が退避されても外さない）
  const parentOptions = board === undefined ? [] : view.archived ? [...board.parents, ...board.archivedParents] : board.parents;
  const parent = parentOptions.some((p) => p.id === view.parent) ? view.parent : EMPTY.parent;
  // 読み直せなかった画面には絞り込みの部品が無い。覚えていた値が有効なままにすると、
  // 出すものが無いのに「絞り込み中」になる
  const attention = board !== undefined && view.attention;
  // アーカイブ済みのチケット（手元の退避）を出すか。既定は出さない。隠すのが既定なので、絞り込み（filtering）には数えない
  const archived = view.archived;
  const filtering = project !== EMPTY.project || parent !== EMPTY.parent || attention;
  // 「アーカイブ済みチケットを表示」を入れたら、右端に足されるアーカイブの列まで横へ送る。
  // ユーザが入れたときだけ動かす（覚えていた値で開き直したときには動かさない）。
  // 送るのはボードの横だけ。列の scrollIntoView だと、列の高さに合わせて文書の縦まで動いてしまう
  const revealArchived = useRef(false);
  useEffect(() => {
    if (!archived || !revealArchived.current) {
      return;
    }
    revealArchived.current = false;
    const board = document.querySelector<HTMLElement>(".board");
    if (board !== null) {
      board.scrollLeft = board.scrollWidth;
    }
  }, [archived]);

  // 絞り込み中かどうかは body に出す。カードの表示・非表示は CSS（.card.hidden）が受け持つ
  useEffect(() => {
    document.body.classList.toggle("filtering", filtering);
  }, [filtering]);

  // 詳細を開いているカード。覚えるのは実際のボードのカードだけ（消えたカードの分は破棄する）。
  // 読み直せなかった画面（board が無い）では、覚えていた分を触らない
  const cardKeys = board === undefined ? undefined : new Set(board.columns.flatMap((column) => column.cards.map(detailsKey)));
  const details = cardKeys === undefined ? view.details : view.details.filter((key) => cardKeys.has(key));

  /**
   * 覚える。外した後の値（候補に無い絞り込みは「すべて」）で書くので、消えた親の絞り込みは
   * ここで正規化される。ユーザが触ったときだけでなく、拡張ホストから絞り込みを渡されたときも通る。
   *
   * 読み直せなかった画面（絞り込みの部品が無い）では書かない。書くと、覚えていた絞り込みが
   * 既定で上書きされる。
   */
  useEffect(() => {
    if (board === undefined) {
      return;
    }
    saveState({ project, parent, attention, archived, folded: view.folded, widths: view.widths, details });
  }, [board === undefined, project, parent, attention, archived, view.folded, view.widths, details.join("\u0000")]);

  // 案内の間は見本の上の様子を描く。絞り込み・畳んだ列・幅は `scene` が決め、実際の `view` は使わない（動かさない）
  const shownProject = touring ? (typeof scene.project === "string" ? scene.project : EMPTY.project) : project;
  const shownParent = touring ? (typeof scene.parent === "string" ? scene.parent : EMPTY.parent) : parent;
  const shownAttention = touring ? scene.attention === true : attention;
  const shownArchived = touring ? scene.archived === true : archived;
  const shownFolded = touring ? sceneList(scene.fold) : view.folded;
  const shownWidths: Readonly<Record<string, number>> = touring ? {} : view.widths;
  // 案内の間に詳細を開いて見せるカードは、手順の `scene.details`（識別子を , で区切る）が決める。実際の開閉（`view.details`）には書かない
  const shownDetails: readonly string[] = touring ? sceneList(scene.details) : details;
  const detailsOpenOf = (card: Card): boolean => (touring ? shownDetails.includes(card.id) : shownDetails.includes(detailsKey(card)));
  const toggleDetails = (card: Card, open: boolean): void => {
    if (touring) {
      return;
    }
    const key = detailsKey(card);
    setView((now) => ({ ...now, details: open ? [...now.details.filter((k) => k !== key), key] : now.details.filter((k) => k !== key) }));
  };
  const shownParents = shown === undefined ? [] : shownArchived ? [...shown.parents, ...shown.archivedParents] : shown.parents;
  // マージ状況の帯。実際の結果は拡張ホストが持ち（ボードが開いている間だけ）、ここは出すだけ。案内の間は見本の結果を、
  // 手順の `scene.merge` が指すときだけ出す（実際の結果は見せない・書き換えない）
  const mergeShown = touring ? (scene.merge === true ? sample?.merge : undefined) : data.kind === "board" ? data.merge : undefined;
  const overlay =
    touring && sample !== undefined
      ? typeof scene.overlay === "string" && (OVERLAY_NAMES as readonly string[]).includes(scene.overlay)
        ? sample.overlays[scene.overlay as OverlayName]
        : undefined
      : data.approval;

  // 前の読み直しから動いたカード。数えるのは拡張ホスト（`core/board-moved.ts`）で、画面は出すだけ。見本には見本の動いたカードがある
  const moved = new Map((sample?.moved ?? (data.kind === "board" ? (data.moved ?? []) : [])).map((m) => [m.id, m]));
  const movedOf = (card: Card): Moved | undefined => (card.column === "archived" ? undefined : moved.get(card.id));

  const hiddenOf = (card: Card): boolean =>
    (!shownArchived && card.column === "archived") ||
    (shownProject !== EMPTY.project && card.project !== shownProject) ||
    (shownParent !== EMPTY.parent && card.family !== shownParent) ||
    (shownAttention && !card.attention);

  // 「承認待ち N 件を承認」は、押したときに承認の対象になるもの（絞り込みで見えている承認待ち）の数にする。
  // 「絞り込み無し」は空の配列ではなく filtered で言う。空を「全部」に読ませると、0 件のつもりが全部承認になってしまう。
  const visiblePending =
    shown?.columns.flatMap((column) => column.cards.filter((card) => card.pendingApproval && !hiddenOf(card)).map((card) => card.id)) ?? [];

  return (
    <>
      {shown === undefined ? (
        <>
          <p className="board-empty">チケット管理画面を読み込めませんでした。原因を直してから「ccnavi ボード: チケット管理を更新」を実行してください。</p>
          <pre className="load-error">{data.kind === "error" ? data.error : ""}</pre>
        </>
      ) : (
        <>
          {sample !== undefined ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
          <ScreenHeader courses={courses} onPick={tour.start}>
            <div className="summary" data-tour="summary">
              {shown.pendingApproval.length > 0 ? <span className="pending warn">承認待ち {shown.pendingApproval.length} 件</span> : null}
              {shown.issueCount > 0 ? <span className="issues warn">不備 {shown.issueCount} 件</span> : null}
              <span className="counts">
                残り {shown.remainingCount} / 全 {shown.totalCount}
              </span>
            </div>
            <div className="controls">
              {shown.projects.length > 0 ? (
                <label className="filter" data-tour="filter-project">
                  プロジェクト
                  <select id="project-filter" value={shownProject} onChange={(event) => change((now) => ({ ...now, project: event.target.value }))}>
                    <option value="*">すべて</option>
                    <option value="">ワークスペース（プロジェクト外）</option>
                    {shown.projects.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              {shownParents.length > 0 ? (
                <label className="filter" data-tour="filter-parent">
                  親
                  <select id="parent-filter" value={shownParent} onChange={(event) => change((now) => ({ ...now, parent: event.target.value }))}>
                    <option value="*">すべて</option>
                    {shownParents.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.title === "" ? p.id : `${p.id} ${p.title}`}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <label className="filter attention" data-tour="filter-attention" title={FILTER_LABELS.attentionTitle}>
                <input type="checkbox" id="attention-filter" checked={shownAttention} onChange={(event) => change((now) => ({ ...now, attention: event.target.checked }))} /> {FILTER_LABELS.attention}
              </label>
              <label className="filter archived" data-tour="filter-archived" title={FILTER_LABELS.archivedTitle}>
                <input type="checkbox" id="archived-filter" checked={shownArchived} onChange={(event) => {
                    if (touring) {
                      return;
                    }
                    const checked = event.target.checked;
                    revealArchived.current = checked;
                    setView((now) => ({ ...now, archived: checked }));
                  }} /> {FILTER_LABELS.archived}
              </label>
              <button
                type="button"
                className={refreshing ? "action busy" : "action"}
                data-action="refresh"
                data-tour="refresh"
                disabled={refreshing}
                aria-busy={refreshing ? "true" : undefined}
                onClick={() => {
                  if (touring) {
                    return;
                  }
                  setRefreshing(true);
                  post({ type: "refresh" });
                }}
              >
                <span className="spin" aria-hidden="true" />
                <span className="label">{refreshing ? "更新中…" : "更新"}</span>
              </button>
              <button
                type="button"
                className={mergeShown?.checking === true || mergePending ? "action busy" : "action"}
                data-action="merge-check"
                data-tour="merge-check"
                disabled={mergeShown?.checking === true || mergePending}
                aria-busy={mergeShown?.checking === true || mergePending ? "true" : undefined}
                title="片付けの記録を持つ親のマージリクエストが、マージ済みかを確かめます（読むだけで、何も変えません）"
                onClick={() => {
                  if (touring) {
                    return;
                  }
                  setMergePending(true);
                  post({ type: "mergeCheck" });
                }}
              >
                <span className="spin" aria-hidden="true" />
                <span className="label">{mergeShown?.checking === true || mergePending ? "確認中…" : "マージ状況を確認"}</span>
              </button>
              <button
                type="button"
                className="action primary"
                data-action="approve"
                data-tour="approve"
                disabled={visiblePending.length === 0}
                onClick={() => {
                  if (!touring) {
                    post({ type: "approve", tickets: visiblePending, filtered: filtering });
                  }
                }}
              >
                承認待ち {visiblePending.length} 件を承認
              </button>
            </div>
          </ScreenHeader>
          {mergeShown !== undefined ? <MergeBand merge={mergeShown} board={shown} touring={touring} /> : null}
          {(sample === undefined ? board?.problems.length ?? 0 : 0) > 0 ? (
            <ul className="problems">
              {(board?.problems ?? []).map((problem, i) => (
                <li key={i}>{problem}</li>
              ))}
            </ul>
          ) : null}
          {/* 退避のチケットを表示しているときは、アーカイブの列に並ぶので「チケットなし」は出さない */}
          {shown.totalCount === 0 && !(shownArchived && shown.archivedCount > 0) ? <p className="board-empty">チケットなし</p> : null}
          <div className="board" data-tour="board">
            {/* アーカイブの列は「アーカイブ済みチケットを表示」が ON のときだけ出す（OFF ならカードは全部隠れる） */}
            {shown.columns.filter((column) => shownArchived || column.state !== "archived").map((column) => (
              <Column
                key={column.state}
                column={column}
                hiddenOf={hiddenOf}
                movedOf={movedOf}
                detailsOpenOf={detailsOpenOf}
                onDetails={toggleDetails}
                folded={shownFolded.includes(column.state)}
                width={shownWidths[column.state]}
                onFold={(folded) =>
                  change((now) => ({
                    ...now,
                    folded: folded ? [...now.folded, column.state] : now.folded.filter((f) => f !== column.state),
                  }))
                }
                onWidth={(width) =>
                  change((now) => {
                    const widths = { ...now.widths };
                    if (width === undefined) {
                      delete widths[column.state];
                    } else {
                      widths[column.state] = width;
                    }
                    return { ...now, widths };
                  })
                }
              />
            ))}
          </div>
          <Footer board={shown} />
        </>
      )}
      {overlay !== undefined ? <Approval overlay={overlay} unsaved={touring ? [] : (data.unsaved ?? [])} /> : null}
      {touring && board !== undefined ? <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} /> : null}
    </>
  );
}

function Footer({ board }: { readonly board: Board }): JSX.Element {
  return (
    <footer className="foot">
      最終更新 {board.generatedAt} / {board.root}
    </footer>
  );
}

/**
 * 1 つの列。見出しを押すと折りたたみ、右端の取っ手をドラッグすると幅が px で固定される
 * （ダブルクリックで元の伸び縮みに戻る）。件数は絞り込みで見えているカードの数。
 * 上部の集計（残り・全・不備・承認待ち）は絞り込みに関係なくボード全体の数のまま。
 */
function Column({
  column,
  hiddenOf,
  movedOf,
  detailsOpenOf,
  onDetails,
  folded,
  width,
  onFold,
  onWidth,
}: {
  readonly column: BoardColumn;
  readonly hiddenOf: (card: Card) => boolean;
  readonly movedOf: (card: Card) => Moved | undefined;
  readonly detailsOpenOf: (card: Card) => boolean;
  readonly onDetails: (card: Card, open: boolean) => void;
  readonly folded: boolean;
  readonly width: number | undefined;
  readonly onFold: (folded: boolean) => void;
  readonly onWidth: (width: number | undefined) => void;
}): JSX.Element {
  const section = useRef<HTMLElement>(null);
  const visible = column.cards.filter((card) => !hiddenOf(card)).length;
  // ドラッグの最中に列が消えたら（読み直せずエラーの画面に替わる）`pointerup` を受ける相手が居なくなり、
  // 後片付けが走らない。body に付けたクラスを残すと、カーソルが変わったまま文字も選べなくなる
  useEffect(() => () => document.body.classList.remove("resizing"), []);
  const classes = ["column"];
  if (width !== undefined) {
    classes.push("sized");
  }
  if (folded) {
    classes.push("folded");
  }

  /**
   * ドラッグしている間の幅は DOM に直に書く。動かすたびに React へ流すと、指を動かすあいだ中
   * ボード全体を描き直すことになる。離したときに覚える（そこで React の持ち物に戻る）。
   */
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0 || section.current === null) {
      return;
    }
    event.preventDefault();
    const handle = event.currentTarget;
    const element = section.current;
    const startX = event.clientX;
    const startWidth = element.getBoundingClientRect().width;
    let last = Math.max(MIN_WIDTH, Math.round(startWidth));
    // 動かさずに押して離しただけなら、幅は決めない。押しただけで px に固定されると、
    // その列は窓の幅に追従しなくなる
    let dragged = false;
    element.classList.add("resizing");
    document.body.classList.add("resizing");
    handle.setPointerCapture(event.pointerId);
    const move = (moved: PointerEvent): void => {
      last = Math.max(MIN_WIDTH, Math.round(startWidth + moved.clientX - startX));
      dragged = true;
      element.classList.add("sized");
      element.style.width = `${last}px`;
    };
    const finish = (): void => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", finish);
      handle.removeEventListener("pointercancel", finish);
      element.classList.remove("resizing");
      document.body.classList.remove("resizing");
      if (dragged) {
        onWidth(last);
      }
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
  };

  return (
    <section className={classes.join(" ")} data-state={column.state} data-tour={`column-${column.state}`} style={width === undefined ? undefined : { width: `${width}px` }} ref={section}>
      <h2>
        <button
          type="button"
          className="fold"
          data-fold={column.state}
          aria-expanded={folded ? "false" : "true"}
          title="列を折りたたむ／広げる"
          onClick={() => onFold(!folded)}
        >
          <span className="fold-mark" aria-hidden="true" />
          <span className="label">{column.label}</span>
        </button>
        <span className="count">{visible}</span>
      </h2>
      {column.cards.length === 0 ? (
        <p className="empty">チケットなし</p>
      ) : (
        <ul className="cards">
          {column.cards.map((card) => (
            <CardItem
              key={`${card.project}/${card.id}`}
              card={card}
              hidden={hiddenOf(card)}
              moved={movedOf(card)}
              detailsOpen={detailsOpenOf(card)}
              onDetails={(open) => onDetails(card, open)}
            />
          ))}
        </ul>
      )}
      <div className="resizer" data-resize={column.state} title="ドラッグで幅を変える／ダブルクリックで戻す" onPointerDown={onPointerDown} onDoubleClick={() => onWidth(undefined)} />
    </section>
  );
}
