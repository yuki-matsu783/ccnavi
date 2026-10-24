/**
 * 項目 1 件の行。折りたたんだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * 値の欄は加点条件で名前も placeholder も変わり、上限（`max`）は glob のときだけ出る。
 */
import type { JSX, ReactNode } from "react";

import { KINDS, KIND_LABELS, type FactorForm, type FactorKind } from "../../core/risk-view.js";
import { describe, kindTitle, summaryId, summaryPoints, valueLabel } from "./text.js";

/** 加点条件の欄のツールチップに出すキーの一覧 */
const KINDS_KEYS = KINDS.join(" / ");

/** 数で答える加点条件。欄に数字のキーボードを出す */
const NUMERIC: readonly FactorKind[] = ["lines_over", "files_over", "deleted_over"];

export interface FactorProps {
  /** 画面の中だけの鍵（`state.ts`）。行を名指しするために `data-key` へ出す */
  readonly factorKey: string;
  readonly factor: FactorForm;
  /** 絞り込みが当てる文字列（`data-find`）。当たらなければ `hidden` */
  readonly find: string;
  readonly hidden: boolean;
  readonly open: boolean;
  /** 欄を触れるか。保存の往復の間と、ファイルが無い間は触れない */
  readonly disabled: boolean;
  /** 案内が指す行。中の部品に `data-tour` の印を付ける（行ごとに付けると、畳んだ行を指してしまう） */
  readonly tour?: boolean;
  readonly onToggle: () => void;
  readonly onChange: (next: FactorForm) => void;
  readonly onMove: (delta: number) => void;
  readonly onRemove: () => void;
}

export function Factor(props: FactorProps): JSX.Element {
  const { factor, disabled } = props;
  /** 案内の指す先の印。指される行だけに付ける */
  const mark = (name: string): { "data-tour"?: string } => (props.tour === true ? { "data-tour": name } : {});
  const text = (name: keyof FactorForm & ("id" | "points" | "value" | "max" | "message"), className: string, placeholder: string, numeric = false): JSX.Element => (
    <input
      type="text"
      className={className}
      spellCheck={false}
      placeholder={placeholder}
      inputMode={numeric ? "numeric" : undefined}
      value={factor[name]}
      disabled={disabled}
      onChange={(event) => props.onChange({ ...factor, [name]: event.target.value })}
    />
  );

  const globs = (name: "include" | "exclude", placeholder: string): JSX.Element => (
    <textarea
      className="f-value"
      rows={2}
      spellCheck={false}
      placeholder={placeholder}
      value={factor[name]}
      disabled={disabled}
      onChange={(event) => props.onChange({ ...factor, [name]: event.target.value })}
    />
  );

  return (
    <li className={`row factor${props.open ? " open" : ""}${props.hidden ? " hidden-by-find" : ""}`} data-key={props.factorKey} data-find={props.find} {...mark("factor-row")}>
      <div
        className="row-head"
        onClick={() => {
          // 文字を選んだだけのときは開閉しない（要約をコピーする操作を妨げない）
          if (window.getSelection !== undefined && String(window.getSelection()) !== "") {
            return;
          }
          props.onToggle();
        }}
      >
        <button type="button" className="twist" title="この項目を開く／畳む" aria-expanded={props.open}>
          {props.open ? "▾" : "▸"}
        </button>
        <span className="sum">
          <span className={factor.id === "" ? "sum-id dim" : "sum-id"} title={summaryId(factor)}>
            {summaryId(factor)}
          </span>
          <span className={factor.points === "" ? "sum-points dim" : "sum-points"} title="points">
            {summaryPoints(factor)}
          </span>
          <span className="clip" title={kindTitle(factor)}>
            {describe(factor).map((part, index) =>
              part.tone === "code" ? <code key={index}>{part.text}</code> : <span key={index} className="dim">{part.text}</span>,
            )}
            {factor.message !== "" && <span className="sum-note">{factor.message}</span>}
          </span>
        </span>
      </div>
      <div className="row-body">
        <Captioned name="id" yamlKey="id" {...mark("factor-id")}>
          {text("id", "f-id narrow", "big-diff")}
        </Captioned>
        <Captioned name="点" yamlKey="points" {...mark("factor-points")}>
          {text("points", "f-points num", "25", true)}
        </Captioned>
        <Captioned name="加点条件" yamlKey={KINDS_KEYS} {...mark("factor-kind")}>
          <select
            className="f-kind"
            value={factor.kind}
            disabled={disabled}
            onChange={(event) => {
              const kind = event.target.value as FactorKind;
              // 値は加点条件ごとに意味が違うので持ち越さない。max は glob だけの欄
              // include / exclude は数える加点条件（lines_over など）だけの欄
              const counting = NUMERIC.includes(kind);
              props.onChange({
                ...factor,
                kind,
                value: "",
                max: kind === "glob" ? factor.max : "",
                include: counting ? factor.include : "",
                exclude: counting ? factor.exclude : "",
              });
            }}
          >
            {KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}（{KIND_LABELS[kind].label}）
              </option>
            ))}
          </select>
        </Captioned>
        <Captioned name={valueLabel(factor.kind)} yamlKey={factor.kind} {...mark("factor-value")}>
          {factor.kind === "glob" ? (
            <div className="inline">
              {text("value", "f-value", KIND_LABELS[factor.kind].placeholder)}
              <span className="cap" title="YAML のキー: max">
                上限
              </span>
              {text("max", "f-max num", "上限。空なら上限なしです")}
            </div>
          ) : (
            text("value", "f-value", KIND_LABELS[factor.kind].placeholder, NUMERIC.includes(factor.kind))
          )}
        </Captioned>
        {NUMERIC.includes(factor.kind) && (
          <>
            <Captioned name="対象パス" yamlKey="include" {...mark("factor-include")}>
              {globs("include", "src/**（glob を 1 行に 1 つ。指定すると、当てはまるパスだけを数えます。空なら全パスです）")}
            </Captioned>
            <Captioned name="除外パス" yamlKey="exclude" {...mark("factor-exclude")}>
              {globs("exclude", "*.lock（glob を 1 行に 1 つ。当てはまるパスは数えません。対象パスに当てはまっていても除外します）")}
            </Captioned>
          </>
        )}
        <Captioned name="理由" yamlKey="message" {...mark("factor-message")}>
          {text("message", "f-message", "加点の理由として依頼文とクローズしたときの出力に出る短い文です。空なら id をそのまま使います")}
        </Captioned>
        <span className="buttons" {...mark("factor-buttons")}>
          <button type="button" className="action small" title="上へ" disabled={disabled} onClick={() => props.onMove(-1)}>
            ↑
          </button>
          <button type="button" className="action small" title="下へ" disabled={disabled} onClick={() => props.onMove(1)}>
            ↓
          </button>
          <button type="button" className="action small" disabled={disabled} onClick={() => props.onRemove()}>
            削除
          </button>
        </span>
      </div>
    </li>
  );
}

/** 欄名を左に置く。YAML のキー名は欄名のツールチップに載せる */
export function Captioned({
  name,
  yamlKey,
  children,
  "data-tour": tour,
}: {
  readonly name: string;
  readonly yamlKey?: string;
  readonly children: ReactNode;
  /** 案内の指す先の印 */
  readonly "data-tour"?: string;
}): JSX.Element {
  return (
    <div className="field" data-tour={tour}>
      <span className="cap" title={yamlKey === undefined ? undefined : `YAML のキー: ${yamlKey}`}>
        {name}
      </span>
      {children}
    </div>
  );
}
