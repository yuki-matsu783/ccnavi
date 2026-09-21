/**
 * 項目 1 件の行。畳んだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * 値の欄は当て方で名前も placeholder も変わり、上限（`max`）は glob のときだけ出る。
 */
import type { JSX, ReactNode } from "react";

import { KINDS, KIND_LABELS, type FactorForm, type FactorKind } from "../../core/risk-view.js";
import { describe, kindTitle, summaryId, summaryPoints, valueLabel } from "./text.js";

/** 当て方の欄のツールチップに出すキーの一覧 */
const KINDS_KEYS = KINDS.join(" / ");

/** 数で答える当て方。欄に数字のキーボードを出す */
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
  readonly onToggle: () => void;
  readonly onChange: (next: FactorForm) => void;
  readonly onMove: (delta: number) => void;
  readonly onRemove: () => void;
}

export function Factor(props: FactorProps): JSX.Element {
  const { factor, disabled } = props;
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

  return (
    <li className={`row factor${props.open ? " open" : ""}${props.hidden ? " hidden-by-find" : ""}`} data-key={props.factorKey} data-find={props.find}>
      <div
        className="row-head"
        onClick={() => {
          // 文字を選んだだけのときは開閉しない（要約をコピーする操作を奪わない）
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
          <span className={factor.id === "" ? "sum-id dim" : "sum-id"}>{summaryId(factor)}</span>
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
        <Captioned name="id" yamlKey="id">
          {text("id", "f-id narrow", "big-diff")}
        </Captioned>
        <Captioned name="点" yamlKey="points">
          {text("points", "f-points num", "25", true)}
        </Captioned>
        <Captioned name="当て方" yamlKey={KINDS_KEYS}>
          <select
            className="f-kind"
            value={factor.kind}
            disabled={disabled}
            onChange={(event) => {
              const kind = event.target.value as FactorKind;
              // 値は当て方ごとに意味が違うので持ち越さない。max は glob だけの欄
              props.onChange({ ...factor, kind, value: "", max: kind === "glob" ? factor.max : "" });
            }}
          >
            {KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}（{KIND_LABELS[kind].label}）
              </option>
            ))}
          </select>
        </Captioned>
        <Captioned name={valueLabel(factor.kind)} yamlKey={factor.kind}>
          {factor.kind === "glob" ? (
            <div className="inline">
              {text("value", "f-value", KIND_LABELS[factor.kind].placeholder)}
              <span className="cap" title="YAML のキー: max">
                上限
              </span>
              {text("max", "f-max num", "上限。空なら上限なし")}
            </div>
          ) : (
            text("value", "f-value", KIND_LABELS[factor.kind].placeholder, NUMERIC.includes(factor.kind))
          )}
        </Captioned>
        <Captioned name="文面" yamlKey="message">
          {text("message", "f-message", "加点の理由として依頼文と閉じたときの出力に出る短い文。空なら id をそのまま使う")}
        </Captioned>
        <span className="buttons">
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
export function Captioned({ name, yamlKey, children }: { readonly name: string; readonly yamlKey?: string; readonly children: ReactNode }): JSX.Element {
  return (
    <div className="field">
      <span className="cap" title={yamlKey === undefined ? undefined : `YAML のキー: ${yamlKey}`}>
        {name}
      </span>
      {children}
    </div>
  );
}
