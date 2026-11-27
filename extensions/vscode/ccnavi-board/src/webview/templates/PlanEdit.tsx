/**
 * 編集欄の計画1つ（全体計画かフィードバック計画）。上に線を引ける図と詳細の欄、下にステップの表（種類・レビューを選び、並べ替え・削除・追加）。
 *
 * 図に描く中身（番号・始まるステップ・終端に届かないステップ・順序のerror）は実行ファイルが読んで返した答え（`plan`）のまま描き、
 * ここは判定しない。
 *
 * **図が古い間も線を引ける。** 直した直後は、まだ読ませた答えが戻っていない（図は少し前の中身のまま）。図の番号は読ませたときの
 * 中身の添字なので、そのときのステップのid（`shownIds`）に置き換えてから、いまの中身の添字へ引き直す（`numberOfId`）。
 * 番号が振り直っても、別のステップを指さない。
 *
 * **点の置き場所は動かさない。** 線を引いて番号が振り直っても、点はその場に残り、表示する番号だけが変わる。置き場所はステップの
 * idで覚え（`PlanGraph`の`layout`。図の`from`をidに置き換えて渡す）、新しいステップだけ組んだ場所に置く。「並べ直す」で今の線から組み直す。
 *
 * **選んだステップ**（`picked`。ステップのid）は編集欄が持ち、図の点と表の行の両方で目立たせる。図の右の詳細の欄に、実行ファイルの
 * 答えを読むだけで出し（`PlanGraph`の`detail`）、その下に、いまの中身のそのステップを直す操作（種類・レビュー・削除・挟む・並べて足す・
 * 子のフロー）を置く。並べ替えは表でだけ行う（図の上の位置と番号は別のもの）。
 */
import { useMemo, useRef, useState, type JSX } from "react";

import type { ApprovePlan } from "../../core/approvemodel.js";
import { keepLayout, layoutOf, nodeId, planGraphOf, type PlanLayout } from "../../core/plan-graph.js";
import {
  addItem,
  canMove,
  connectItems,
  disconnectItems,
  insertItem,
  ITEM_MODES,
  modeOf,
  type InsertMode,
  type ItemMode,
  moveItem,
  numberOfId,
  removeItem,
  setItem,
  type EditTemplate,
  type PlanPart,
} from "../../core/template-edit.js";
import type { PhaseTypeInfo } from "../../core/templates-view.js";
import { PlanGraph } from "../shared/PlanGraph.js";

const PART_TITLE: Readonly<Record<PlanPart, string>> = { plan: "全体計画", feedback: "フィードバック計画" };

/** ステップのレビューの指定の言葉 */
const MODE_LABEL: Readonly<Record<ItemMode, string>> = {
  "": "定義のまま",
  review: "review: true（MRを見る）",
  "no-review": "review: false（MRを見ない）",
  deferred: "deferred: true（後ろのステップへ延ばす）",
};

export interface PlanEditProps {
  readonly part: PlanPart;
  readonly template: EditTemplate;
  /** 実行ファイルが返した、この計画の図の中身。まだ無ければundefined */
  readonly plan: ApprovePlan | undefined;
  /** 図の中身が、いまの編集の中身を読ませたものか */
  readonly fresh: boolean;
  /** 図の中身を読ませたときの、この計画のステップのid（添字の順） */
  readonly shownIds: readonly number[];
  readonly types: readonly PhaseTypeInfo[];
  /** 選んだステップのid。どちらの計画のものでもよい（この計画に無ければ、この計画では何も選んでいない） */
  readonly picked: number | undefined;
  readonly onPick: (id: number | undefined) => void;
  readonly onChange: (next: EditTemplate, typing?: string) => void;
  /** 全体計画の`index`番のステップに子のフローを足す */
  readonly onAddFlow: (index: number) => void;
  /** 全体計画の`index`番のステップの子のフローを開き、画面をそこまで送る */
  readonly onGoFlow: (index: number) => void;
}

/** この計画で選べる型。全体計画は`work`、フィードバック計画は`feedback`。いま使っている型が定義に無ければそれも挙げる */
function optionsFor(part: PlanPart, types: readonly PhaseTypeInfo[], current: string): readonly { readonly id: string; readonly label: string }[] {
  const kind = part === "plan" ? "work" : "feedback";
  const options = types.filter((t) => t.kind === kind).map((t) => ({ id: t.id, label: t.title === "" || t.title === t.id ? t.id : `${t.id}（${t.title}）` }));
  if (current !== "" && !options.some((o) => o.id === current)) {
    options.push({ id: current, label: `${current}（定義なし）` });
  }
  return options;
}

/** 図の`from`を、読ませたときのステップのidに置き換える。番号（`number`）と描く中身は実行ファイルの答えのまま */
function withIds(plan: ApprovePlan, ids: readonly number[]): ApprovePlan {
  const order = [...plan.items].map((item) => item.number).sort((a, b) => a - b);
  return { ...plan, items: plan.items.map((item) => ({ ...item, from: ids[order.indexOf(item.number)] ?? item.from })) };
}

/** 削除ボタンのtitle。消せない理由か、子のフローも消えること */
function removeTitle(last: boolean, hasFlow: boolean): string | undefined {
  if (last) {
    return "全体計画の最後の1ステップは消せません";
  }
  return hasFlow ? "このステップの子のフローも消えます（元に戻すで戻せます）" : undefined;
}

export function PlanEdit({ part, template, plan, fresh, shownIds, types, picked, onPick, onChange, onAddFlow, onGoFlow }: PlanEditProps): JSX.Element {
  const items = template[part];
  const shown = useMemo(() => (plan === undefined ? undefined : withIds(plan, shownIds)), [plan, shownIds]);
  // 置き場所は描くたびに覚え直す（次の答えで、残ったステップをその場所に置くため）。「並べ直す」で忘れる
  const kept = useRef<PlanLayout | undefined>(undefined);
  const [tidy, setTidy] = useState(0);
  const layout = useMemo(() => {
    if (shown === undefined) {
      return undefined;
    }
    kept.current = keepLayout(layoutOf(planGraphOf(shown)), kept.current);
    return kept.current;
    // tidyは「並べ直す」で組み直す合図
  }, [shown, tidy]);
  const relayout = (): void => {
    kept.current = undefined;
    setTidy((n) => n + 1);
  };
  /** 図の番号（読ませたときの添字）を、いまの中身の図の番号に引き直す。消えたステップならundefined */
  const nowNumber = (shownNumber: number): number | undefined => {
    const id = shown?.items.find((item) => item.number === shownNumber)?.from;
    return id === undefined ? undefined : numberOfId(template, id);
  };
  const link = (edit: typeof connectItems) => (from: number, to: number) => {
    const a = nowNumber(from);
    const b = nowNumber(to);
    if (a !== undefined && b !== undefined) {
      onChange(edit(template, a, b));
    }
  };
  // 選んだステップ。この計画の添字と、図の点のid（読ませたときの番号の点。まだ図に無ければundefined）
  const pickedIndex = picked === undefined ? -1 : items.findIndex((item) => item.id === picked);
  const pickedNode = useMemo(() => {
    const number = picked === undefined ? undefined : shown?.items.find((item) => item.from === picked)?.number;
    return number === undefined ? undefined : nodeId(number);
  }, [picked, shown]);
  const pickNode = (id: string | undefined): void => {
    const number = id === undefined ? undefined : shown?.items.find((item) => nodeId(item.number) === id)?.from;
    onPick(number);
  };

  const title = PART_TITLE[part];
  const first = optionsFor(part, types, "")[0]?.id;
  const offset = part === "plan" ? 0 : template.plan.length;
  if (items.length === 0) {
    return (
      <section className="tpl-edit-plan" data-part={part}>
        <h3>{title}</h3>
        <p className="hint">{part === "feedback" ? "フィードバック計画はありません（省いてよい欄です）。" : "計画がありません。"}</p>
        <button
          type="button"
          className="action small"
          data-action="add-item"
          disabled={first === undefined}
          title={first === undefined ? "このレイヤーで指定できる型（phases.ymlの定義）が見つかりません" : undefined}
          onClick={() => first !== undefined && onChange(addItem(template, part, first))}
        >
          {part === "feedback" ? "フィードバック計画を作る" : "ステップを足す"}
        </button>
      </section>
    );
  }
  const footer =
    pickedIndex < 0 ? undefined : (
      <StepActions
        part={part}
        template={template}
        index={pickedIndex + 1}
        number={offset + pickedIndex + 1}
        types={types}
        onChange={onChange}
        onPick={onPick}
        onAddFlow={onAddFlow}
        onGoFlow={onGoFlow}
      />
    );
  return (
    <section className="tpl-edit-plan" data-part={part}>
      <h3>{title}</h3>
      {shown !== undefined ? (
        <>
          <PlanGraph
            plan={shown}
            editable
            detail
            proposalNumbers={false}
            picked={pickedNode}
            onPick={pickNode}
            detailFooter={footer}
            onConnect={link(connectItems)}
            onDisconnect={link(disconnectItems)}
            layout={layout}
            fitKey={`${part}:${items.length}:${tidy}`}
          />
          <p className="tpl-edit-graph-foot">
            <button type="button" className="action small" data-action="relayout" title="いまの線で点の置き場所を並べ直します（番号は変わりません）" onClick={relayout}>
              並べ直す
            </button>{" "}
            {!fresh && (
              <span className="hint" data-role="graph-stale">
                実行ファイルで読んでいます。図は読み終えると新しくなります（その間も線は引けます）。
              </span>
            )}
          </p>
        </>
      ) : (
        <>
          <p className="hint">図は、実行ファイルが読み終えると出ます。</p>
          {footer !== undefined && <div className="plan-detail tpl-edit-detail-alone">{footer}</div>}
        </>
      )}
      <table className="tpl-plan tpl-edit-items">
        <caption className="sr-only">{title}のステップ</caption>
        <thead>
          <tr>
            <th scope="col">番号</th>
            <th scope="col">種類</th>
            <th scope="col">レビュー</th>
            <th scope="col">先行ステップ</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => {
            const index = i + 1;
            const number = offset + index;
            const hasFlow = part === "plan" && template.flows[String(index)] !== undefined;
            const last = part === "plan" && items.length <= 1;
            const chosen = item.id === picked;
            return (
              <tr key={item.id} data-index={index} data-picked={chosen ? "1" : "0"}>
                <td className="mono">
                  <button
                    type="button"
                    className="link tpl-edit-pick"
                    data-action="pick-step"
                    aria-pressed={chosen}
                    title={chosen ? "選ぶのをやめる" : "このステップを選ぶ（図の右の詳細の欄で直せます）"}
                    onClick={() => onPick(chosen ? undefined : item.id)}
                  >
                    {number}
                  </button>
                </td>
                <td>
                  <TypeSelect className="f-item-type" part={part} template={template} index={index} number={number} types={types} onChange={onChange} />
                </td>
                <td>
                  <ReviewSelect className="f-item-review" part={part} template={template} index={index} number={number} onChange={onChange} />
                </td>
                <td className="mono">{item.after.length === 0 ? "なし" : item.after.map((n) => offset + n).join(", ")}</td>
                <td className="tpl-edit-ops">
                  <button type="button" className="action small" data-action="move-up" aria-label={`${number}番を前へ`} disabled={!canMove(template, part, index, -1)} onClick={() => onChange(moveItem(template, part, index, -1))}>
                    ↑
                  </button>
                  <button type="button" className="action small" data-action="move-down" aria-label={`${number}番を後ろへ`} disabled={!canMove(template, part, index, 1)} onClick={() => onChange(moveItem(template, part, index, 1))}>
                    ↓
                  </button>
                  <button
                    type="button"
                    className="action small"
                    data-action="remove-item"
                    aria-label={`${number}番を削除`}
                    disabled={last}
                    title={removeTitle(last, hasFlow)}
                    onClick={() => onChange(removeItem(template, part, index))}
                  >
                    削除
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <AddItem part={part} template={template} types={types} onChange={onChange} />
    </section>
  );
}

interface StepProps {
  readonly part: PlanPart;
  readonly template: EditTemplate;
  /** 計画の中の添字（1始まり） */
  readonly index: number;
  /** 図の番号（フィードバック計画は全体計画の続き） */
  readonly number: number;
  readonly onChange: (next: EditTemplate, typing?: string) => void;
}

/** ステップの種類の選択。表と詳細の欄で同じものを使う */
function TypeSelect({ id, className, part, template, index, number, types, onChange }: StepProps & { readonly id?: string; readonly className: string; readonly types: readonly PhaseTypeInfo[] }): JSX.Element {
  const item = template[part][index - 1];
  return (
    <select id={id} className={className} aria-label={`${number}番の種類`} value={item.type} onChange={(event) => onChange(setItem(template, part, index, { type: event.target.value }))}>
      {optionsFor(part, types, item.type).map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/** ステップのレビューの指定の選択。表と詳細の欄で同じものを使う */
function ReviewSelect({ id, className, part, template, index, number, onChange }: StepProps & { readonly id?: string; readonly className: string }): JSX.Element {
  return (
    <select
      id={id}
      className={className}
      aria-label={`${number}番のレビュー`}
      value={modeOf(template[part][index - 1])}
      onChange={(event) => onChange(setItem(template, part, index, { mode: event.target.value as ItemMode }))}
    >
      {ITEM_MODES.map((mode) => (
        <option key={mode} value={mode}>
          {MODE_LABEL[mode]}
        </option>
      ))}
    </select>
  );
}

/**
 * 詳細の欄の下に置く、選んだステップの操作。種類・レビュー・削除・挟む・並べて足す・子のフロー（全体計画だけ）。
 * 中身はいまの編集の中身（図が古い間も、いまのステップを直す）
 */
function StepActions({
  part,
  template,
  index,
  number,
  types,
  onChange,
  onPick,
  onAddFlow,
  onGoFlow,
}: StepProps & Pick<PlanEditProps, "types" | "onPick" | "onAddFlow" | "onGoFlow">): JSX.Element {
  const options = optionsFor(part, types, "");
  const [chosen, setChosen] = useState("");
  const type = options.some((o) => o.id === chosen) ? chosen : (options[0]?.id ?? "");
  const items = template[part];
  const last = part === "plan" && items.length <= 1;
  const hasFlow = part === "plan" && template.flows[String(index)] !== undefined;
  const insert = (mode: InsertMode): void => {
    const added = insertItem(template, part, index, type, mode);
    if (added.id !== undefined) {
      onChange(added.template);
      onPick(added.id);
    }
  };
  const typeId = `step-type-${part}`;
  const reviewId = `step-review-${part}`;
  const insertId = `step-insert-${part}`;
  return (
    <div className="tpl-step-actions" data-role="step-actions" data-number={number}>
      <h4>{number}番を直す</h4>
      <label className="field" htmlFor={typeId}>
        <span className="dim">種類</span>
      </label>
      <TypeSelect id={typeId} className="f-step-type" part={part} template={template} index={index} number={number} types={types} onChange={onChange} />
      <label className="field" htmlFor={reviewId}>
        <span className="dim">レビュー</span>
      </label>
      <ReviewSelect id={reviewId} className="f-step-review" part={part} template={template} index={index} number={number} onChange={onChange} />
      <p className="tpl-step-row">
        <button
          type="button"
          className="action small"
          data-action="remove-step"
          disabled={last}
          title={removeTitle(last, hasFlow)}
          onClick={() => {
            onChange(removeItem(template, part, index));
            onPick(undefined);
          }}
        >
          このステップを削除
        </button>
      </p>
      {part === "plan" && (
        <p className="tpl-step-row">
          {hasFlow ? (
            <button type="button" className="action small" data-action="go-flow" onClick={() => onGoFlow(index)}>
              子のフローへ移る
            </button>
          ) : (
            <button type="button" className="action small" data-action="add-step-flow" onClick={() => onAddFlow(index)}>
              子のフローを足す
            </button>
          )}
        </p>
      )}
      <label className="field" htmlFor={insertId}>
        <span className="dim">足すステップの種類</span>
      </label>
      <select id={insertId} className="f-insert-type" value={type} disabled={options.length === 0} onChange={(event) => setChosen(event.target.value)}>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
      <p className="tpl-step-row">
        <button type="button" className="action small" data-action="insert-after" disabled={type === ""} title="このステップのすぐ後に挟みます（後ろのステップは新しいステップを待ちます）" onClick={() => insert("after")}>
          この後に挟む
        </button>{" "}
        <button type="button" className="action small" data-action="insert-beside" disabled={type === ""} title="このステップと並行に走り、同じ所で合流するステップを足します" onClick={() => insert("beside")}>
          これと並べて足す
        </button>
      </p>
    </div>
  );
}

/** 末尾にステップを足す。足したステップには前の末尾のステップが先行する（線は図で引き直せる） */
function AddItem({ part, template, types, onChange }: Pick<PlanEditProps, "part" | "template" | "types" | "onChange">): JSX.Element {
  const options = optionsFor(part, types, "");
  const [picked, setPicked] = useState("");
  const type = options.some((o) => o.id === picked) ? picked : (options[0]?.id ?? "");
  const id = `add-${part}`;
  return (
    <p className="tpl-edit-add">
      <label htmlFor={id} className="dim">
        足すステップの種類
      </label>{" "}
      <select id={id} className="f-add-type" value={type} disabled={options.length === 0} onChange={(event) => setPicked(event.target.value)}>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>{" "}
      <button type="button" className="action small" data-action="add-item" disabled={type === ""} onClick={() => onChange(addItem(template, part, type))}>
        ステップを足す
      </button>
    </p>
  );
}
