/**
 * 種類 1 件の行。畳んだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * 出番の少ない 5 欄（ほかの種類との関係と補足）は見出し 1 行に畳み、値がある種類だけ最初から開く。
 *
 * 関係の 3 欄（overlap / requires / after）は、このファイルのほかの種類の id をチェックで選ぶ
 * （`IdPicker`）。自分の id は候補に出さない。ほかの層の種類を指すこともあるので、手で打つ欄も残す。
 */
import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";

import { KIND_LABELS, PHASE_KINDS, REVIEWS, REVIEW_LABELS, type PhaseForm, type PhaseKind, type Review } from "../../core/phases-view.js";
import { relationsNote, scopeText, scopeTitle, splitList } from "./text.js";

export interface PhaseProps {
  /** 画面の中だけの鍵（`state.ts`）。行を名指しするために `data-key` へ出す */
  readonly phaseKey: string;
  readonly phase: PhaseForm;
  readonly find: string;
  readonly hidden: boolean;
  readonly open: boolean;
  /**
   * 「ほかの種類との関係・補足」を開いているか。**決めるのは呼ぶ側**（行ごとに 1 度だけ値の有無で決め、あとは
   * 人の開閉で動く）。ここで値の有無から決め直すと、最後の値を消した瞬間に、打っている欄ごと畳まれる
   */
  readonly moreOpen: boolean;
  /** このファイルの種類の id（並び順）。関係の欄の候補にする */
  readonly ids: readonly string[];
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
type ListKey = "scope" | "deliverables";
type IdsKey = "overlap" | "requires" | "after";

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

  const ids = (name: IdsKey, className: string, title: string): JSX.Element => (
    <IdPicker
      className={className}
      title={title}
      self={phase.id.trim()}
      candidates={props.ids}
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
          open={props.moreOpen}
          onToggle={(event) => props.onToggleMore((event.currentTarget as HTMLDetailsElement).open)}
        >
          <summary>
            <b>ほかの種類との関係・補足</b>
            {relationsNote(phase)}
          </summary>
          <div className="sub">
            <Captioned name="並行できる種類" yamlKey="overlap">
              {ids("overlap", "f-overlap", "この種類と並行して進めてよい種類")}
            </Captioned>
            <Captioned name="一緒に必要な種類" yamlKey="requires">
              {ids("requires", "f-requires", "計画にこの種類を入れるなら、一緒に入れる必要がある種類")}
            </Captioned>
            <Captioned name="先に済ませる種類" yamlKey="after">
              {ids("after", "f-after", "待ち方が dag のとき、この種類より先に閉じてレビューを終えておく種類")}
            </Captioned>
            <Captioned name="担当エージェント" yamlKey="agent">
              {text("agent", "f-agent narrow", "サブエージェント名（案内に出す）")}
            </Captioned>
            <Captioned name="使う場面" yamlKey="when">
              {text("when", "f-when", "計画にこの種類を入れる目安（エージェントへの案内にだけ使う）")}
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
 * ところで `a` に縮む（区切りの直後が打てない）。
 *
 * 外から中身が入れ替わったとき（再読込・保存）は、行の鍵が配り直されてこの部品ごと作り直されるので、
 * 欄は新しい値で始まる。下の `useEffect` はその道を通らない保険で、鍵を保つ書き方に変えたときに
 * 欄が古いまま残らないように置いてある。
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

/**
 * 関係の欄。ほかの種類の id をチェックで選ぶ。
 *
 * 候補はこのファイルの種類の id から、自分と空を除いたもの。**候補に無い値も消さずに出す**
 * （ほかの層の種類や綴り違い）。外せば並びから消える。候補に無い id は下の欄に打って Enter で足す
 * （`,` 区切りで複数も可）。自分の id は打っても足さない。
 */
function IdPicker({
  value,
  self,
  candidates,
  className,
  title,
  disabled,
  onChange,
}: {
  readonly value: readonly string[];
  readonly self: string;
  readonly candidates: readonly string[];
  readonly className: string;
  readonly title: string;
  readonly disabled: boolean;
  readonly onChange: (next: readonly string[]) => void;
}): JSX.Element {
  const [extra, setExtra] = useState("");
  const known = new Set(candidates);
  const options = Array.from(new Set([...candidates.filter((id) => id !== "" && id !== self), ...value]));
  const toggle = (id: string, checked: boolean): void => {
    onChange(checked ? [...value, id] : value.filter((item) => item !== id));
  };
  const addExtra = (): void => {
    const added = splitList(extra).filter((id) => id !== self && !value.includes(id));
    if (added.length > 0) {
      onChange([...value, ...Array.from(new Set(added))]);
    }
    setExtra("");
  };
  return (
    <div className={`ids ${className}`} title={title}>
      {options.map((id) => {
        const foreign = !known.has(id);
        return (
          <label key={id} className={foreign ? "id-option foreign" : "id-option"} title={foreign ? "このファイルに無い id（ほかの層の種類か、綴り違い）" : undefined}>
            <input type="checkbox" value={id} checked={value.includes(id)} disabled={disabled} onChange={(event) => toggle(id, event.target.checked)} />
            {id}
          </label>
        );
      })}
      {options.length === 0 && <span className="dim">選べる種類が無い</span>}
      <input
        type="text"
        className="id-extra"
        spellCheck={false}
        placeholder="ほかの層の id を入力して Enter"
        value={extra}
        disabled={disabled}
        onChange={(event) => setExtra(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.nativeEvent.isComposing) {
            event.preventDefault();
            addExtra();
          }
        }}
        onBlur={addExtra}
      />
    </div>
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
