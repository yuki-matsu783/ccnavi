/**
 * リスク管理画面の本体。段階の閾値と、加点する項目の一覧。
 *
 * 見せる中身は拡張ホストが渡す（`RiskData`）。画面が持つのは、人が触って決めるもの
 * （編集中の配点、開いている行、絞り込み、直前の操作の一言）だけ。点は数えず、ファイルも書かない。
 *
 * **中身（`data`）が届いたら、編集中の配点はその中身で置き換える。** 届くのは編集を捨ててよい
 * ときだけ（人が「再読込」を押した、保存や作成が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ（ADR-0062）。移行前に HTML ごと入れ直していたのと同じ見え方になる。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { Lock } from "../../core/lock.js";
import { BUILTIN_LEVELS, LEVEL_NAMES, type FactorForm, type LevelName, type RiskData, type RiskPage, type ToRisk } from "../../core/risk-view.js";
import { applyAppearance } from "../appearance.js";
import { Captioned, Factor } from "./Factor.js";
import { post } from "./post.js";
import { countText, findText } from "./text.js";
import { draftOf, emptyFactor, formOf, keyer, loadOpen, openedFromIds, saveOpen, type Draft } from "./state.js";

/** 中身が読めなかったときの錠。画面は保存させない（押せる形で出して落とさない） */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

const EMPTY_DRAFT: Draft = { levels: { medium: "", high: "", critical: "" }, rows: [] };

/** 直前の操作の一言。生きている画面にしか届かないので持ち越さない */
interface Status {
  readonly text: string;
  readonly error: boolean;
}

interface Editing {
  readonly draft: Draft;
  /** 開いている行の鍵 */
  readonly open: ReadonlySet<string>;
}

function pageOf(data: RiskData): RiskPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

function editingOf(data: RiskData, nextKey: () => string): Editing {
  const page = pageOf(data);
  const draft = page === undefined ? EMPTY_DRAFT : draftOf(page.model.form, nextKey);
  return { draft, open: openedFromIds(draft, loadOpen()) };
}

export function App({ initial }: { readonly initial: RiskData }): JSX.Element {
  // 鍵は 1 枚の画面の中で数え上げる。描き直しで配り直さない
  const nextKey = useRef(keyer()).current;
  const [data, setData] = useState<RiskData>(initial);
  const [editing, setEditing] = useState<Editing>(() => editingOf(initial, nextKey));
  const [dirty, setDirty] = useState(false);
  /** 保存や作成の往復の間。欄を止める（通ると中身が入れ替わり、その間の編集は消えるため） */
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<Lock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  /** ファイルが外で変わった。捨てて読み直すかは人が決める */
  const [changed, setChanged] = useState(false);
  const [find, setFind] = useState("");
  /** 足した直後の行。id の欄に焦点を移したら忘れる */
  const [focusKey, setFocusKey] = useState<string | undefined>(undefined);

  const { draft, open } = editing;
  const page = pageOf(data);
  const exists = page?.exists === true;

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToRisk> & { data?: RiskData; lock?: Lock; message?: string; value?: unknown };
      if (message.type === "data" && message.data !== undefined) {
        const next = message.data;
        setData(next);
        setEditing(editingOf(next, nextKey));
        setDirty(false);
        setBusy(false);
        setStatus(undefined);
        setChanged(false);
        setLock(pageOf(next)?.lock ?? NO_LOCK);
      } else if (message.type === "failed") {
        setBusy(false);
        setStatus({ text: String(message.message ?? ""), error: true });
      } else if (message.type === "lock" && message.lock !== undefined) {
        setLock(message.lock);
      } else if (message.type === "changed") {
        setChanged(true);
      } else if (message.type === "cancelled") {
        // 「破棄して読み直す？」をやめた。止めた欄を戻す
        setBusy(false);
        setStatus(undefined);
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    // 組み上がったと伝える。拡張ホストはここで中身を渡し直す
    post({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, [nextKey]);

  // 足した行の id へ焦点を移す。畳んだままでは何を足したか分からないので、行は開いて出してある
  useEffect(() => {
    if (focusKey === undefined) {
      return;
    }
    const input = document.querySelector<HTMLInputElement>(`.factor[data-key="${focusKey}"] input.f-id`);
    input?.focus();
    setFocusKey(undefined);
  }, [focusKey]);

  /**
   * 読み直しを頼む。**押した時点で欄を止める。** 拡張ホストは実行ファイルに聞いてから中身を返す
   * ことがあり（層の置き場を解く）、その間に打った内容は、届いた中身で黙って消えるため。
   * 人が「破棄して読み直す？」をやめたときは `cancelled` が返り、欄が戻る。
   */
  const reload = (): void => {
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  if (data.kind === "error") {
    return (
      <>
        <p className="empty">
          リスク管理画面を読み直せなかった。原因を直してから「再読込」を押す（画面を開き直すなら、このタブを閉じてから「ccnavi ボード: リスク管理画面を開く」を実行する。開いたままでは前面に出るだけ）。
        </p>
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          再読込
        </button>
      </>
    );
  }

  const editDraft = (next: Draft, open?: ReadonlySet<string>): void => {
    setEditing((now) => ({ draft: next, open: open ?? now.open }));
    setDirty(true);
  };

  const editRow = (key: string, factor: FactorForm): void => {
    editDraft({ ...draft, rows: draft.rows.map((row) => (row.key === key ? { ...row, factor } : row)) });
  };

  const toggle = (key: string): void => {
    const next = new Set(open);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    setEditing((now) => ({ ...now, open: next }));
    saveOpen(draft, next);
  };

  const move = (key: string, delta: number): void => {
    const rows = draft.rows.slice();
    const from = rows.findIndex((row) => row.key === key);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= rows.length) {
      return;
    }
    const moved = rows[from];
    rows[from] = rows[to];
    rows[to] = moved;
    editDraft({ ...draft, rows });
  };

  const remove = (key: string): void => {
    editDraft({ ...draft, rows: draft.rows.filter((row) => row.key !== key) });
  };

  const add = (): void => {
    const row = { key: nextKey(), factor: emptyFactor() };
    editDraft({ ...draft, rows: [...draft.rows, row] }, new Set([...open, row.key]));
    setFocusKey(row.key);
  };

  const query = find.trim().toLowerCase();
  const rows = draft.rows.map((row) => {
    const text = findText(row.factor);
    return { ...row, find: text, hidden: query !== "" && !text.includes(query) };
  });
  const shown = rows.filter((row) => !row.hidden).length;
  const kept = rows.filter((row) => row.hidden && open.has(row.key)).length;

  return (
    <>
      <div id="changed" className={changed ? "banner warn" : "banner warn hidden"}>
        ファイルが外で変更されたので、画面の内容は古い。
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
          再読込
        </button>
      </div>
      <header className="toolbar">
        <div className="summary">
          <span className="path" title={page?.root ?? ""}>
            {page?.riskPath ?? ""}
          </span>
          <span id="dirty" className={dirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="open-risk" disabled={!exists} onClick={() => post({ type: "openFile" })}>
            エディタで開く
          </button>
          <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
            再読込
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-action="save"
            disabled={!dirty || lock.locked || busy || !exists}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "検証して保存中…", error: false });
              post({ type: "save", form: formOf(draft) });
            }}
          >
            保存
          </button>
        </div>
      </header>
      <p id="lock" className={lock.locked ? "lock" : "lock hidden"}>
        {lock.reason}
      </p>
      {page !== undefined && page.model.problems.length > 0 && (
        <ul className="problems">
          {page.model.problems.map((problem, index) => (
            <li key={index}>{problem}</li>
          ))}
        </ul>
      )}
      {page !== undefined && !exists && (
        <div className="banner missing">
          <span>{page.riskPath} が無い。実行ファイルは組み込みの配点で数えている（画面の値はその組み込みの配点）。直すにはまずファイルを作る。</span>
          <button
            type="button"
            className="action primary"
            data-action="create"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "ファイルを作成中…", error: false });
              post({ type: "create" });
            }}
          >
            組み込みの配点でファイルを作る
          </button>
        </div>
      )}
      <section className="block">
        <h2>
          段階の閾値 <span className="count">点がこの値以上になると段階が上がる。HIGH 以上はレビューが済むまで止まる</span>
        </h2>
        <details className="help">
          <summary>この欄の説明</summary>
          <p className="hint">
            点がその値以上になると段階が上がる（LOW → MEDIUM → HIGH → CRITICAL）。<strong>HIGH 以上はレビューが済むまでフェーズが止まり</strong>
            、宣言に関わらず人間レビューが要る扱いになる。medium ≤ high ≤ critical の順。空ならその段階は組み込みの値（
            {LEVEL_NAMES.map((name) => `${name} ${BUILTIN_LEVELS[name]}`).join(" / ")}）。
          </p>
        </details>
        <div className="levels" id="levels">
          {LEVEL_NAMES.map((name) => (
            <Captioned key={name} name={name.toUpperCase()} yamlKey={`levels.${name}`}>
              <input
                type="text"
                className="f-level"
                spellCheck={false}
                inputMode="numeric"
                title={`${name.toUpperCase()} 以上になる点`}
                placeholder={`既定 ${BUILTIN_LEVELS[name]}`}
                value={draft.levels[name]}
                disabled={busy || !exists}
                onChange={(event) => editDraft({ ...draft, levels: { ...draft.levels, [name]: event.target.value } as Readonly<Record<LevelName, string>> })}
              />
            </Captioned>
          ))}
        </div>
      </section>
      <section className="block">
        <h2>
          項目{" "}
          <span className="count" id="factor-count">
            {countText(draft.rows.length, query, shown, kept)}
          </span>
          <button type="button" className="action small" data-action="add" disabled={busy || !exists} onClick={add}>
            ＋ 項目を追加
          </button>
        </h2>
        <div className="find">
          <input id="find" type="search" placeholder="id・当て方・値・文面で絞り込む" spellCheck={false} value={find} onChange={(event) => setFind(event.target.value)} />
          <span className="hint">行を押すと開く</span>
        </div>
        <details className="help">
          <summary>この欄の説明</summary>
          <p className="hint">
            子を閉じるとき、その子の差分（base_sha..HEAD）に当てて加点する。1 件につき当て方は 1 つ。点の合計で段階が決まり、フェーズの点は子の最大値。
            <code>script</code> が失敗したときと出力が読めないときは安全側に倒して points をそのまま加点し、<code>judge</code> は判定が揃うまで子を閉じられない。
          </p>
        </details>
        <ul className="list" id="factors">
          {rows.map((row) => (
            <Factor
              key={row.key}
              factorKey={row.key}
              factor={row.factor}
              find={row.find}
              hidden={row.hidden}
              open={open.has(row.key)}
              disabled={busy || !exists}
              onToggle={() => toggle(row.key)}
              onChange={(factor) => editRow(row.key, factor)}
              onMove={(delta) => move(row.key, delta)}
              onRemove={() => remove(row.key)}
            />
          ))}
          {draft.rows.length === 0 && <li className="empty">項目が無い。加点する項目が無ければ、どの子も LOW のまま閉じる</li>}
        </ul>
      </section>
      <footer className={status?.error === true ? "foot error" : "foot"}>
        <span id="status">{status?.text ?? ""}</span>
      </footer>
    </>
  );
}
