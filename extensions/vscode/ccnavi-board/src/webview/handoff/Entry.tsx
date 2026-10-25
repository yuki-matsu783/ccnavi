/**
 * 対象 1 件。見出しに選択の欄・パス・種類・状態のバッジを出し、取り込めない理由（錠を含む）を直下に出す。
 * 開くと差分・指摘（errors / warnings）を出す。`board` が偽の対象（`defense`）には、ターミナルで取り込む案内を出す。
 * 取り込めるか・取り込めない理由は実行ファイルの答えを読んだ `handoffmodel.ts` の判断のまま使う。
 */
import type { JSX } from "react";

import type { HandoffEntry } from "../../core/handoff-view.js";
import { blockReason } from "../../core/handoffmodel.js";
import type { Lock } from "../../core/lock.js";
import { DEFENSE_NOTE, kindLabel, stateLabel, stateTone } from "./text.js";

/** ターミナルで打つコマンドの案内の状態。まだ頼んでいなければ undefined */
export interface TerminalState {
  readonly command: string;
  readonly error: string;
}

export interface EntryProps {
  readonly entry: HandoffEntry;
  readonly lock: Lock;
  readonly selected: boolean;
  readonly open: boolean;
  /** 往復の途中か案内の間は、選択もターミナルの案内も頼めない */
  readonly disabled: boolean;
  readonly terminal: TerminalState | undefined;
  readonly onToggle: () => void;
  readonly onSelect: (checked: boolean) => void;
  readonly onTerminal: () => void;
  readonly onCopy: (text: string) => void;
}

export function Entry({ entry, lock, selected, open, disabled, terminal, onToggle, onSelect, onTerminal, onCopy }: EntryProps): JSX.Element {
  const reason = blockReason(entry, lock);
  const importable = reason === "";
  const tone = stateTone(entry.state);
  return (
    <li className={`row entry${open ? " open" : ""}`} data-path={entry.path} data-kind={entry.kind} data-state={entry.state}>
      <div
        className="row-head"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
      >
        <span className="twist">{open ? "▾" : "▸"}</span>
        {/* 見出しの開閉と混ざらないよう、選択の欄の操作は見出しに伝えない（キーも同じ） */}
        <input
          type="checkbox"
          className="entry-pick"
          data-action="select"
          aria-label={`${entry.path} を取り込む対象に選ぶ`}
          checked={selected}
          disabled={!importable || disabled}
          title={importable ? undefined : reason}
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
          onChange={(event) => onSelect(event.target.checked)}
        />
        <span className="entry-path mono">{entry.path}</span>
        <span className="tag" data-role="kind">
          {kindLabel(entry.kind)}
        </span>
        <span className={tone === "" ? "tag" : `tag ${tone}`} data-role="state">
          {stateLabel(entry.state)}
        </span>
        {!entry.board && <span className="tag warn">ターミナルで取り込む</span>}
        {entry.errors.length > 0 && <span className="tag bad">error {entry.errors.length}</span>}
        {entry.warnings.length > 0 && <span className="tag warn">warn {entry.warnings.length}</span>}
      </div>
      {!importable && (
        <p className="hint entry-reason" data-role="reason">
          取り込めません: {reason}
        </p>
      )}
      {open && (
        <div className="row-body entry-body">
          {entry.errors.length > 0 || entry.warnings.length > 0 ? (
            <ul className="entry-notes">
              {entry.errors.map((text, index) => (
                <li key={`e${index}`} className="sev-error">
                  <span className="sev-label">[error]</span> <span>{text}</span>
                </li>
              ))}
              {entry.warnings.map((text, index) => (
                <li key={`w${index}`} className="sev-warn">
                  <span className="sev-label">[warn]</span> <span>{text}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <h3 className="entry-diff-head">差分（制御文字・書式文字・改行コードは、見える形で示します）</h3>
          {entry.diff === "" ? (
            <p className="hint" data-role="no-diff">
              差分はありません。
            </p>
          ) : (
            <pre className="entry-diff" data-role="diff">
              {entry.diff}
            </pre>
          )}
          {!entry.board && <TerminalGuide entry={entry} terminal={terminal} disabled={disabled} onTerminal={onTerminal} onCopy={onCopy} />}
        </div>
      )}
    </li>
  );
}

/**
 * `board` が偽の対象の案内。ボードからは取り込まない。ターミナルで打つコマンドを出す
 * （`expect` はそのパスだけを指して見るだけをもう一度呼んで取る。拡張が組み直さない）。
 */
function TerminalGuide({
  entry,
  terminal,
  disabled,
  onTerminal,
  onCopy,
}: {
  readonly entry: HandoffEntry;
  readonly terminal: TerminalState | undefined;
  readonly disabled: boolean;
  readonly onTerminal: () => void;
  readonly onCopy: (text: string) => void;
}): JSX.Element {
  return (
    <div className="terminal-guide" data-role="terminal-guide">
      <p>
        この対象（{kindLabel(entry.kind)}）はボードから取り込めません。差分を読んだうえで、ターミナルで次のコマンドを打って取り込みます。
      </p>
      {entry.kind === "defense" && <p className="hint">{DEFENSE_NOTE}</p>}
      {terminal === undefined ? (
        <button type="button" className="action small" data-action="show-terminal" disabled={disabled} onClick={onTerminal}>
          ターミナルのコマンドを出す
        </button>
      ) : terminal.command !== "" ? (
        <>
          <pre className="entry-command" data-role="terminal-command">
            {terminal.command}
          </pre>
          <button type="button" className="action small" data-action="copy-terminal" onClick={() => onCopy(terminal.command)}>
            コピー
          </button>
        </>
      ) : (
        <p className="status failed" data-role="terminal-error">
          {terminal.error}
        </p>
      )}
    </div>
  );
}
