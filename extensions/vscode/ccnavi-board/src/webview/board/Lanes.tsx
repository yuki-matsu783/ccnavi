/**
 * チケット管理画面の本体。列（承認待ち・進行中・完了・取り消し、アーカイブを表示すれば5つめ）を横に、親ごとの行を縦に並べる。
 * 親は必ず行の見出し（`CardItem`の`head`）に置き、列の中には子だけを置く。列の見出しの件数は子だけを数える。
 * 「フェーズを表示」で親の行を開くと、フェーズごとの状態と子の一覧を出す（順序は矢印で描かない）。順序は、開いた行の
 * 「計画の流れを見る」で、承認のオーバーレイと同じ図の部品を読むだけで開いて見せる（`PlanFlow.tsx`）。
 *
 * どの親の行に入るかは、カードの`family`（親なら自分、子なら親の識別子）とプロジェクトの組で決める。
 * 親のカードがチケット管理画面に無い子（親のチケットが読めない・別の場所にある）は、親の識別子だけを見出しにした行に入る。
 * 判定はしない。並べるカードと言葉は組み立て（core/board.ts）が決めた値のとおり。
 *
 * 列の幅は全部の行で揃える（CSSのgridの列）。見出しを押すと列を畳み、見出しの右端の取っ手をドラッグすると幅を固定する
 * （ダブルクリックで元の伸び縮みに戻る）。940px以下では親ごとの行を縦に積み、行の中では子を列の名前つきで並べる。
 */
import { Fragment, useEffect, useRef, type JSX } from "react";

import type { BoardColumn, Card } from "../../core/board.js";
import type { Moved } from "../../core/board-moved.js";
import { CardItem } from "./Card.js";
import { post } from "./post.js";

/** 列の最小の幅（px）。ドラッグでもこれより狭くしない */
export const MIN_WIDTH = 220;

/** 親ごとの行。`parent`は親のカード（チケット管理画面に無ければundefined） */
export interface Lane {
  readonly key: string;
  readonly project: string;
  readonly family: string;
  readonly parent: Card | undefined;
  readonly children: readonly Card[];
}

/** カードを親ごとの行に分ける。行の順は親の識別子の順（同じならプロジェクトの順）、行の中の子は列の中の順のまま */
export function lanesOf(columns: readonly BoardColumn[]): Lane[] {
  const lanes = new Map<string, { project: string; family: string; parent: Card | undefined; children: Card[] }>();
  for (const card of columns.flatMap((column) => column.cards)) {
    const key = laneKey(card);
    const lane = lanes.get(key) ?? { project: card.project, family: card.family, parent: undefined, children: [] };
    if (card.isParent) {
      lane.parent = card;
    } else {
      lane.children.push(card);
    }
    lanes.set(key, lane);
  }
  return [...lanes.entries()]
    .map(([key, lane]) => ({ key, ...lane }))
    .sort((a, b) => (a.family !== b.family ? (a.family < b.family ? -1 : 1) : a.project < b.project ? -1 : a.project > b.project ? 1 : 0));
}

/** 列のgridの幅。畳んだ列は見出しの幅、固定した列はpx、ほかは220px以上で空きを分け合う */
export function tracksOf(columns: readonly BoardColumn[], folded: readonly string[], widths: Readonly<Record<string, number>>): string {
  return columns
    .map((column) => (folded.includes(column.state) ? "max-content" : widths[column.state] !== undefined ? `${widths[column.state]}px` : `minmax(${MIN_WIDTH}px, 1fr)`))
    .join(" ");
}

export interface LaneBoardProps {
  readonly columns: readonly BoardColumn[];
  readonly folded: readonly string[];
  readonly widths: Readonly<Record<string, number>>;
  /** 開いた親の行（`laneKey`） */
  readonly opened: readonly string[];
  readonly hiddenOf: (card: Card) => boolean;
  readonly movedOf: (card: Card) => Moved | undefined;
  readonly selectedOf: (card: Card) => boolean;
  readonly onSelect: (card: Card) => void;
  /** 幅が狭いときだけ、選んだカードのすぐ下に置くパネル（それ以外はnull） */
  readonly panelOf: (card: Card) => JSX.Element | null;
  readonly onFold: (state: string, folded: boolean) => void;
  readonly onWidth: (state: string, width: number | undefined) => void;
  readonly onOpen: (lane: string, opened: boolean) => void;
  /** 開いた親の行の「計画の流れを見る」を押したとき */
  readonly onPlanFlow: (parent: Card) => void;
}

export function LaneBoard(props: LaneBoardProps): JSX.Element {
  const { columns, folded, widths, hiddenOf } = props;
  const board = useRef<HTMLDivElement>(null);
  const lanes = lanesOf(columns);
  // ドラッグの最中は、その列の幅だけを差し替えたgridをDOMに直に書く（離したときに覚える）
  const preview = (state: string, width: number): void => {
    if (board.current !== null) {
      board.current.style.gridTemplateColumns = tracksOf(columns, folded, { ...widths, [state]: width });
    }
  };
  return (
    <div className="board" data-tour="board" ref={board} style={{ gridTemplateColumns: tracksOf(columns, folded, widths) }}>
      {columns.map((column) => (
        <ColumnHead
          key={column.state}
          column={column}
          count={column.cards.filter((card) => !card.isParent && !hiddenOf(card)).length}
          folded={folded.includes(column.state)}
          sized={widths[column.state] !== undefined}
          onFold={(next) => props.onFold(column.state, next)}
          onWidth={(width) => props.onWidth(column.state, width)}
          onPreview={(width) => preview(column.state, width)}
        />
      ))}
      {lanes.map((lane) => (
        <LaneRow key={lane.key} lane={lane} {...props} />
      ))}
    </div>
  );
}

/** 親の行を開いた状態を覚えるキー（プロジェクトと親の識別子の組） */
export function laneKey(lane: { readonly project: string; readonly family: string }): string {
  return JSON.stringify([lane.project, lane.family]);
}

function LaneRow({ lane, columns, folded, opened, hiddenOf, movedOf, selectedOf, onSelect, panelOf, onOpen, onPlanFlow }:LaneBoardProps & { readonly lane: Lane }): JSX.Element {
  const parent = lane.parent;
  const open = parent !== undefined && parent.phases.length > 0 && opened.includes(lane.key);
  // 行は、親か子のどれかが見えている間は出す（子だけが絞り込みに残っても、見出しの親で分かるように）。
  // 隠れたカードもDOMには置き、`hidden`で隠す（列のときと同じ）
  const shown = (parent !== undefined && !hiddenOf(parent)) || lane.children.some((card) => !hiddenOf(card));
  return (
    <div className={shown ? "lane" : "lane hidden"} data-lane={lane.family} data-project={lane.project} role="group" aria-label={`親 ${lane.family}`}>
      {parent !== undefined ? (
        <>
          <CardItem card={parent} hidden={!shown} head moved={movedOf(parent)} selected={selectedOf(parent)} onSelect={() => onSelect(parent)}>
            {parent.workflow ? <WorkflowHeadButton id={parent.id} /> : null}
            {parent.phases.length > 0 ? (
              <button
                type="button"
                className="card-button"
                data-action="lane-phases"
                data-tour="lane-phases"
                aria-expanded={open ? "true" : "false"}
                onClick={() => onOpen(lane.key, !open)}
              >
                {open ? "フェーズを隠す" : "フェーズを表示"}
              </button>
            ) : null}
          </CardItem>
          {panelOf(parent)}
        </>
      ) : (
        <div className="lane-head lane-orphan" data-orphan={lane.family}>
          <span className="lane-orphan-id">親 {lane.family}</span>
          <span className="lane-orphan-note">親のチケットはチケット管理画面にありません</span>
        </div>
      )}
      {open && parent !== undefined ? <LanePhases parent={parent} childCards={lane.children} onPlanFlow={() => onPlanFlow(parent)} /> : null}
      {parent !== undefined && lane.children.length === 0 ? <p className="lane-empty">子はまだありません</p> : null}
      {columns.map((column) => {
        const own = lane.children.filter((card) => card.column === column.state);
        const visible = own.filter((card) => !hiddenOf(card)).length;
        const isFolded = folded.includes(column.state);
        // 畳んだ列のセルは件数だけを見せる（カードはDOMに置いたまま隠す。列のときと同じ）
        return (
          <div key={column.state} className={visible === 0 ? "cell empty-cell" : isFolded ? "cell folded-cell" : "cell"} data-cell={column.state}>
            {visible > 0 && isFolded ? (
              <span className="folded-count" title={`この親の${column.label}の子チケット ${visible}件（列を畳んでいます）`}>
                子{visible}
              </span>
            ) : null}
            {own.length > 0 ? (
              <>
                <div className="cell-label">
                  {column.label}
                  <span className="count" title={`この親の${column.label}の子チケット ${visible}件`}>子{visible}</span>
                </div>
                <ul className="cards">
                  {own.map((card) => (
                    <Fragment key={`${card.project}/${card.id}`}>
                      <CardItem card={card} hidden={hiddenOf(card)} moved={movedOf(card)} selected={selectedOf(card)} onSelect={() => onSelect(card)} />
                      {panelOf(card) === null ? null : <li className="card-panel-slot">{panelOf(card)}</li>}
                    </Fragment>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/**
 * 開いた親の行。フェーズごとに、状態の一言と、そのフェーズの子（識別子とバッジの言葉）を1行で並べる。
 * 計画のステップの先行関係は一直線とは限らないので、矢印で順序を描かない。順序は「計画の流れを見る」で図にして見せる
 * （図の中身`card.plans`が空の親では出さない）
 */
function LanePhases({ parent, childCards, onPlanFlow }: { readonly parent: Card; readonly childCards: readonly Card[]; readonly onPlanFlow: () => void }): JSX.Element {
  return (
    <div className="lane-phases" data-tour="lane-phase-list">
      {parent.plans.length > 0 ? (
        <div className="lane-phases-actions">
          <button type="button" className="card-button" data-action="plan-flow" data-tour="lane-plan-flow" data-ticket={parent.id} title="計画のステップの順序を図で見ます（見るだけで、直せません）" onClick={onPlanFlow}>
            計画の流れを見る
          </button>
        </div>
      ) : null}
      <ul className="phases">
        {parent.phases.map((p) => {
          const own = childCards.filter((card) => card.phase === p.number);
          return (
            <li key={p.number} className={`phase phase-${p.state}${p.status === "レビュー待ち" || p.status === "レビュー準備中" ? " review-hold" : ""}${p.status === "要確認" ? " phase-check" : ""}`} data-phase={p.number} data-status={p.status}>
              <span className="phase-dot" aria-hidden="true" />
              <span className="phase-name">
                <span className="phase-label">{p.label}</span>
                <span className="phase-state">{p.status}</span>
              </span>
              <span className="phase-children">{own.length === 0 ? "子なし" : own.map((card) => `${card.id} ${card.status.label}`).join("、")}</span>
              {p.manual !== "" ? <span className="phase-manual">{p.manual}</span> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** 承認待ちの親の「計画編集」。出すかは組み立て（`card.workflow`）が決め、押したら拡張ホストへ返すだけ */
function WorkflowHeadButton({ id }: { readonly id: string }): JSX.Element {
  return (
    <button
      type="button"
      className="card-button"
      data-action="workflow"
      data-ticket={id}
      title="計画編集タブを開きます。ステップの順序（どのステップが先行するか）を図で直せます。保存すると提案の内容が変わるので、承認画面を開き直してください。"
      onClick={() => post({ type: "workflow", ticket: id })}
    >
      計画編集
    </button>
  );
}

/**
 * 列の見出し。押すと畳み、右端の取っ手をドラッグすると幅がpxで固定される（ダブルクリックで元の伸び縮みに戻る）。
 * 件数は絞り込みで見えている子の数（親は数えない）
 */
function ColumnHead({
  column,
  count,
  folded,
  sized,
  onFold,
  onWidth,
  onPreview,
}: {
  readonly column: BoardColumn;
  readonly count: number;
  readonly folded: boolean;
  readonly sized: boolean;
  readonly onFold: (folded: boolean) => void;
  readonly onWidth: (width: number | undefined) => void;
  readonly onPreview: (width: number) => void;
}): JSX.Element {
  const head = useRef<HTMLDivElement>(null);
  // ドラッグの最中に見出しが消えたら（読み直せずエラーの画面に替わる）`pointerup`を受ける相手が居なくなり、
  // 後片付けが走らない。bodyに付けたクラスを残すと、カーソルが変わったまま文字も選べなくなる
  useEffect(() => () => document.body.classList.remove("resizing"), []);
  const classes = ["column"];
  if (sized) {
    classes.push("sized");
  }
  if (folded) {
    classes.push("folded");
  }

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0 || head.current === null) {
      return;
    }
    event.preventDefault();
    const handle = event.currentTarget;
    const element = head.current;
    const startX = event.clientX;
    // 画面を拡大している（bodyのzoom）と、矩形とポインタの座標は見た目のpx、幅はCSSのpxになる。その比で換算する
    const scale = Number.parseFloat(document.body.style.zoom) || 1;
    const startWidth = element.getBoundingClientRect().width / scale;
    let last = Math.max(MIN_WIDTH, Math.round(startWidth));
    // 動かさずに押して離しただけなら、幅は決めない。押しただけでpxに固定されると、その列は窓の幅に追従しなくなる
    let dragged = false;
    element.classList.add("resizing");
    document.body.classList.add("resizing");
    handle.setPointerCapture(event.pointerId);
    const move = (moved: PointerEvent): void => {
      last = Math.max(MIN_WIDTH, Math.round(startWidth + (moved.clientX - startX) / scale));
      dragged = true;
      onPreview(last);
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
    <div className={classes.join(" ")} data-state={column.state} data-tour={`column-${column.state}`} ref={head}>
      <h2>
        <button type="button" className="fold" data-fold={column.state} aria-expanded={folded ? "false" : "true"} title="列を折りたたむ／広げる" onClick={() => onFold(!folded)}>
          <span className="fold-mark" aria-hidden="true" />
          <span className="label">{column.label}</span>
        </button>
        <span className="count" title={`この列の子チケット ${count}件（親は数えません）`}>
          子{count}
        </span>
      </h2>
      {folded ? null : <div className="resizer" data-resize={column.state} title="ドラッグで幅を変える／ダブルクリックで戻す" onPointerDown={onPointerDown} onDoubleClick={() => onWidth(undefined)} />}
    </div>
  );
}
