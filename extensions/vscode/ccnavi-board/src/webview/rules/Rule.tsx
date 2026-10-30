/**
 * ルール 1 件の行。折りたたんだときは要約 1 行、開くと欄が出る。
 *
 * 欄名は日本語で欄の左に出し、YAML のキー名は欄名のツールチップに載せる（`Captioned`）。
 * ツールの欄（`match`）は押すと選択肢が出る。`message` は deny だけの欄で、ask と allow に
 * 残っていれば「どこにも届かない」と言って消すボタンだけ出す。
 *
 * 組み合わせ条件（`when:`）を持つルールは、パターンの欄の代わりに組み合わせ条件の図（`When.tsx`）を出し、畳んだ行には
 * 組み合わせ条件を 1 行に縮めたもの（`all(regex …, not(any(2)))`）を出す。glob / regex 1 つのルールは
 * 「組み合わせ条件に変換」で同じ意味の組み合わせ条件にできる（除くものは `not: {any: …}` に入れ、`except:` は消す）。
 */
import { useState, type JSX, type ReactNode } from "react";

import { isToolName, KNOWN_TOOLS, SECTIONS, type FileField, type PatternKind, type RuleForm, type Section } from "../../core/rules-view.js";
import { condProblems, condSummary, toWhenBlocker, treeOfPattern } from "../../core/rules-when.js";
import { contextSummary, hasContext, staleMessage, summaryId, summaryMatch, summaryNote } from "./text.js";
import { When } from "./When.js";

/** 1 行の欄（input）で受ける値 */
type TextKey = "id" | "match" | "pattern" | "every" | "additionalContextFile" | "additionalContextOnceFile";
/** 文の欄（textarea）で受ける値。ブロック（`>-`）で書かれることがあるので折り返せる欄にする */
type AreaKey = "message" | "additionalContext" | "additionalContextOnce";

export interface RuleProps {
  /** 画面の中だけのキー（`state.ts`）。行を名指しするために `data-key` へ出す */
  readonly ruleKey: string;
  readonly section: Section;
  readonly rule: RuleForm;
  /** 絞り込みが適用する文字列（`data-find`）。ヒットしなければ `hidden` */
  readonly find: string;
  readonly hidden: boolean;
  readonly open: boolean;
  /** 直前の判定でヒットした行。折りたたんだままでも分かるように縁を付ける */
  readonly hit: boolean;
  /** ツールの選択肢が開いているか。開くのは画面ぜんたいで 1 つだけ */
  readonly pickerOpen: boolean;
  readonly onToggle: () => void;
  readonly onChange: (next: RuleForm) => void;
  readonly onMoveSection: (to: Section) => void;
  readonly onMove: (delta: number) => void;
  readonly onRemove: () => void;
  readonly onOpenPicker: () => void;
  readonly onPickFile: (field: FileField) => void;
  /** 「コンテキストの追加」の開閉。最初は値の有無で決め、以後はユーザの操作を覚える */
  readonly moreOpen: boolean;
  readonly onToggleMore: (open: boolean) => void;
  /** 案内が指定する、組み合わせ条件の図で選ぶ点（`pathKey`）。普段は渡さない */
  readonly pickNode?: string;
}

export function Rule(props: RuleProps): JSX.Element {
  const { rule, section } = props;
  // glob と regex の併記や、except が組み合わせ条件へそのまま移せない形なら、「組み合わせ条件に変換」を押させずに理由を出す
  const blocker = toWhenBlocker(rule);
  /** 案内が指す先（`data-tour`）。id 未設定の行には付けない（行ごとに名前を分けるので、別の行の欄を指さない） */
  const tour = (name: string): string | undefined => (rule.id === "" ? undefined : `rule-${rule.id}-${name}`);

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
    <Captioned name={label} yamlKey={name} tour={tour(name === "additionalContextFile" ? "file" : "once-file")}>
      <div className="with-button">
        {text(name, className, placeholder)}
        <button type="button" className="action small" title="ファイルを選ぶ" onClick={() => props.onPickFile(name)}>
          選択…
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
    <li className={classes.join(" ")} data-key={props.ruleKey} data-id={rule.id} data-find={props.find} data-tour={rule.id === "" ? undefined : `rule-${rule.id}`}>
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
        <button type="button" className="twist" title="このルールを開く／畳む" aria-expanded={props.open}>
          {props.open ? "▾" : "▸"}
        </button>
        <span className="sum">
          {/* id は長いと列の幅で切れるので、全体は title で見せる */}
          <span className={rule.id === "" ? "sum-id dim" : "sum-id"} title={summaryId(rule)}>
            {summaryId(rule)}
          </span>
          <span className="dim mono clip" title={rule.match}>
            {summaryMatch(rule)}
          </span>
          <span className="clip" title={rule.when !== undefined ? `when: ${condSummary(rule.when)}` : rule.pattern === "" ? "" : `${rule.kind} ${rule.pattern}`}>
            {rule.when !== undefined ? (
              <>
                <code className="sum-when">{condSummary(rule.when)}</code>
                {condProblems(rule.when).length > 0 && <span className="sum-note sum-when-error">組み合わせ条件に誤り {condProblems(rule.when).length}</span>}
              </>
            ) : rule.pattern === "" ? (
              <span className="dim">（{rule.kind} 未設定）</span>
            ) : (
              <code>{rule.pattern}</code>
            )}
            {summaryNote(section, rule) !== "" && <span className="sum-note">{summaryNote(section, rule)}</span>}
            {rule.excepts !== undefined && rule.excepts.length > 0 && (
              <span className="sum-note sum-except" title={`except: ${rule.excepts.join(" / ")}`}>
                除外 {rule.excepts.length}
              </span>
            )}
          </span>
          {/* 刻みは折りたたんだままでも見える。見えないと「毎回渡る」と思ったままプロンプトを直すことになる */}
          {rule.every === "" ? (
            <span className="sum-every" />
          ) : (
            <span className="tag sum-every" title={`every: ${rule.every}`}>
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
        <Captioned name="ツール" yamlKey="match" tour={tour("match")}>
          <Picker rule={rule} open={props.pickerOpen} onOpen={props.onOpenPicker} onChange={props.onChange}>
            {text("match", "f-match", "Bash / Write|Edit")}
          </Picker>
        </Captioned>
        <Captioned name="タイプ" yamlKey="deny / ask / allow" tour={tour("type")}>
          <select className="f-section" value={section} onChange={(event) => props.onMoveSection(event.target.value as Section)}>
            {SECTIONS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Captioned>
        {rule.when !== undefined ? (
          <Captioned name="組み合わせ条件" yamlKey="when" tour={tour("pattern")}>
            {/* 開いた行にだけ図を置く（畳んだ行の分まで React Flow を立ち上げない） */}
            {props.open ? <When tree={rule.when} pick={props.pickNode} onChange={(when) => props.onChange({ ...rule, when })} /> : <span />}
          </Captioned>
        ) : (
          <Captioned name="パターン" yamlKey="glob / regex" tour={tour("pattern")}>
            <div className="inline">
              <select className="f-kind" value={rule.kind} onChange={(event) => props.onChange({ ...rule, kind: event.target.value as PatternKind })}>
                <option value="glob">glob</option>
                <option value="regex">regex</option>
              </select>
              {text("pattern", "f-pattern pattern", rule.kind === "glob" ? "*git push*" : "\\bgit push\\b")}
              <button
                type="button"
                className="action small"
                data-action="to-when"
                data-tour={tour("to-when")}
                disabled={blocker !== undefined}
                title={blocker ?? "このパターン（と除外）を、同じ意味の組み合わせ条件（when）に変換します。除外は not の中へ移ります"}
                onClick={() => props.onChange(toWhen(rule))}
              >
                組み合わせ条件に変換
              </button>
            </div>
          </Captioned>
        )}
        {/* 除くもの（except）は見せるだけ。書き戻しは元のノードのままなので、消えも変わりもしない */}
        {rule.excepts !== undefined && rule.excepts.length > 0 && (
          <Captioned name="除外" yamlKey="except" tour={tour("except")}>
            <div className="f-except">
              {rule.excepts.map((item, i) => (
                <div key={i}>
                  <code>{item}</code>
                </div>
              ))}
              <span className="dim">
                {rule.when !== undefined ? "組み合わせ条件" : "パターン"}にヒットした操作でも、除外にヒットしたものは対象から外れます。画面では編集できません。ルールファイルで直すか、
                {rule.when !== undefined ? "組み合わせ条件の not に移してください" : "「組み合わせ条件に変換」で組み合わせ条件の中へ移してください"}
              </span>
            </div>
          </Captioned>
        )}
        {section === "deny" ? (
          <Captioned name="メッセージ" yamlKey="message" tour={tour("message")}>
            {area("message", "f-message", "なぜ拒否するかと、代わりに何をすればよいか（拒否されたモデルに届きます）")}
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
        {/* コンテキストの 4 欄は出番が少ないので見出し 1 行に折りたたむ。値があるルールだけ最初から開く */}
        <details className="more" data-tour={tour("context")} open={props.moreOpen} onToggle={(event) => props.onToggleMore(event.currentTarget.open)}>
          <summary>
            <b>コンテキストの追加</b>
            {contextSummary(rule)}
          </summary>
          <div className="sub">
            {/* 刻みはプロンプトの前。ここから下の 4 欄が「何回に 1 度届くか」を決める欄なので、先に置く。
                type は text（number ではない）。数でない値を空にしてしまうと、読み込めない値を
                見せて直させるという欄の目的が消える */}
            <Captioned name="every" yamlKey="every" tour={tour("every")}>
              {text("every", "f-every", "5（空なら毎回渡します）")}
            </Captioned>
            <Captioned name="additionalContext" yamlKey="additionalContext" tour={tour("text")}>
              {area("additionalContext", "f-context", "ヒットしたときにモデルへ渡すプロンプト")}
            </Captioned>
            {file("additionalContextFile", "additionalContextFile", "f-context-file", "ヒットしたときにモデルへ渡すファイル（先頭 4000 文字まで）")}
            <Captioned name="additionalContextOnce" yamlKey="additionalContextOnce" tour={tour("once")}>
              {area("additionalContextOnce", "f-once", "セッションで最初にヒットしたときだけモデルへ渡すプロンプト")}
            </Captioned>
            {file("additionalContextOnceFile", "additionalContextOnceFile", "f-once-file", "セッションで最初にヒットしたときだけモデルへ渡すファイル（先頭 4000 文字まで）")}
          </div>
        </details>
        <span className="buttons" data-tour={tour("buttons")}>
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
 * glob / regex 1 つのルールを、同じ意味の組み合わせ条件にする。除くもの（`except:`）は組み合わせ条件の中へ移し、
 * 書き戻しで `except:` を消す（`dropExcept`）。組み合わせ条件から glob / regex 1 つへは戻さない
 */
function toWhen(rule: RuleForm): RuleForm {
  const { excepts, exceptNodes, ...rest } = rule;
  const moved = exceptNodes ?? [];
  return {
    ...rest,
    pattern: "",
    when: treeOfPattern(rule.kind, rule.pattern, moved),
    ...(excepts !== undefined || exceptNodes !== undefined ? { dropExcept: true } : {}),
  };
}

/**
 * ツールの選択肢。`match` はツール名を `|` で並べたもので、判定は名前をそのまま突き合わせるので
 * 打ち間違えると気づかないうちにヒットしなくなる。書かせずに選ばせる。ファイルに書いてある知らない名前
 * （MCP のツールなど）も、消さずにそのまま選択肢にして出す（開いただけで消えたように見えないように）。
 *
 * 一覧にない新しいツールは、欄に名前を打って足す。形は `isToolName` で確かめる。一覧にない名前は、
 * ccnavi がそのツールの対象を取り出せないため、判定には届かずヒットしない（`lint` が warn で言う）。
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
  const [added, setAdded] = useState("");
  const [addError, setAddError] = useState("");
  const chosen = rule.match.split("|").map((name) => name.trim()).filter((name) => name !== "");
  const names = [...KNOWN_TOOLS, ...chosen.filter((name) => !(KNOWN_TOOLS as readonly string[]).includes(name))];
  const toggle = (name: string, on: boolean): void => {
    const next = names.filter((item) => (item === name ? on : chosen.includes(item)));
    onChange({ ...rule, match: next.join("|") });
  };
  const add = (): void => {
    const name = added.trim();
    if (name === "") {
      return;
    }
    if (!isToolName(name)) {
      setAddError("英字で始まり、英数字・_・- だけの名前を入れてください");
      return;
    }
    setAddError("");
    setAdded("");
    if (!chosen.includes(name)) {
      onChange({ ...rule, match: [...chosen, name].join("|") });
    }
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
        <div className="add-tool">
          <input
            type="text"
            aria-label="新しいツール名"
            placeholder="新しいツール名"
            spellCheck={false}
            value={added}
            aria-invalid={addError !== ""}
            onChange={(event) => setAdded(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
          />
          <button type="button" className="action" onClick={add}>
            追加
          </button>
          {addError !== "" && (
            <span className="add-error" role="alert">
              {addError}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** 欄名を左に置く。YAML のキー名は欄名のツールチップに載せる。`tour` は案内が指す先の名前 */
function Captioned({ name, yamlKey, tour, children }: { readonly name: string; readonly yamlKey?: string; readonly tour?: string; readonly children: ReactNode }): JSX.Element {
  return (
    <div className="field" data-tour={tour}>
      <span className="cap" title={yamlKey === undefined ? undefined : `YAML のキー: ${yamlKey}`}>
        {name}
      </span>
      {children}
    </div>
  );
}
