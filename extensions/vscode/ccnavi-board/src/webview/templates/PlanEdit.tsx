/**
 * 編集欄の計画 1 つ（全体計画かフィードバック計画）。上に線を引ける図、下にステップの表（種類・レビューを選び、並べ替え・削除・追加）。
 *
 * 図に描く中身（番号・始まるステップ・終端にヒットしないステップ・順序の error）は実行ファイルが読んで返した答え（`plan`）のまま描き、
 * ここは判定しない。**図が古い間は（編集した直後で、まだ読ませた答えが戻っていない間）線を引けない**。古い図の番号で線を引くと、
 * 別のステップを指してしまうため。
 */
import { useState, type JSX } from "react";

import type { ApprovePlan } from "../../core/approvemodel.js";
import {
  addItem,
  canMove,
  connectItems,
  disconnectItems,
  ITEM_REVIEWS,
  moveItem,
  removeItem,
  setItem,
  type EditTemplate,
  type PlanPart,
} from "../../core/template-edit.js";
import type { PhaseTypeInfo } from "../../core/templates-view.js";
import { PlanGraph } from "../shared/PlanGraph.js";

const PART_TITLE: Readonly<Record<PlanPart, string>> = { plan: "全体計画", feedback: "フィードバック計画" };

/** ステップの `review` の言葉 */
const REVIEW_LABEL: Readonly<Record<string, string>> = {
  "": "定義のまま",
  mr: "mr（MR を見る）",
  defer: "defer（後ろのステップへ延ばす）",
};

export interface PlanEditProps {
  readonly part: PlanPart;
  readonly template: EditTemplate;
  /** 実行ファイルが返した、この計画の図の中身。まだ無ければ undefined */
  readonly plan: ApprovePlan | undefined;
  /** 図の中身が、いまの編集の中身を読ませたものか */
  readonly fresh: boolean;
  readonly types: readonly PhaseTypeInfo[];
  readonly onChange: (next: EditTemplate, typing?: string) => void;
}

/** この計画で選べる型。全体計画は `work`、フィードバック計画は `feedback`。いま使っている型が定義に無ければそれも挙げる */
function optionsFor(part: PlanPart, types: readonly PhaseTypeInfo[], current: string): readonly { readonly id: string; readonly label: string }[] {
  const kind = part === "plan" ? "work" : "feedback";
  const options = types.filter((t) => t.kind === kind).map((t) => ({ id: t.id, label: t.title === "" || t.title === t.id ? t.id : `${t.id}（${t.title}）` }));
  if (current !== "" && !options.some((o) => o.id === current)) {
    options.push({ id: current, label: `${current}（定義なし）` });
  }
  return options;
}

export function PlanEdit({ part, template, plan, fresh, types, onChange }: PlanEditProps): JSX.Element {
  const items = template[part];
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
          title={first === undefined ? "この層で指定できる型（phases.yml の定義）が見つかりません" : undefined}
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
      {plan !== undefined && plan.items.length === items.length ? (
        <>
          <PlanGraph
            plan={plan}
            editable={fresh}
            onConnect={(from, to) => onChange(connectItems(template, from, to))}
            onDisconnect={(from, to) => onChange(disconnectItems(template, from, to))}
            fitKey={`${part}:${items.length}`}
          />
          {!fresh && <p className="hint">実行ファイルで読んでいます。終わるまで線は引けません。</p>}
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
            return (
              <tr key={index} data-index={index}>
                <td className="mono">{offset + index}</td>
                <td>
                  <select
                    className="f-item-type"
                    aria-label={`${offset + index} 番の種類`}
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
                    aria-label={`${offset + index} 番のレビュー`}
                    value={item.review}
                    onChange={(event) => onChange(setItem(template, part, index, { review: event.target.value }))}
                  >
                    {ITEM_REVIEWS.map((r) => (
                      <option key={r} value={r}>
                        {REVIEW_LABEL[r]}
                      </option>
                    ))}
                    {!ITEM_REVIEWS.some((r) => r === item.review) && <option value={item.review}>{item.review}（未知）</option>}
                  </select>
                </td>
                <td className="mono">{item.after.length === 0 ? "なし" : item.after.map((n) => offset + n).join(", ")}</td>
                <td className="tpl-edit-ops">
                  <button type="button" className="action small" data-action="move-up" aria-label={`${offset + index} 番を前へ`} disabled={!canMove(template, part, index, -1)} onClick={() => onChange(moveItem(template, part, index, -1))}>
                    ↑
                  </button>
                  <button type="button" className="action small" data-action="move-down" aria-label={`${offset + index} 番を後ろへ`} disabled={!canMove(template, part, index, 1)} onClick={() => onChange(moveItem(template, part, index, 1))}>
                    ↓
                  </button>
                  <button
                    type="button"
                    className="action small"
                    data-action="remove-item"
                    aria-label={`${offset + index} 番を削除`}
                    disabled={part === "plan" && items.length <= 1}
                    title={part === "plan" && items.length <= 1 ? "全体計画の最後の 1 ステップは消せません" : undefined}
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
