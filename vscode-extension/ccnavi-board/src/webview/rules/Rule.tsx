/**
 * ルール 1 件の行。畳んだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * ツールの欄（`match`）は押すと札が出る。`message` は deny だけの欄で、ask と allow に
 * 残っていれば「どこにも届かない」と言って消すボタンだけ出す。
 */
import type { JSX, ReactNode } from "react";

import { KNOWN_TOOLS, SECTIONS, type FileField, type PatternKind, type RuleForm, type Section } from "../../core/rules-view.js";
import { contextSummary, hasContext, staleMessage, summaryId, summaryMatch, summaryNote } from "./text.js";

/** 1 行の欄（input）で受ける値 */
type TextKey = "id" | "match" | "pattern" | "every" | "additionalContextFile" | "additionalContextOnceFile";
/** 文の欄（textarea）で受ける値。ブロック（`>-`）で書かれることがあるので折り返せる欄にする */
type AreaKey = "message" | "additionalContext" | "additionalContextOnce";

export interface RuleProps {
  /** 画面の中だけの鍵（`state.ts`）。行を名指しするために `data-key` へ出す */
  readonly ruleKey: string;
  readonly section: Section;
  readonly rule: RuleForm;
  /** 絞り込みが当てる文字列（`data-find`）。当たらなければ `hidden` */
  readonly find: string;
  readonly hidden: boolean;
  readonly open: boolean;
  /** 直前の判定で当たった行。畳んだままでも分かるように縁を付ける */
  readonly hit: boolean;
  /** ツールの札が開いているか。開くのは画面ぜんたいで 1 つだけ */
  readonly pickerOpen: boolean;
  readonly onToggle: () => void;
  readonly onChange: (next: RuleForm) => void;
  readonly onMoveSection: (to: Section) => void;
  readonly onMove: (delta: number) => void;
  readonly onRemove: () => void;
  readonly onOpenPicker: () => void;
  readonly onPickFile: (field: FileField) => void;
  /** 「コンテキストの追加」の開閉。最初は値の有無で決め、以後は人の操作を覚える */
  readonly moreOpen: boolean;
  readonly onToggleMore: (open: boolean) => void;
}

export function Rule(props: RuleProps): JSX.Element {
  const { rule, section } = props;

  const text = (name: TextKey, className: string, placeholder: string): JSX.Element => (
    <input
      type="text"
      className={className}
      spellCheck={false}
      placeholder={placeholder}
      value={rule[name]}
      onChange={(event) => props.onChange({ ...rule, [name]: event.target.value })}
    />
  );
  const area = (name: AreaKey, className: string, placeholder: string): JSX.Element => (
    <textarea className={className} placeholder={placeholder} value={rule[name]} onChange={(event) => props.onChange({ ...rule, [name]: event.target.value })} />
  );
  /** 手で書くほかに、VS Code のダイアログでも選べる欄。選んだ結果は拡張ホストが `picked` で返す */
  const file = (name: FileField, label: string, className: string, placeholder: string): JSX.Element => (
    <Captioned name={label} yamlKey={name}>
      <div className="with-button">
        {text(name, className, placeholder)}
        <button type="button" className="action small" title="ファイルを選ぶ" onClick={() => props.onPickFile(name)}>
          選ぶ…
        </button>
      </div>
    </Captioned>
  );

  const classes = ["row", "rule"];
  if (props.open) {
    classes.push("open");
  }
  if (props.hidden) {
    classes.push("hidden-by-find");
  }
  if (props.hit) {
    classes.push("hit");
  }

  return (
    <li className={classes.join(" ")} data-key={props.ruleKey} data-id={rule.id} data-find={props.find}>
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
        <button type="button" className="twist" title="このルールを開く／畳む" aria-expanded={props.open}>
          {props.open ? "▾" : "▸"}
        </button>
        <span className="sum">
          <span className={rule.id === "" ? "sum-id dim" : "sum-id"}>{summaryId(rule)}</span>
          <span className="dim mono clip" title={rule.match}>
            {summaryMatch(rule)}
          </span>
          <span className="clip" title={rule.pattern === "" ? "" : `${rule.kind} ${rule.pattern}`}>
            {rule.pattern === "" ? <span className="dim">（{rule.kind} 未設定）</span> : <code>{rule.pattern}</code>}
            {summaryNote(section, rule) !== "" && <span className="sum-note">{summaryNote(section, rule)}</span>}
          </span>
          {/* 刻みは畳んだままでも見える。見えないと「毎回渡る」と思ったまま渡す文を直すことになる */}
          {rule.every === "" ? (
            <span className="sum-every" />
          ) : (
            <span className="tag sum-every" title={`渡す回の刻み（every）: ${rule.every}`}>
              {rule.every} 回ごと
            </span>
          )}
          <span className={hasContext(rule) ? "sum-flag on" : "sum-flag"} title={hasContext(rule) ? "コンテキストの追加あり" : ""} />
        </span>
      </div>
      <div className="row-body">
        <Captioned name="id" yamlKey="id">
          {text("id", "f-id narrow", "git-push")}
        </Captioned>
        <Captioned name="ツール" yamlKey="match">
          <Picker rule={rule} open={props.pickerOpen} onOpen={props.onOpenPicker} onChange={props.onChange}>
            {text("match", "f-match", "Bash / Write|Edit")}
          </Picker>
        </Captioned>
        <Captioned name="タイプ" yamlKey="deny / ask / allow">
          <select className="f-section" value={section} onChange={(event) => props.onMoveSection(event.target.value as Section)}>
            {SECTIONS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Captioned>
        <Captioned name="パターン" yamlKey="glob / regex">
          <div className="inline">
            <select className="f-kind" value={rule.kind} onChange={(event) => props.onChange({ ...rule, kind: event.target.value as PatternKind })}>
              <option value="glob">glob</option>
              <option value="regex">regex</option>
            </select>
            {text("pattern", "f-pattern pattern", rule.kind === "glob" ? "*git push*" : "\\bgit push\\b")}
          </div>
        </Captioned>
        {section === "deny" ? (
          <Captioned name="文面" yamlKey="message">
            {area("message", "f-message", "なぜ拒否するかと、代わりに何をすればよいか（拒否されたモデルに届く）")}
          </Captioned>
        ) : (
          rule.message !== "" && (
            <p className="stale">
              {staleMessage(section)}
              <code>{rule.message}</code>
              <button type="button" className="action small" onClick={() => props.onChange({ ...rule, message: "" })}>
                message を削除
              </button>
            </p>
          )
        )}
        {/* コンテキストの 4 欄は出番が少ないので見出し 1 行に畳む。値があるルールだけ最初から開く */}
        <details className="more" open={props.moreOpen} onToggle={(event) => props.onToggleMore(event.currentTarget.open)}>
          <summary>
            <b>コンテキストの追加</b>
            {contextSummary(rule)}
          </summary>
          <div className="sub">
            {/* 刻みは渡す文の前。ここから下の 4 欄が「何回に 1 度届くか」を決める欄なので、先に置く。
                type は text（number ではない）。数でない値を空にしてしまうと、読めない値を
                見せて直させるという欄の目的が消える */}
            <Captioned name="渡す回の刻み" yamlKey="every">
              {text("every", "f-every", "5（空なら毎回渡す）")}
            </Captioned>
            <Captioned name="渡す文" yamlKey="additionalContext">
              {area("additionalContext", "f-context", "ヒットしたときにモデルへ渡すプロンプト")}
            </Captioned>
            {file("additionalContextFile", "渡すファイル", "f-context-file", "ヒットしたときにモデルへ渡すファイル（先頭 4000 文字まで）")}
            <Captioned name="初回だけ渡す文" yamlKey="additionalContextOnce">
              {area("additionalContextOnce", "f-once", "セッションで最初にヒットしたときだけモデルへ渡すプロンプト")}
            </Captioned>
            {file("additionalContextOnceFile", "初回だけ渡すファイル", "f-once-file", "セッションで最初にヒットしたときだけモデルへ渡すファイル（先頭 4000 文字まで）")}
          </div>
        </details>
        <span className="buttons">
          <button type="button" className="action small" title="上へ" onClick={() => props.onMove(-1)}>
            ↑
          </button>
          <button type="button" className="action small" title="下へ" onClick={() => props.onMove(1)}>
            ↓
          </button>
          <button type="button" className="action small" onClick={() => props.onRemove()}>
            削除
          </button>
        </span>
      </div>
    </li>
  );
}

/**
 * ツールの札。`match` はツール名を `|` で並べたもので、判定は名前をそのまま突き合わせるので
 * 打ち間違えると黙って当たらなくなる。書かせずに選ばせる。**ファイルに書いてある知らない名前
 * （MCP のツールなど）も、消さずにそのまま札にして出す**（開いただけで消えたように見えないように）。
 */
function Picker({
  rule,
  open,
  onOpen,
  onChange,
  children,
}: {
  readonly rule: RuleForm;
  readonly open: boolean;
  readonly onOpen: () => void;
  readonly onChange: (next: RuleForm) => void;
  readonly children: ReactNode;
}): JSX.Element {
  const chosen = rule.match.split("|").map((name) => name.trim()).filter((name) => name !== "");
  const names = [...KNOWN_TOOLS, ...chosen.filter((name) => !(KNOWN_TOOLS as readonly string[]).includes(name))];
  const toggle = (name: string, on: boolean): void => {
    const next = names.filter((item) => (item === name ? on : chosen.includes(item)));
    onChange({ ...rule, match: next.join("|") });
  };
  return (
    <div className={open ? "picker open" : "picker"} onClick={onOpen} onFocus={onOpen}>
      {children}
      <div className="menu">
        {names.map((name) => (
          <label className="tool" key={name}>
            <input type="checkbox" value={name} checked={chosen.includes(name)} onChange={(event) => toggle(name, event.target.checked)} />
            {name}
          </label>
        ))}
      </div>
    </div>
  );
}

/** 欄名を左に置く。YAML のキー名は欄名のツールチップに載せる */
function Captioned({ name, yamlKey, children }: { readonly name: string; readonly yamlKey?: string; readonly children: ReactNode }): JSX.Element {
  return (
    <div className="field">
      <span className="cap" title={yamlKey === undefined ? undefined : `YAML のキー: ${yamlKey}`}>
        {name}
      </span>
      {children}
    </div>
  );
}
