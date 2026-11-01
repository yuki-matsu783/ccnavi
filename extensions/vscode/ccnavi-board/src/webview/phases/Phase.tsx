/**
 * 定義 1 件の行。折りたたんだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * 出番の少ない 2 欄（補足。agent / when）は見出し 1 行に折りたたみ、値がある定義だけ最初から開く。
 *
 * **ほかの定義との関係の欄（overlap / requires / after）は出さない。** 順序（どのフェーズにどのフェーズが先行するか）は
 * フェーズ定義ではなく親チケットの計画のステップの `after` で決める。
 */
import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";

import { KIND_LABELS, PHASE_KINDS, REVIEW_OPTIONS, type PhaseForm, type PhaseKind } from "../../core/phases-view.js";
import { moreNote, scopeText, scopeTitle, splitList } from "./text.js";
import { moveTitle } from "../shared/find.js";

export interface PhaseProps {
  /** 画面の中だけのキー（`state.ts`）。行を名指しするために `data-key` へ出す */
  readonly phaseKey: string;
  readonly phase: PhaseForm;
  readonly find: string;
  readonly hidden: boolean;
  readonly open: boolean;
  /**
   * 「補足」を開いているか。決めるのは呼ぶ側（行ごとに 1 度だけ値の有無で決め、あとは
   * ユーザの開閉で動く）。ここで値の有無から決め直すと、最後の値を消した時点で、打っている欄ごと折りたたまれる
   */
  readonly moreOpen: boolean;
  /** id が他の定義と重なっている。保存は止まる */
  readonly duplicate: boolean;
  /** 欄を触れるか。保存の往復の間は触れない */
  readonly disabled: boolean;
  readonly onToggle: () => void;
  readonly onToggleMore: (open: boolean) => void;
  readonly onChange: (next: PhaseForm) => void;
  /** 先頭の行は「上へ」、末尾の行は「下へ」を押せないようにする */
  readonly first: boolean;
  readonly last: boolean;
  /** 絞り込み中（検索語が空でない）。並べ替えを止める */
  readonly finding: boolean;
  readonly onMove: (delta: number) => void;
  readonly onRemove: () => void;
  /** 案内が指す行か。真のとき、行と欄に `data-tour` の印を付ける（印は画面に 1 つだけ。見本の最初の行にだけ付ける） */
  readonly tour?: boolean;
  /** 案内の見本の行。打っても欄の文字が変わらず、「補足」の開閉も動かない */
  readonly frozen?: boolean;
}

type TextKey = "id" | "title" | "agent" | "when";
type ListKey = "scope" | "deliverables";

export function Phase(props: PhaseProps): JSX.Element {
  const { phase, disabled } = props;
  /** 案内が指す行にだけ印を付ける */
  const mark = (name: string): string | undefined => (props.tour === true ? name : undefined);
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
      frozen={props.frozen === true}
      onChange={(next) => props.onChange({ ...phase, [name]: next })}
    />
  );

  return (
    <li className={`row phase ${phase.kind}${props.open ? " open" : ""}${props.hidden ? " hidden-by-find" : ""}`} data-key={props.phaseKey} data-find={props.find}>
      <div
        className="row-head"
        data-tour={mark("phase-row")}
        onClick={() => {
          // 文字を選んだだけのときは開閉しない（要約をコピーする操作を妨げない）
          if (window.getSelection !== undefined && String(window.getSelection()) !== "") {
            return;
          }
          props.onToggle();
        }}
      >
        <button type="button" className="twist" title="この定義を開く／畳む" aria-expanded={props.open}>
          {props.open ? "▾" : "▸"}
        </button>
        <span className="sum">
          <span className={phase.id === "" ? "sum-id dim" : "sum-id"} title={phase.id === "" ? "（id 未設定）" : phase.id}>
            {phase.id === "" ? "（id 未設定）" : phase.id}
          </span>
          <span className="clip" title={phase.title}>
            {phase.title}
          </span>
          <span className={`tag ${phase.kind}`}>{phase.kind}</span>
          <span className="dim" title="review">
            レビュー {String(phase.review)}
          </span>
          <span className="clip dim mono" title={scopeTitle(phase)}>
            {scopeText(phase)}
          </span>
        </span>
      </div>
      <div className="row-body">
        <Captioned name="id" yamlKey="id" tour={mark("phase-id")}>
          {text("id", "f-id narrow", "implement")}
          <p className="hint">英数字で始まり、使えるのは英数字と . _ - です。</p>
        </Captioned>
        <Captioned name="タイトル" yamlKey="title" tour={mark("phase-title")}>
          {text("title", "f-title narrow", "実装とテスト")}
          <p className="hint">空なら id をそのまま使います。</p>
        </Captioned>
        <Captioned name="区分" yamlKey="kind" tour={mark("phase-kind")}>
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
        <Captioned name="レビュー" yamlKey="review" tour={mark("phase-review")}>
          <select
            className="f-review"
            value={String(phase.review)}
            disabled={disabled}
            onChange={(event) => props.onChange({ ...phase, review: event.target.value === "true" })}
          >
            {REVIEW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Captioned>
        <Captioned name="編集可能な範囲" yamlKey="scope" tour={mark("phase-scope")}>
          <div className="inline">
            <select
              className="f-scope"
              value={phase.inherit ? "inherit" : "globs"}
              disabled={disabled}
              onChange={(event) => props.onChange({ ...phase, inherit: event.target.value === "inherit" })}
            >
              <option value="inherit">inherit（親チケットの編集可能範囲を引き継ぐ）</option>
              <option value="globs">上限を指定する（glob のリスト）</option>
            </select>
            {!phase.inherit && list("scope", "f-scope-globs", "src/*, tests/*。ワークツリーのルートからの相対で、子チケットの編集可能な範囲はこの中に収めます。区切りは半角 ,")}
          </div>
        </Captioned>
        <Captioned name="必須成果物" yamlKey="deliverables" tour={mark("phase-deliverables")}>
          {list("deliverables", "f-deliverables", "wip/design/*.md。閉じる前に存在し、git に追跡されている必要があります。区切りは半角 ,")}
        </Captioned>
        <details
          className="more"
          data-tour={mark("phase-more")}
          open={props.moreOpen}
          onToggle={(event) => {
            const details = event.currentTarget as HTMLDetailsElement;
            if (props.frozen === true) {
              // 見本の補足は、押しても開閉しない（ブラウザが先に切り替えた分を戻す）
              if (details.open !== props.moreOpen) {
                details.open = props.moreOpen;
              }
              return;
            }
            props.onToggleMore(details.open);
          }}
        >
          <summary>
            <b>補足</b>
            {moreNote(phase)}
          </summary>
          <div className="sub">
            <Captioned name="案内するエージェント" yamlKey="agent" tour={mark("phase-agent")}>
              {text("agent", "f-agent narrow", "サブエージェント名（案内に出すだけで、割り当てはしません）")}
            </Captioned>
            <Captioned name="使う場面" yamlKey="when" tour={mark("phase-when")}>
              {text("when", "f-when", "この定義を計画に入れる場面（エージェントへの案内にだけ使います）")}
            </Captioned>
          </div>
        </details>
        <span className="buttons">
          <button type="button" className="action small" title={moveTitle("上へ", props.finding)} disabled={disabled || props.first || props.finding} onClick={() => props.onMove(-1)}>
            ↑
          </button>
          <button type="button" className="action small" title={moveTitle("下へ", props.finding)} disabled={disabled || props.last || props.finding} onClick={() => props.onMove(1)}>
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
 * リストの欄。1 つの欄に `,` 区切りで出し、打つたびにリストへ戻す。
 *
 * 打っている途中の文字は欄が持つ。 リストに直したものをそのまま欄へ戻すと、`a, ` と打った
 * ところで `a` に縮む（区切りの直後が打てない）。
 *
 * 外から中身が入れ替わったとき（再読み込み・保存）は、行のキーが配り直されてこの部品ごと作り直されるので、
 * 欄は新しい値で始まる。下の `useEffect` はその経路を通らない保険で、キーを保つ書き方に変えたときに
 * 欄が古いまま残らないように置いてある。
 */
function ListInput({
  value,
  className,
  placeholder,
  disabled,
  frozen,
  onChange,
}: {
  readonly value: readonly string[];
  readonly className: string;
  readonly placeholder: string;
  readonly disabled: boolean;
  /** 案内の見本。打っても欄の文字を変えない */
  readonly frozen: boolean;
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
        if (frozen) {
          return;
        }
        setText(event.target.value);
        onChange(splitList(event.target.value));
      }}
    />
  );
}

/** 欄名を左に置く。YAML のキー名は欄名のツールチップに載せる */
export function Captioned({ name, yamlKey, tour, children }: { readonly name: string; readonly yamlKey?: string; readonly tour?: string; readonly children: ReactNode }): JSX.Element {
  return (
    <div className="field" data-tour={tour}>
      <span className="cap" title={yamlKey === undefined ? undefined : `YAML のキー: ${yamlKey}`}>
        {name}
      </span>
      {children}
    </div>
  );
}
