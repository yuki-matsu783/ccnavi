/**
 * 編集欄の計画1つ（全体計画かフィードバック計画）。上に線を引ける図、下にステップの表（種類・レビューを選び、並べ替え・削除・追加）。
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
 */
import { useMemo, useRef, useState, type JSX } from "react";

import type { ApprovePlan } from "../../core/approvemodel.js";
import { keepLayout, layoutOf, planGraphOf, type PlanLayout } from "../../core/plan-graph.js";
import {
  addItem,
  canMove,
  connectItems,
  disconnectItems,
  ITEM_MODES,
  modeOf,
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
  readonly onChange: (next: EditTemplate, typing?: string) => void;
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

export function PlanEdit({ part, template, plan, fresh, shownIds, types, onChange }: PlanEditProps): JSX.Element {
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
  return (
    <section className="tpl-edit-plan" data-part={part}>
      <h3>{title}</h3>
      {shown !== undefined ? (
        <>
          <PlanGraph plan={shown} editable onConnect={link(connectItems)} onDisconnect={link(disconnectItems)} layout={layout} fitKey={`${part}:${items.length}:${tidy}`} />
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
        <p className="hint">図は、実行ファイルが読み終えると出ます。</p>
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
            const options = optionsFor(part, types, item.type);
            const hasFlow = part === "plan" && template.flows[String(index)] !== undefined;
            const last = part === "plan" && items.length <= 1;
            return (
              <tr key={index} data-index={index}>
                <td className="mono">{offset + index}</td>
                <td>
                  <select
                    className="f-item-type"
                    aria-label={`${offset + index}番の種類`}
                    value={item.type}
                    onChange={(event) => onChange(setItem(template, part, index, { type: event.target.value }))}
                  >
                    {options.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    className="f-item-review"
                    aria-label={`${offset + index}番のレビュー`}
                    value={modeOf(item)}
                    onChange={(event) => onChange(setItem(template, part, index, { mode: event.target.value as ItemMode }))}
                  >
                    {ITEM_MODES.map((mode) => (
                      <option key={mode} value={mode}>
                        {MODE_LABEL[mode]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="mono">{item.after.length === 0 ? "なし" : item.after.map((n) => offset + n).join(", ")}</td>
                <td className="tpl-edit-ops">
                  <button type="button" className="action small" data-action="move-up" aria-label={`${offset + index}番を前へ`} disabled={!canMove(template, part, index, -1)} onClick={() => onChange(moveItem(template, part, index, -1))}>
                    ↑
                  </button>
                  <button type="button" className="action small" data-action="move-down" aria-label={`${offset + index}番を後ろへ`} disabled={!canMove(template, part, index, 1)} onClick={() => onChange(moveItem(template, part, index, 1))}>
                    ↓
                  </button>
                  <button
                    type="button"
                    className="action small"
                    data-action="remove-item"
                    aria-label={`${offset + index}番を削除`}
                    disabled={last}
                    title={last ? "全体計画の最後の1ステップは消せません" : hasFlow ? "このステップの子のフローも消えます（元に戻すで戻せます）" : undefined}
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
