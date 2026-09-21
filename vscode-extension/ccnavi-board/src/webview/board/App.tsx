/**
 * ボード画面の本体。列とカード、絞り込み、承認のオーバーレイ。
 *
 * 見せる中身は拡張ホストが渡す（`BoardData`）。画面が自分で持つのは、人が触って決めるもの
 * （絞り込み・畳んだ列・列の幅・「更新」を押したか）だけ。判定はしない。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { Board, BoardColumn, Card } from "../../core/board.js";
import type { BoardData, ToBoard } from "../../core/board-view.js";
import { applyAppearance } from "../appearance.js";
import { post } from "./post.js";
import { Approval } from "./Approval.js";
import { CardItem } from "./Card.js";
import { EMPTY, loadState, saveState, type ViewState } from "./state.js";

/** 列の最小の幅（px）。ドラッグでもこれより狭くしない。CSS の min-width と同じ値 */
const MIN_WIDTH = 220;

/**
 * プロジェクトの絞り込みの候補。「すべて」と、プロジェクトがあれば「ワークスペース自身ス本体」（空）と各プロジェクト。
 * プロジェクトが無いボードでは欄を出さないので、候補も「すべて」だけ。覚えていた値がここに無ければ効かせない
 * （欄が無いまま「絞り込み中」になると、人には解除する手立てが無い）
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
  // 「更新」は押した瞬間に非活性にして回り記号を出す。活性に戻すのは、拡張ホストが読み直しを終えて
  // 次の中身を渡したとき。読み直しが失敗しても中身は届く（エラーの画面になる）ので、ここで戻す道は要らない。
  // 実行ファイルが返らない場合は期限（ccnavi.ts）が切る。
  const [refreshing, setRefreshing] = useState(false);

  const board = data.kind === "board" ? data.board : undefined;
  // 受け口（メッセージ）はいまのボードを知らないので、描くたびに写しておく
  const boardRef = useRef<Board | undefined>(board);
  boardRef.current = board;

  useEffect(() => {
    /** 拡張ホストが指す絞り込み。候補に無ければ何もしない（いまの絞り込みを外さない） */
    const pickProject = (value: string): void => {
      if (!projectOptions(boardRef.current).includes(value)) {
        return;
      }
      setView((now) => ({ ...now, project: value }));
    };
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToBoard>;
      if (message.type === "data" && message.data !== undefined) {
        // 中身だけ。開いた直後の絞り込みは 1 枚目の HTML に埋まっていて、2 枚目からは filter で届く
        setData(message.data);
        setRefreshing(false);
      } else if (message.type === "filter" && typeof message.project === "string") {
        pickProject(message.project);
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    // 組み上がったと伝える。裏に回って捨てられた画面は、表に戻ると拡張ホストが入れてある HTML から
    // 作り直される。その HTML は少し古いことがあるので、いまの中身をもらい直す
    post({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // 覚えていた値が候補に無ければ（その親が消えた等）「すべて」のまま。覚え直すのも、落とした後の値
  const project = projectOptions(board).includes(view.project) ? view.project : EMPTY.project;
  const parent = board !== undefined && board.parents.some((p) => p.id === view.parent) ? view.parent : EMPTY.parent;
  // 読み直せなかった画面には絞り込みの部品が無い。覚えていた値が効いたままにすると、
  // 出すものが無いのに「絞り込み中」になる
  const attention = board !== undefined && view.attention;
  const filtering = project !== EMPTY.project || parent !== EMPTY.parent || attention;

  // 絞り込み中かどうかは body に出す。カードの表示・非表示は CSS（.card.hidden）が受け持つ
  useEffect(() => {
    document.body.classList.toggle("filtering", filtering);
  }, [filtering]);

  /**
   * 覚える。落とした後の値（候補に無い絞り込みは「すべて」）で書くので、消えた親の絞り込みは
   * ここで正規化される。人が触ったときだけでなく、拡張ホストから絞り込みを渡されたときも通る。
   *
   * 読み直せなかった画面（絞り込みの部品が無い）では書かない。書くと、覚えていた絞り込みが
   * 既定で上書きされる。
   */
  useEffect(() => {
    if (board === undefined) {
      return;
    }
    saveState({ project, parent, attention, folded: view.folded, widths: view.widths });
  }, [board === undefined, project, parent, attention, view.folded, view.widths]);

  const hiddenOf = (card: Card): boolean =>
    (project !== EMPTY.project && card.project !== project) ||
    (parent !== EMPTY.parent && card.family !== parent) ||
    (attention && !card.attention);

  // 「承認待ち N 件を承認」は、押したときに承認の対象になるもの（絞り込みで見えている承認待ち）の数にする。
  // 「絞り込み無し」は空の並びではなく filtered で言う。空を「全部」に読ませると、0 件のつもりが全部承認に化ける。
  const visiblePending =
    board?.columns.flatMap((column) => column.cards.filter((card) => card.pendingApproval && !hiddenOf(card)).map((card) => card.id)) ?? [];

  return (
    <>
      {board === undefined ? (
        <>
          <p className="board-empty">ボードを読み直せなかった。原因を直してから「ccnavi ボード: ボードを更新」を実行する。</p>
          <pre className="load-error">{data.kind === "error" ? data.error : ""}</pre>
        </>
      ) : (
        <>
          <header className="toolbar">
            <div className="summary">
              {board.pendingApproval.length > 0 ? <span className="pending warn">承認待ち {board.pendingApproval.length} 件</span> : null}
              {board.issueCount > 0 ? <span className="issues warn">不備 {board.issueCount} 件</span> : null}
              <span className="counts">
                残り {board.remainingCount} / 全 {board.totalCount}
              </span>
            </div>
            <div className="controls">
              {board.projects.length > 0 ? (
                <label className="filter">
                  プロジェクト
                  <select id="project-filter" value={project} onChange={(event) => setView((now) => ({ ...now, project: event.target.value }))}>
                    <option value="*">すべて</option>
                    <option value="">ワークスペース自身</option>
                    {board.projects.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              {board.parents.length > 0 ? (
                <label className="filter">
                  親
                  <select id="parent-filter" value={parent} onChange={(event) => setView((now) => ({ ...now, parent: event.target.value }))}>
                    <option value="*">すべて</option>
                    {board.parents.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.title === "" ? p.id : `${p.id} ${p.title}`}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <label className="filter attention" title="人が動く必要があるカードだけを出す（承認待ち・レビュー準備中／レビュー待ち・ワークツリーなし・HIGH 以上のリスク・不備）">
                <input type="checkbox" id="attention-filter" checked={attention} onChange={(event) => setView((now) => ({ ...now, attention: event.target.checked }))} /> 要対応だけ
              </label>
              <button
                type="button"
                className={refreshing ? "action busy" : "action"}
                data-action="refresh"
                disabled={refreshing}
                aria-busy={refreshing ? "true" : undefined}
                onClick={() => {
                  setRefreshing(true);
                  post({ type: "refresh" });
                }}
              >
                <span className="spin" aria-hidden="true" />
                <span className="label">{refreshing ? "更新中" : "更新"}</span>
              </button>
              <button
                type="button"
                className="action primary"
                data-action="approve"
                disabled={visiblePending.length === 0}
                onClick={() => post({ type: "approve", tickets: visiblePending, filtered: filtering })}
              >
                承認待ち {visiblePending.length} 件を承認
              </button>
            </div>
          </header>
          {board.problems.length > 0 ? (
            <ul className="problems">
              {board.problems.map((problem, i) => (
                <li key={i}>{problem}</li>
              ))}
            </ul>
          ) : null}
          {board.totalCount === 0 ? <p className="board-empty">チケット無し</p> : null}
          <div className="board">
            {board.columns.map((column) => (
              <Column
                key={column.state}
                column={column}
                hiddenOf={hiddenOf}
                folded={view.folded.includes(column.state)}
                width={view.widths[column.state]}
                onFold={(folded) =>
                  setView((now) => ({
                    ...now,
                    folded: folded ? [...now.folded, column.state] : now.folded.filter((f) => f !== column.state),
                  }))
                }
                onWidth={(width) =>
                  setView((now) => {
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
          <Footer board={board} />
        </>
      )}
      {data.approval !== undefined ? <Approval overlay={data.approval} /> : null}
    </>
  );
}

function Footer({ board }: { readonly board: Board }): JSX.Element {
  return (
    <footer className="foot">
      取得 {board.generatedAt} / {board.root}
    </footer>
  );
}

/**
 * 1 つの列。見出しを押すと畳み、右端の取っ手をドラッグすると幅が px で固定される
 * （ダブルクリックで元の伸び縮みに戻る）。件数は絞り込みで見えているカードの数。
 * 上部の集計（残り・全・不備・承認待ち）は絞り込みに関係なくボード全体の数のまま。
 */
function Column({
  column,
  hiddenOf,
  folded,
  width,
  onFold,
  onWidth,
}: {
  readonly column: BoardColumn;
  readonly hiddenOf: (card: Card) => boolean;
  readonly folded: boolean;
  readonly width: number | undefined;
  readonly onFold: (folded: boolean) => void;
  readonly onWidth: (width: number | undefined) => void;
}): JSX.Element {
  const section = useRef<HTMLElement>(null);
  const visible = column.cards.filter((card) => !hiddenOf(card)).length;
  // ドラッグの最中に列が消えたら（読み直せずエラーの画面に替わる）`pointerup` を受ける相手が居なくなり、
  // 後片付けが走らない。body に付けた印を残すと、カーソルが変わったまま文字も選べなくなる
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
    <section className={classes.join(" ")} data-state={column.state} style={width === undefined ? undefined : { width: `${width}px` }} ref={section}>
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
        <p className="empty">チケット無し</p>
      ) : (
        <ul className="cards">
          {column.cards.map((card) => (
            <CardItem key={card.id} card={card} hidden={hiddenOf(card)} />
          ))}
        </ul>
      )}
      <div className="resizer" data-resize={column.state} title="ドラッグで幅を変える／ダブルクリックで戻す" onPointerDown={onPointerDown} onDoubleClick={() => onWidth(undefined)} />
    </section>
  );
}
