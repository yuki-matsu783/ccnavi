/**
 * 種類 1 件の行。畳んだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * 出番の少ない 5 欄（ほかの種類との関係と補足）は見出し 1 行に畳み、値がある種類だけ最初から開く。
 *
 * 関係の 3 欄（overlap / requires / after）は、このファイルのほかの種類の id を複数選択のセレクトボックスで選ぶ
 * （`IdPicker`）。自分の id は候補に出さない。`after` の候補は work の種類だけ（feedback の種類は
 * 待つ先にできず、feedback の種類は `after` を持てない。`phasetypes.py`）。同じ id を `after` と
 * `overlap` の両方には挙げられない（同じく error）ので、片方で選んだ id はもう片方で選べなくする。
 * 層（自身の層・プロジェクト）はほかの層の種類を指せるので、候補に無い id を打つ欄も出す。
 * 共通層はほかの層を指せない（照合は自分のファイルの中だけ）ので、その欄は出さない。
 */
import { useEffect, useId, useRef, useState, type JSX, type KeyboardEvent, type ReactNode } from "react";

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
  /** このファイルの種類の id と区分（並び順）。関係の欄の候補にする */
  readonly kinds: ReadonlyMap<string, PhaseKind>;
  /** 層（自身の層・プロジェクト）の画面か。層だけがほかの層の種類を指せる */
  readonly layer: boolean;
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

  const self = phase.id.trim();
  const others = Array.from(props.kinds.keys()).filter((id) => id !== "" && id !== self);
  const ids = (name: IdsKey, label: string, className: string, title: string): JSX.Element => {
    // after と overlap は同じ id を両方に挙げられない。もう片方で選んでいる id は選べなくする
    const blocked = name === "after" ? phase.overlap : name === "overlap" ? phase.after : [];
    const blockedBy = name === "after" ? "overlap" : "after";
    return (
      <IdPicker
        className={className}
        label={label}
        title={title}
        self={self}
        candidates={name === "after" ? others.filter((id) => props.kinds.get(id) === "work") : others}
        known={props.kinds}
        blocked={new Set(blocked.map((id) => id.trim()))}
        blockedNote={`${blockedBy} にも挙げているので選べない（両方に挙げると保存のときの検証が止める）`}
        typed={props.layer}
        value={phase[name]}
        disabled={disabled}
        onChange={(next) => props.onChange({ ...phase, [name]: next })}
      />
    );
  };

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
              {ids("overlap", "並行できる種類", "f-overlap", "この種類と並行して進めてよい種類")}
            </Captioned>
            <Captioned name="一緒に必要な種類" yamlKey="requires">
              {ids("requires", "一緒に必要な種類", "f-requires", "計画にこの種類を入れるなら、一緒に入れる必要がある種類")}
            </Captioned>
            <Captioned name="先に済ませる種類" yamlKey="after">
              {phase.kind === "feedback" && phase.after.length === 0 ? (
                <span className="f-after dim">feedback の種類は持てない（レビュー後の対応で、全体計画の待ち方の外にある）</span>
              ) : (
                ids("after", "先に済ませる種類", "f-after", "待ち方が dag のとき、この種類より先に閉じてレビューを終えておく work の種類")
              )}
            </Captioned>
            <Captioned name="案内するエージェント" yamlKey="agent">
              {text("agent", "f-agent narrow", "サブエージェント名（案内に出すだけで、割り当てはしない）")}
            </Captioned>
            <Captioned name="使う場面" yamlKey="when">
              {text("when", "f-when", "この種類を計画に入れる場面（エージェントへの案内にだけ使う）")}
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
 * 関係の欄。ほかの種類の id を複数選択のセレクトボックスで選ぶ。
 *
 * 押すだけで 1 件ずつ付け外しする（`mousedown` で素の動きを止める）。素の複数選択は Ctrl / Shift なしで
 * 押すとほかの選択が外れ、気付かずに関係を消しやすい。**キー操作も同じ理由で素の動きを止める。** 素の
 * 矢印キーは、動かした先の 1 件だけを選んだ状態に縮める（見て回るだけで関係が消える）。矢印・Home・End で
 * 印（`active`）だけを動かし、Space か Enter で付け外しする。印は `aria-activedescendant` で読み上げに伝える。
 * `change` はそれでも届いたとき（止めきれない操作）のために、届いた選択をそのまま受ける。
 *
 * 候補は呼ぶ側が決める（自分と空を除いた、このファイルの種類）。**候補に無い値も消さずに出す**
 * （ほかの層の種類・綴り違い・自分自身）。外せば並びから消える。値は前後の空白を落として読む
 * （実行ファイルも落として解く）。空の値は出さない。
 *
 * 並びは候補の順（ファイルの並び）に揃え、候補に無い値はその後ろに元の順で置く。選択を
 * 付け外しするたびに並びが入れ替わって差分が出る、ということをしない。
 *
 * 候補に無い id を打つ欄は `typed` のときだけ出す（層の画面）。打った文字は Enter か、欄を離れたときに
 * 足す（`,` 区切りで複数も可）。自分の id は打っても足さない。
 */
function IdPicker({
  value,
  self,
  candidates,
  known,
  blocked,
  blockedNote,
  typed,
  className,
  label,
  title,
  disabled,
  onChange,
}: {
  readonly value: readonly string[];
  readonly self: string;
  readonly candidates: readonly string[];
  readonly known: ReadonlyMap<string, PhaseKind>;
  readonly blocked: ReadonlySet<string>;
  readonly blockedNote: string;
  readonly typed: boolean;
  readonly className: string;
  readonly label: string;
  readonly title: string;
  readonly disabled: boolean;
  readonly onChange: (next: readonly string[]) => void;
}): JSX.Element {
  const [extra, setExtra] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const chosen = Array.from(new Set(value.map((id) => id.trim()).filter((id) => id !== "")));
  const picked = new Set(chosen);
  const options = Array.from(new Set([...candidates, ...chosen]));
  const ordered = (ids: ReadonlySet<string>): readonly string[] => options.filter((id) => ids.has(id)).concat(Array.from(ids).filter((id) => !options.includes(id)));
  const toggle = (id: string, checked: boolean): void => {
    const next = new Set(picked);
    if (checked) {
      next.add(id);
    } else {
      next.delete(id);
    }
    onChange(ordered(next));
  };
  const addExtra = (): void => {
    const next = new Set(picked);
    for (const id of splitList(extra)) {
      if (id !== self) {
        next.add(id);
      }
    }
    if (next.size !== picked.size) {
      onChange(ordered(next));
    }
    setExtra("");
  };
  const choose = (select: HTMLSelectElement): void => {
    onChange(ordered(new Set(Array.from(select.selectedOptions, (option) => option.value))));
  };
  const isLocked = (id: string): boolean => blocked.has(id) && !picked.has(id);
  // 候補が減ったときに印が外へはみ出さないよう、描くたびに収める
  const current = Math.min(active, options.length - 1);
  const onKeyDown = (event: KeyboardEvent<HTMLSelectElement>): void => {
    if (event.key === "Tab" || event.nativeEvent.isComposing) {
      return;
    }
    // Tab 以外は素の動きを止める（矢印も文字の頭出しも、選択を 1 件に縮める）
    event.preventDefault();
    const last = options.length - 1;
    const moves: Record<string, number> = { ArrowDown: current + 1, ArrowUp: current - 1, Home: 0, End: last, PageDown: current + 5, PageUp: current - 5 };
    if (event.key in moves) {
      setActive(Math.max(0, Math.min(last, moves[event.key])));
    } else if ((event.key === " " || event.key === "Enter") && current >= 0 && !isLocked(options[current])) {
      toggle(options[current], !picked.has(options[current]));
    }
  };
  return (
    <div className={`ids ${className}`} title={title}>
      {options.length > 0 && (
        <select
          multiple
          className="id-select"
          aria-label={label}
          aria-activedescendant={`${listId}-${current}`}
          size={Math.min(options.length, 6)}
          value={chosen}
          disabled={disabled}
          onChange={(event) => choose(event.currentTarget)}
          onKeyDown={onKeyDown}
        >
          {options.map((id, index) => {
            const note = id === self ? "自分自身を挙げている（外す）" : !known.has(id) ? "このファイルに無い id（ほかの層の種類か、綴り違い）" : !candidates.includes(id) ? "ここには挙げられない種類（外す）" : undefined;
            const locked = isLocked(id);
            const tip = [locked ? blockedNote : undefined, note].filter((part) => part !== undefined).join("／");
            return (
              <option
                key={id}
                id={`${listId}-${index}`}
                value={id}
                className={`id-option${note !== undefined ? " foreign" : ""}${index === current ? " active" : ""}`}
                title={tip === "" ? "押すか Space で選ぶ／外す" : tip}
                disabled={locked}
                onMouseDown={(event) => {
                  // 押すだけで 1 件ずつ付け外しする。素の複数選択は Ctrl なしで押すとほかの選択が外れる
                  event.preventDefault();
                  if (disabled || locked) {
                    return;
                  }
                  (event.currentTarget.parentElement as HTMLSelectElement | null)?.focus();
                  setActive(index);
                  toggle(id, !picked.has(id));
                }}
              >
                {id}
              </option>
            );
          })}
        </select>
      )}
      {options.length === 0 && <span className="dim">選べる種類が無い</span>}
      {typed && (
        <input
          type="text"
          className="id-extra"
          spellCheck={false}
          aria-label={`${label}にほかの層の id を足す`}
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
      )}
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
