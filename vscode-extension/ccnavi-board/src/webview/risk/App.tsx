/**
 * リスク管理画面の本体。リスクレベルの基準点と、加点する項目の一覧。
 *
 * 見せる中身は拡張ホストが渡す（`RiskData`）。画面が持つのは、人が触って決めるもの
 * （編集中の配点、開いている行、絞り込み、直前の操作の一言）だけ。点は数えず、ファイルも書かない。
 *
 * **中身（`data`）が届いたら、編集中の配点はその中身で置き換える。** 届くのは編集を捨ててよい
 * ときだけ（人が「再読込」を押した、保存や作成が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ（ADR-0062）。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import type { Lock } from "../../core/lock.js";
import { BUILTIN_LEVELS, LEVEL_NAMES, type FactorForm, type LevelName, type RiskData, type RiskPage, type ToRisk } from "../../core/risk-view.js";
import { applyAppearance } from "../appearance.js";
import { Tour, TourButton, useTour, type TourStep } from "../Tour.js";
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
  /** ファイルが外で変わった。破棄して読み直すかは人が決める */
  const [changed, setChanged] = useState(false);
  const [find, setFind] = useState("");
  /** 足した直後の行。id の欄に焦点を移したら忘れる */
  const [focusKey, setFocusKey] = useState<string | undefined>(undefined);

  const { draft, open } = editing;
  const page = pageOf(data);
  const exists = page?.exists === true;
  const tour = useTour(data.kind === "page", { onEnd: () => post({ type: "tourDone" }) });
  const requestTour = tour.request;

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
      } else if (message.type === "tour") {
        requestTour();
      } else if (message.type === "appearance") {
        applyAppearance(message.value);
      }
    };
    window.addEventListener("message", onMessage);
    // 組み上がったと伝える。拡張ホストはここで中身を渡し直す
    post({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, [nextKey, requestTour]);

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
          リスク管理画面を読み込めませんでした。原因を直してから「更新」を押してください（画面を開き直すなら、このタブを閉じてから「ccnavi ボード: リスク管理を開く」を実行してください。開いたままでは前面に出るだけです）。
        </p>
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          更新
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
        ファイルの変更を検知しました。更新してください。
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={reload}>
          更新
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
          <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={reload}>
            更新
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
        <TourButton onClick={tour.start} />
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
          <span>{page.riskPath} がありません。実行ファイルは組み込みの配点で数えています（画面の値はその組み込みの配点です）。直すにはまずファイルを作ってください。</span>
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
          リスクレベルの基準点 <span className="count">リスクの合計点がこの値以上になると、リスクレベルが 1 段上がります。HIGH 以上の場合は、次フェーズに進む前に人間レビューが必須になります
          </span>
        </h2>
        <details className="help">
          <summary>この欄の説明</summary>
          <p className="hint">
            子チケットを閉じるとき、下の「項目」で当てはまった点を足し合わせて、その変更のリスクの点を出します。
            合計点が設定値以上になると、リスクレベルが LOW → MEDIUM → HIGH → CRITICAL の順に上がっていきます。
            <strong>HIGH 以上になったフェーズは、レビューが終わるまで先へ進めません。</strong>
            チケットで「レビュー不要」と宣言していても、人間のレビューが必要になります。
            値は MEDIUM ≤ HIGH ≤ CRITICAL となるように入れてください。空欄にしたリスクレベルは、組み込みの値（
            {LEVEL_NAMES.map((name) => `${name} ${BUILTIN_LEVELS[name]}`).join(" / ")}）を使います。
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
          <input id="find" type="search" placeholder="id・加点条件・値・理由で絞り込む" spellCheck={false} value={find} onChange={(event) => setFind(event.target.value)} />
        </div>
        <details className="help">
          <summary>この欄の説明</summary>
          <p className="hint">
            子チケットの完了時、その子の差分（base_sha..HEAD）で判定して加点します。1 件につき加点条件は 1 つです。
            <code>script</code> が失敗したときと出力が読めないときは安全側に倒して points をそのまま加点し、<code>judge</code> は判定が揃うまで子を閉じられません。
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
          {draft.rows.length === 0 && <li className="empty">項目がありません。加点する項目が無ければ、どの子も LOW のまま閉じます</li>}
        </ul>
      </section>
      {tour.touring && <Tour steps={exists ? TOUR_STEPS : [MISSING_STEP, ...TOUR_STEPS]} onClose={tour.end} />}
      <footer className={status?.error === true ? "foot error" : "foot"}>
        <span id="status">{status?.text ?? ""}</span>
      </footer>
    </>
  );
}

/** ファイルが無いときだけ、案内の頭で作るボタンを指す（無いうちは欄が止まっていて直せない） */
const MISSING_STEP: TourStep = {
  target: '[data-action="create"]',
  title: "まずファイルを作る",
  body: "配点のファイルがまだありません。実行ファイルは組み込みの配点で数えています。直すには、ここで組み込みの配点からファイルを作ってください。",
};

/** リスク管理画面の案内。画面の様子は動かさないので、閉じても戻すものは無い */
const TOUR_STEPS: readonly TourStep[] = [
  {
    target: "#levels",
    title: "リスクレベルの基準点",
    body: "子チケットのリスクの点がこの値以上になると、リスクレベルが 1 段上がります（LOW → MEDIUM → HIGH → CRITICAL）。HIGH 以上になると、レビューが終わるまでフェーズは先へ進めません。空欄なら組み込みの値を使います。",
  },
  {
    target: "#factors",
    title: "項目",
    body: "子チケットを閉じるとき、その差分に当てて加点する項目です。行を押すと欄が開き、加点条件と点を直せます。「＋ 項目を追加」で足せます。",
  },
  {
    target: "#save",
    title: "保存",
    body: "保存すると実行ファイルが検証してから書き込みます。通らなければ、下に理由が出ます。",
  },
  {
    target: '[data-action="tour"]',
    title: "案内",
    body: "この案内は、ヘッダ右上の ? からもう一度見られます。",
  },
];
