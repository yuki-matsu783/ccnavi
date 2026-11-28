/**
 * ボード画面の本体。列とカード、選んだカードの右のパネル、絞り込み、承認のオーバーレイ。
 *
 * 見せる中身は拡張ホストが渡す（`BoardData`）。承認のオーバーレイも、動いた表示も、
 * 決めて覚えるのは拡張ホストで、ここは渡された分を出すだけ。画面が自分で持つのは、ユーザが触って
 * 決めるもの（絞り込み・折りたたんだ列・列の幅・選んだカード・「更新」を押したか）だけ。判定はしない。
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type JSX } from "react";
import { LoadError } from "../shared/LoadError.js";

import { FOLDED_FIRST, type Board, type Card } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import type { BoardData, ToBoard } from "../../core/board-view.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { OVERLAY_NAMES, parseBoardSample, type OverlayName } from "../../core/tour-board.js";
import { useHostMessages } from "../useHostMessages.js";
import { ESCAPE_LAYER, useEscape } from "../useEscape.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import { TOUR_IMAGES } from "../tours/images.js";
import boardTour from "../tours/board.yml";
import { mutePosts, post } from "./post.js";
import { Approval } from "./Approval.js";
import { CardPanel } from "./Panel.js";
import { LaneBoard, laneKey } from "./Lanes.js";
import { lastUpdatedText } from "../shared/format.js";
import { ModeBand } from "../shared/ModeBand.js";
import { MergeBand } from "./MergeBand.js";
import { cardKey, EMPTY, loadState, saveState, type ViewState } from "./state.js";
import { FILTER_LABELS } from "./text.js";

/** 親ごとの行を縦に積む幅（`Lanes.css`の`@media (max-width: 940px)`と同じ値）。ここより狭いと、右のパネルは選んだカードの下に開く */
const NARROW = "(max-width: 940px)";

/** 幅が狭いか。CSSの縦積みと同じ条件で、幅が変わったら描き直す。`matchMedia`の無い環境では広いものとして扱う */
function useNarrow(): boolean {
  const query = typeof window.matchMedia === "function" ? window.matchMedia(NARROW) : undefined;
  const [narrow, setNarrow] = useState(query?.matches === true);
  useEffect(() => {
    if (query === undefined) {
      return;
    }
    const onChange = (): void => setNarrow(query.matches);
    query.addEventListener?.("change", onChange);
    onChange();
    return () => query.removeEventListener?.("change", onChange);
  }, []);
  return narrow;
}

/**
 * 案内（`tours/board.yml`）。案内の間は今のチケットではなく、案内用の見本（`core/tour-board.ts`）を描く。
 * 読み込み時に形を確かめる（壊れていればテストが先に落とす）。
 */
const TOUR = tourOf(boardTour);
const NO_SCENE: Scene = {};

/** 手順の`scene`の文字列を`,`で割る */
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
  // 「MRの状況を確認」を押した直後から、拡張ホストの答え（確認中）が届くまで押せなくする
  const [mergePending, setMergePending] = useState(false);

  const board = data.kind === "board" ? data.board : undefined;
  // 受け取る側（メッセージ）はいまのボードを知らないので、描くたびにrefへ入れておく
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
  // 案内の間は、見本の上の操作を拡張ホストへ送らない（`tourDone`だけ返す）。描いたあとに切り替える
  useLayoutEffect(() => {
    mutePosts(touring);
    return () => mutePosts(false);
  }, [touring]);
  const courses = coursesOf(TOUR, setScene, TOUR_IMAGES);
  /**
   * 案内の間は、実際のチケットではなく見本のボードを描く（`tour-board.ts`）。見本は描くだけで、
   * 絞り込みのstateや承認の件数の元にはしない
   */
  const sample = useMemo(
    () => (touring && board !== undefined ? parseBoardSample(TOUR.sample, board.root, board.generatedAt) : undefined),
    [touring, board?.root, board?.generatedAt],
  );
  const shown = sample?.board ?? board;
  /** 案内の間に画面のstateを動かさない（見本の上の押す・入れるは何も起こさない） */
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
  // サイドパネルの行から選ばれたカード。カードを押したときと同じ選択にする（右のパネルに出す）。
  // 隠している絞り込み・畳んだ列・アーカイブの表示を外してから、描いたあとで送って焦点を当てる
  const [revealing, setRevealing] = useState<string | undefined>(undefined);
  const selectFromHost = (ticket: string): void => {
    const card = boardRef.current?.columns.flatMap((column) => column.cards).find((c) => c.id === ticket);
    if (card === undefined) {
      return;
    }
    setView((now) => ({
      ...now,
      project: now.project === EMPTY.project || now.project === card.project ? now.project : EMPTY.project,
      parent: now.parent === EMPTY.parent || now.parent === card.family ? now.parent : EMPTY.parent,
      attention: now.attention && card.attention,
      archived: now.archived || card.column === "archived",
      // 親は行の見出しにあり、列を畳んでも隠れない
      folded: card.isParent ? now.folded : now.folded.filter((state) => state !== card.column),
      selected: cardKey(card),
    }));
    setRevealing(cardKey(card));
  };
  useEffect(() => {
    if (revealing === undefined) {
      return;
    }
    setRevealing(undefined);
    const element = [...document.querySelectorAll<HTMLElement>(".card[data-id]")].find((el) => cardKey({ project: el.dataset.project ?? "", id: el.dataset.id ?? "" }) === revealing);
    if (element !== undefined) {
      element.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      element.focus();
    }
  }, [revealing]);
  useHostMessages<ToBoard>((message) => {
    if (message.type === "data" && message.data !== undefined) {
      // 中身だけ。開いた直後の絞り込みは1枚目のHTMLに埋まっていて、2枚目以降はfilterのメッセージで届く
      setData(message.data);
      setRefreshing(false);
      setMergePending(false);
    } else if (message.type === "filter" && typeof message.project === "string") {
      pickProject(message.project);
    } else if (message.type === "select" && typeof message.ticket === "string") {
      // 案内の間は見本を描いているので、実際のカードを探さない
      if (!touring) {
        selectFromHost(message.ticket);
      }
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
  // 送るのはボードの横だけ。列のscrollIntoViewだと、列の高さに合わせて文書の縦まで動いてしまう
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

  // 絞り込み中かどうかはbodyに出す。カードの表示・非表示はCSS（.card.hidden）が受け持つ
  useEffect(() => {
    document.body.classList.toggle("filtering", filtering);
  }, [filtering]);

  // 選んだカード。覚えるのは実際のボードのカードだけ（消えたカードなら外す）。
  // 読み直せなかった画面（boardが無い）では、覚えていた分を触らない
  const selected = board === undefined || board.columns.some((column) => column.cards.some((card) => cardKey(card) === view.selected)) ? view.selected : EMPTY.selected;

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
    saveState({ project, parent, attention, archived, folded: view.folded, widths: view.widths, selected, opened: view.opened });
  }, [board === undefined, project, parent, attention, archived, view.folded, view.widths, selected, view.opened]);

  // 案内の間は見本の上の様子を描く。絞り込み・畳んだ列・幅は`scene`が決め、実際の`view`は使わない（動かさない）
  const shownProject = touring ? (typeof scene.project === "string" ? scene.project : EMPTY.project) : project;
  const shownParent = touring ? (typeof scene.parent === "string" ? scene.parent : EMPTY.parent) : parent;
  const shownAttention = touring ? scene.attention === true : attention;
  const shownArchived = touring ? scene.archived === true : archived;
  // 案内でも、手順が`fold`を書かなければ実際の既定（完了と取り消しを畳む）と同じに描く
  const shownFolded = touring ? (typeof scene.fold === "string" ? sceneList(scene.fold) : FOLDED_FIRST) : view.folded;
  // 案内の`open`は見本の親の識別子で書く。開いた行の覚え方（プロジェクトと親の組）に直す
  const shownOpened = touring
    ? (shown?.columns.flatMap((column) => column.cards).filter((card) => card.isParent && sceneList(scene.open).includes(card.id)).map((card) => laneKey(card)) ?? [])
    : view.opened;
  const shownWidths: Readonly<Record<string, number>> = touring ? {} : view.widths;
  // 案内の間に選んで見せるカードは、手順の`scene.select`（見本の識別子）が決める。実際の選択（`view.selected`）には書かない
  const selectedOf = (card: Card): boolean => (touring ? typeof scene.select === "string" && scene.select === card.id : selected === cardKey(card));
  const selectCard = (card: Card): void => change((now) => ({ ...now, selected: cardKey(card) }));
  const closePanel = (): void => change((now) => ({ ...now, selected: EMPTY.selected }));
  const narrow = useNarrow();
  const shownParents = shown === undefined ? [] : shownArchived ? [...shown.parents, ...shown.archivedParents] : shown.parents;
  // マージ状況の帯。実際の結果は拡張ホストが持ち（ボードが開いている間だけ）、ここは出すだけ。案内の間は見本の結果を、
  // 手順の`scene.merge`が指すときだけ出す（実際の結果は見せない・書き換えない）
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

  // 「承認待ちN件を承認」は、押したときに承認の対象になるもの（絞り込みで見えている承認待ち）の数にする。
  // 「絞り込み無し」は空のリストではなくfilteredで言う。空を「全部」に読ませると、0件のつもりが全部承認になってしまう。
  const visiblePending =
    shown?.columns.flatMap((column) => column.cards.filter((card) => card.pendingApproval && !hiddenOf(card)).map((card) => card.id)) ?? [];

  // 出している列（アーカイブの列は「アーカイブ済みチケットを表示」がONのときだけ）
  const shownColumns = shown?.columns.filter((column) => shownArchived || column.state !== "archived") ?? [];
  // 親の行の見出しは、その親か子のどれかが見えている間は出る（子だけが絞り込みに残っても、親が分かるように）
  const headShown = (parentCard: Card): boolean =>
    !hiddenOf(parentCard) || shownColumns.some((column) => column.cards.some((card) => !card.isParent && card.family === parentCard.id && card.project === parentCard.project && !hiddenOf(card)));
  // 右のパネルに出すカード。選んだカードが絞り込み・畳んだ列で隠れている間は出さない（選択は覚えたまま）。
  // 親は行の見出しにあるので、列を畳んでも隠れない
  const panelCard = shownColumns
    .flatMap((column) => column.cards)
    .find((card) => selectedOf(card) && (card.isParent ? headShown(card) : !hiddenOf(card) && !shownFolded.includes(card.column)));
  // Escでパネルを閉じる。オーバーレイ・案内が出ていれば、そちらが先に受ける
  useEscape(panelCard !== undefined && !touring, ESCAPE_LAYER.panel, closePanel);
  const panelOf = (card: Card): JSX.Element | null =>
    panelCard !== undefined && card === panelCard ? <CardPanel card={card} below={narrow} onClose={closePanel} /> : null;

  return (
    <>
      {shown === undefined ? (
        <LoadError
          heading="チケット管理画面を読み込めませんでした。原因を直してから「更新」を押してください。"
          error={data.kind === "error" ? data.error : ""}
          onReload={() => post({ type: "refresh" })}
        />
      ) : (
        <>
          {sample !== undefined ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
          {touring ? null : <ModeBand mode={shown.mode} />}
          <ScreenHeader courses={courses} onPick={tour.start}>
            <div className="summary" data-tour="summary">
              {shown.pendingApproval.length > 0 ? <span className="pending warn">承認待ち{shown.pendingApproval.length}件</span> : null}
              {shown.issueCount > 0 ? <span className="issues warn">不備{shown.issueCount}件</span> : null}
              <span className="counts">
                残り{shown.remainingCount} / 全{shown.totalCount}
              </span>
            </div>
            <div className="controls">
              {/* 絞り込みと操作は段を分ける。操作の段はボタンが3つだけなので、狭い幅でも主ボタン（承認）が次の行に落ちず、常に右端に出る */}
              <div className="filters">
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
              </div>
              <div className="actions">
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
                  title="片付けの記録を持つ親のMRが、マージ済みかを確かめます（読むだけで、何も変えません）"
                  onClick={() => {
                    if (touring) {
                      return;
                    }
                    setMergePending(true);
                    post({ type: "mergeCheck" });
                  }}
                >
                  <span className="spin" aria-hidden="true" />
                  <span className="label">{mergeShown?.checking === true || mergePending ? "確認中…" : "MRの状況を確認"}</span>
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
                  承認待ち{visiblePending.length}件を承認
                </button>
              </div>
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
          {shown.totalCount === 0 && !(shownArchived && shown.archivedCount > 0) && (mergeShown?.rows.length ?? 0) === 0 ? (
            <>
              <p className="board-empty">チケットなし</p>
              {/* finish済みでマージ待ちの親は、MRの状況を確認するまでボードに出ない。空のボードと区別できるよう一言添える */}
              {mergeShown === undefined && !touring ? <p className="merge-note" data-merge="hint">マージ待ちのチケットを見たい場合は、［MRの状況を確認］をクリックしてください</p> : null}
            </>
          ) : null}
          <div className={panelCard !== undefined && !narrow ? "board-area with-panel" : "board-area"}>
          <LaneBoard
            columns={shownColumns}
            folded={shownFolded}
            widths={shownWidths}
            opened={shownOpened}
            hiddenOf={hiddenOf}
            movedOf={movedOf}
            selectedOf={selectedOf}
            onSelect={selectCard}
            panelOf={narrow ? panelOf : () => null}
            onFold={(state, folded) =>
              change((now) => ({
                ...now,
                folded: folded ? [...now.folded, state] : now.folded.filter((f) => f !== state),
              }))
            }
            onWidth={(state, width) =>
              change((now) => {
                const widths = { ...now.widths };
                if (width === undefined) {
                  delete widths[state];
                } else {
                  widths[state] = width;
                }
                return { ...now, widths };
              })
            }
            onOpen={(lane, opened) =>
              change((now) => ({
                ...now,
                opened: opened ? [...now.opened, lane] : now.opened.filter((o) => o !== lane),
              }))
            }
          />
          {panelCard !== undefined && !narrow ? panelOf(panelCard) : null}
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
      {lastUpdatedText(board.generatedAt)} / {board.root}
    </footer>
  );
}
