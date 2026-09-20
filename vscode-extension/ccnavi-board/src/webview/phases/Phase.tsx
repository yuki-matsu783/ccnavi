/**
 * 種類 1 件の行。畳んだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * 出番の少ない 4 欄（関係と案内）は見出し 1 行に畳み、値がある種類だけ最初から開く。
 */
import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";

import { KIND_LABELS, PHASE_KINDS, REVIEWS, REVIEW_LABELS, type PhaseForm, type PhaseKind, type Review } from "../../core/phases-view.js";
import { hasRelations, relationsNote, scopeText, scopeTitle, splitList } from "./text.js";

export interface PhaseProps {
  /** 画面の中だけの鍵（`state.ts`）。行を名指しするために `data-key` へ出す */
  readonly phaseKey: string;
  readonly phase: PhaseForm;
  readonly find: string;
  readonly hidden: boolean;
  readonly open: boolean;
  /** 「関係と案内」を開いているか。未指定なら値の有無で決める */
  readonly moreOpen?: boolean;
  /** id が他の種類と重なっている。保存は止まる */
  readonly duplicate: boolean;
  /** 欄を触れるか。保存の往復の間と、共通層でファイルが無い間は触れない */
  readonly disabled: boolean;
  readonly onToggle: () => void;
  readonly onToggleMore: (open: boolean) => void;
  readonly onChange: (next: PhaseForm) => void;
  readonly onMove: (delta: number) => void;
  readonly onRemove: () => void;
}

type TextKey = "id" | "title" | "agent" | "when";
type ListKey = "scope" | "deliverables" | "overlap" | "requires";

export function Phase(props: PhaseProps): JSX.Element {
  const { phase, disabled } = props;
  const text = (name: TextKey, className: string, placeholder: string): JSX.Element => (
    <input
      type="text"
      className={name === "id" && props.duplicate ? `${className} duplicate` : className}
      spellCheck={false}
      placeholder={placeholder}
      value={phase[name]}
      disabled={disabled}
      onChange={(event) => props.onChange({ ...phase, [name]: event.target.value })}
    />
  );
  const list = (name: ListKey, className: string, placeholder: string): JSX.Element => (
    <ListInput
      className={className}
      placeholder={placeholder}
      value={phase[name]}
      disabled={disabled}
      onChange={(next) => props.onChange({ ...phase, [name]: next })}
    />
  );

  return (
    <li className={`row phase ${phase.kind}${props.open ? " open" : ""}${props.hidden ? " hidden-by-find" : ""}`} data-key={props.phaseKey} data-find={props.find}>
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
        <button type="button" className="twist" title="この種類を開く／畳む" aria-expanded={props.open}>
          {props.open ? "▾" : "▸"}
        </button>
        <span className="sum">
          <span className={phase.id === "" ? "sum-id dim" : "sum-id"}>{phase.id === "" ? "（id 未設定）" : phase.id}</span>
          <span className="clip" title={phase.title}>
            {phase.title}
          </span>
          <span className={`tag ${phase.kind}`}>{phase.kind}</span>
          <span className="dim" title="review">
            レビュー {phase.review}
          </span>
          <span className="clip dim mono" title={scopeTitle(phase)}>
            {scopeText(phase)}
          </span>
        </span>
      </div>
      <div className="row-body">
        <Captioned name="id" yamlKey="id">
          {text("id", "f-id narrow", "implement（英数字で始まり、使えるのは英数字と . _ -）")}
        </Captioned>
        <Captioned name="題" yamlKey="title">
          {text("title", "f-title narrow", "実装とテスト（空なら id をそのまま使う）")}
        </Captioned>
        <Captioned name="区分" yamlKey="kind">
          <select
            className="f-kind"
            value={phase.kind}
            disabled={disabled}
            onChange={(event) => props.onChange({ ...phase, kind: event.target.value as PhaseKind })}
          >
            {PHASE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {KIND_LABELS[kind]}
              </option>
            ))}
          </select>
        </Captioned>
        <Captioned name="レビュー" yamlKey="review">
          <select
            className="f-review"
            value={phase.review}
            disabled={disabled}
            onChange={(event) => props.onChange({ ...phase, review: event.target.value as Review })}
          >
            {REVIEWS.map((review) => (
              <option key={review} value={review}>
                {REVIEW_LABELS[review]}
              </option>
            ))}
          </select>
        </Captioned>
        <Captioned name="範囲" yamlKey="scope">
          <div className="inline">
            <select
              className="f-scope"
              value={phase.inherit ? "inherit" : "globs"}
              disabled={disabled}
              onChange={(event) => props.onChange({ ...phase, inherit: event.target.value === "inherit" })}
            >
              <option value="inherit">inherit（親の範囲そのまま）</option>
              <option value="globs">上限を書く（glob の並び）</option>
            </select>
            {!phase.inherit && list("scope", "f-scope-globs", "src/*, tests/*（ワークツリーのルートからの相対。子チケットの範囲はこの中に収める）")}
          </div>
        </Captioned>
        <Captioned name="成果物" yamlKey="deliverables">
          {list("deliverables", "f-deliverables", "wip/design/*.md（閉じる前に存在し、git に追跡されているべきもの）")}
        </Captioned>
        <details
          className="more"
          open={props.moreOpen ?? hasRelations(phase)}
          onToggle={(event) => props.onToggleMore((event.currentTarget as HTMLDetailsElement).open)}
        >
          <summary>
            <b>関係と案内</b>
            {relationsNote(phase)}
          </summary>
          <div className="sub">
            <Captioned name="並行できる種類" yamlKey="overlap">
              {list("overlap", "f-overlap", "並行してよい種類の id")}
            </Captioned>
            <Captioned name="一緒に要る種類" yamlKey="requires">
              {list("requires", "f-requires", "計画に置くなら一緒に要る種類の id")}
            </Captioned>
            <Captioned name="エージェント" yamlKey="agent">
              {text("agent", "f-agent narrow", "案内に出すサブエージェントの名前")}
            </Captioned>
            <Captioned name="置く目安" yamlKey="when">
              {text("when", "f-when", "この種類を計画に置く目安。案内にだけ使う")}
            </Captioned>
          </div>
        </details>
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

/**
 * 並びの欄。1 つの欄に `,` 区切りで出し、打つたびに並びへ戻す。
 *
 * **打っている途中の文字は欄が持つ。** 並びに直したものをそのまま欄へ戻すと、`a, ` と打った
 * ところで `a` に縮む（区切りの直後が打てない）。外から中身が入れ替わったとき（再読込・保存）は、
 * 並びが打っている途中のものと違うので、そこで欄を入れ直す。
 */
function ListInput({
  value,
  className,
  placeholder,
  disabled,
  onChange,
}: {
  readonly value: readonly string[];
  readonly className: string;
  readonly placeholder: string;
  readonly disabled: boolean;
  readonly onChange: (next: readonly string[]) => void;
}): JSX.Element {
  const [text, setText] = useState(() => value.join(", "));
  const typed = useRef(text);
  typed.current = text;

  useEffect(() => {
    if (value.join("\n") !== splitList(typed.current).join("\n")) {
      setText(value.join(", "));
    }
  }, [value]);

  return (
    <input
      type="text"
      className={className}
      spellCheck={false}
      placeholder={placeholder}
      value={text}
      disabled={disabled}
      onChange={(event) => {
        setText(event.target.value);
        onChange(splitList(event.target.value));
      }}
    />
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
