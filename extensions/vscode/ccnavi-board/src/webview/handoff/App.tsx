/**
 * 取り込み待ち画面の本体。帯・件数の要約・対象の一覧・取り込みの確認と結果。
 *
 * 見せる中身は拡張ホストが渡す（`HandoffData`）。画面が自分で持つのは、ユーザが触って決めるもの
 * （選んだ対象、開いている行、確認の帯、直近の結果、ターミナルの案内）だけ。種類・状態・検査・差分・ハッシュは実行ファイルの答えを読むだけで、
 * 取り込めるか・取り込めない理由は `core/handoffmodel.ts` の判断をそのまま使う。
 * 取り込む手順（見るだけをもう一度呼んで `expect` を取る、錠を取り直す）は拡張ホストが持つ（`core/handoff-import.ts`）。
 */
import { useRef, useState, type JSX } from "react";

import type { HandoffData, HandoffMessage, HandoffOutcome, HandoffPage, ToHandoff } from "../../core/handoff-view.js";
import { LOCKED_KINDS, blockReason, reconcileSelection, selectablePaths } from "../../core/handoffmodel.js";
import type { Lock } from "../../core/lock.js";
import { useHostMessages } from "../useHostMessages.js";
import { Entry, type TerminalState } from "./Entry.js";
import { post } from "./post.js";
import { Result } from "./Result.js";

/** 中身が無いときの錠。止めていない扱いにする */
const NO_LOCK: Lock = { locked: false, reason: "", doing: [] };

function pageOf(data: HandoffData): HandoffPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

export function App({ initial }: { readonly initial: HandoffData }): JSX.Element {
  const [data, setData] = useState<HandoffData>(initial);
  const [lock, setLock] = useState<Lock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  /** 選んだ対象。パス → 選んだときの `entry_sha`（読み直したあとも同じ中身のものだけ残す） */
  const [selected, setSelected] = useState<ReadonlyMap<string, string>>(() => new Map());
  /** 開いている行のパス */
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  /** 「この内容で取り込む」の確認の帯を出しているか */
  const [confirming, setConfirming] = useState(false);
  /** 往復の途中か（更新・取り込み） */
  const [busy, setBusy] = useState(false);
  /** 直近の取り込みの結果 */
  const [outcome, setOutcome] = useState<HandoffOutcome | undefined>(undefined);
  /** ターミナルで打つコマンドの案内（パス → 答え）。読み直すと古くなるので破棄する */
  const [terminals, setTerminals] = useState<Readonly<Record<string, TerminalState>>>({});
  /** 届いたメッセージの中で「いまの一覧」を見るための ref（setState の更新関数の中から読むため） */
  const pageOfRef = useRef<HandoffPage | undefined>(pageOf(data));
  pageOfRef.current = pageOf(data);

  useHostMessages<ToHandoff>((message) => {
    if (message.type === "data" && message.data !== undefined) {
      const next = message.data;
      const page = pageOf(next);
      setData(next);
      setBusy(false);
      setConfirming(false);
      setTerminals({});
      if (page === undefined) {
        setSelected(new Map());
        return;
      }
      setLock(page.lock);
      setSelected((now) => reconcileSelection(now, page.list.entries, page.lock));
    } else if (message.type === "lock" && message.lock !== undefined) {
      const next = message.lock;
      setLock(next);
      // 錠が掛かった種類の選択は外す（取り込む直前にも取り直すが、選んだままにしない）
      setSelected((now) => {
        const kept = new Map(now);
        for (const path of now.keys()) {
          const entries = pageOfRef.current?.list.entries ?? [];
          const entry = entries.find((e) => e.path === path);
          if (entry === undefined || blockReason(entry, next) !== "") {
            kept.delete(path);
          }
        }
        return kept;
      });
    } else if (message.type === "cancelled") {
      setBusy(false);
      setConfirming(false);
    } else if (message.type === "imported" && message.result !== undefined && message.paths !== undefined) {
      setBusy(false);
      setConfirming(false);
      setOutcome({ paths: message.paths, result: message.result });
    } else if (message.type === "terminal" && message.path !== undefined) {
      const path = message.path;
      setTerminals((now) => ({ ...now, [path]: { command: String(message.command ?? ""), error: String(message.error ?? "") } }));
    }
  }, () => post({ type: "ready" }));

  const act = (message: HandoffMessage): void => post(message);

  if (data.kind === "loading") {
    return (
      <p className="empty" id="ccnavi-loading">
        {data.text}
      </p>
    );
  }

  if (data.kind === "disabled") {
    return (
      <>
        <p className="empty" id="handoff-disabled">
          取り込み待ち画面は使えません。
        </p>
        <pre className="load-error" data-role="disabled-reason">
          {data.reason}
        </pre>
        <button type="button" className="action" data-action="reload" onClick={() => act({ type: "refresh" })}>
          更新
        </button>
      </>
    );
  }

  if (data.kind === "error") {
    return (
      <>
        <p className="empty">取り込み待ち画面を読み込めませんでした。原因を直してから「更新」を押してください。</p>
        {data.trees !== undefined && data.tree !== undefined && (
          <TreeSelect tree={data.tree} trees={data.trees} disabled={false} onSwitch={(key) => act({ type: "switchTree", key })} />
        )}
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" onClick={() => act({ type: "refresh" })}>
          更新
        </button>
      </>
    );
  }

  const page = data.page;
  const entries = page.list.entries;
  const selectable = selectablePaths(entries, lock);
  const lockedPresent = lock.locked && entries.some((e) => LOCKED_KINDS.includes(e.kind) && e.board);

  const refresh = (): void => {
    setBusy(true);
    act({ type: "refresh" });
  };

  const toggleOpen = (path: string): void => {
    setOpen((now) => {
      const next = new Set(now);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const select = (path: string, sha: string, checked: boolean): void => {
    setConfirming(false);
    setSelected((now) => {
      const next = new Map(now);
      if (checked) {
        next.set(path, sha);
      } else {
        next.delete(path);
      }
      return next;
    });
  };

  const selectAll = (): void => {
    setConfirming(false);
    setSelected(new Map(entries.filter((e) => selectable.includes(e.path)).map((e) => [e.path, e.entry_sha])));
  };

  const clearAll = (): void => {
    setConfirming(false);
    setSelected(new Map());
  };

  const chosen = [...selected.keys()];
  const doImport = (): void => {
    setBusy(true);
    setOutcome(undefined);
    act({ type: "import", paths: chosen });
  };

  return (
    <>
      {page.notices.map((notice, index) => (
        <div key={index} className="banner warn">
          {notice}
        </div>
      ))}
      <header className="toolbar">
        <div className="summary">
          <TreeSelect tree={page.tree} trees={page.trees} disabled={busy} onSwitch={(key) => act({ type: "switchTree", key })} />
          <span className="path" title={page.root}>
            {page.list.tree}
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="reload" title="ファイルを再読み込みします" disabled={busy} onClick={refresh}>
            更新
          </button>
        </div>
      </header>
      <p id="lock" data-banner="lock" className={lockedPresent ? "lock" : "lock hidden"}>
        {lockedPresent ? `テンプレートと設定の取り込みは止めています: ${lock.reason}` : ""}
      </p>
      <p className="count" id="counts">
        <span>対象 {entries.length} 件</span>・<span>取り込める {selectable.length} 件</span>・<span>選択 {chosen.length} 件</span>・<span>対象外 {page.list.ignored} 件</span>
      </p>
      {outcome !== undefined && <Result outcome={outcome} />}
      <section className="entries">
        <h2>
          取り込み待ち <span className="count">{entries.length}</span>
        </h2>
        <p className="hint">
          wip/handoff/ に置いた下書きを、同じ相対パスへ取り込みます。取り込めるのは、新規か変更ありで、error が無い対象です。
        </p>
        {entries.length === 0 ? (
          <p className="empty" data-role="empty">
            取り込み待ちの下書きはありません。保護対象を直すときは、ccnavi-stage.sh で wip/handoff/ にコピーして編集します。
          </p>
        ) : (
          <>
            <div className="bulk">
              <button type="button" className="action small" data-action="select-all" disabled={busy || selectable.length === 0} onClick={selectAll}>
                取り込める対象をすべて選ぶ
              </button>
              <button type="button" className="action small" data-action="clear" disabled={busy || chosen.length === 0} onClick={clearAll}>
                選択を外す
              </button>
              <button type="button" className="action" data-action="import" disabled={busy || chosen.length === 0 || confirming} onClick={() => setConfirming(true)}>
                選んだ対象を取り込む…
              </button>
            </div>
            {confirming && (
              <div className="confirm" data-role="confirm" role="alertdialog" aria-label="取り込みの確認">
                <p>次の {chosen.length} 件を取り込みます。取り込み先のファイルは、差分のとおりに置き換わります。</p>
                <ul>
                  {chosen.map((path) => (
                    <li key={path}>{path}</li>
                  ))}
                </ul>
                <div className="buttons">
                  <button type="button" className="action" data-action="import-yes" disabled={busy} onClick={doImport}>
                    この内容で取り込む
                  </button>
                  <button type="button" className="action" data-action="import-cancel" disabled={busy} onClick={() => setConfirming(false)}>
                    やめる
                  </button>
                </div>
              </div>
            )}
            <ul className="list">
              {entries.map((entry) => (
                <Entry
                  key={entry.path}
                  entry={entry}
                  lock={lock}
                  selected={selected.has(entry.path)}
                  open={open.has(entry.path)}
                  disabled={busy}
                  terminal={terminals[entry.path]}
                  onToggle={() => toggleOpen(entry.path)}
                  onSelect={(checked) => select(entry.path, entry.entry_sha, checked)}
                  onTerminal={() => act({ type: "terminal", path: entry.path })}
                  onCopy={(text) => act({ type: "copy", text })}
                />
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  );
}

/** 見るツリーを切り替える欄。選んだ値は `onSwitch` で拡張ホストへ送るだけで、欄の値は `tree` に従う（制御された `<select>`） */
function TreeSelect({
  tree,
  trees,
  disabled,
  onSwitch,
}: {
  readonly tree: string;
  readonly trees: readonly { readonly key: string; readonly label: string }[];
  readonly disabled: boolean;
  readonly onSwitch: (key: string) => void;
}): JSX.Element {
  return (
    <label className="tree-select">
      ツリー{" "}
      <select id="tree" data-action="switch-tree" value={tree} disabled={disabled} onChange={(event) => onSwitch(event.target.value)}>
        {trees.map((t) => (
          <option key={t.key} value={t.key}>
            {t.label}
          </option>
        ))}
      </select>
    </label>
  );
}
