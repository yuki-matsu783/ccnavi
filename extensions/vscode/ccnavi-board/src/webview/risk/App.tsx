/**
 * リスク管理画面の本体。リスクレベルの境目の点と、加点する項目の一覧。
 *
 * 見せる中身は拡張ホストが渡す（`RiskData`）。画面が持つのは、ユーザが触って決めるもの
 * （編集中の配点、開いている行、絞り込み、直前の操作の一言）だけ。点は数えず、ファイルも書かない。
 *
 * 中身（`data`）が届いたら、編集中の配点はその中身で置き換える。 届くのは編集を破棄してよい
 * ときだけ（ユーザが「再読み込み」を押した、保存や作成が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ。
 */
import { useEffect, useRef, useState, type JSX } from "react";

import { lockClass, type Lock } from "../../core/lock.js";
import { BUILTIN_LEVELS, LEVEL_NAMES, type FactorForm, type LevelName, type RiskData, type RiskMessage, type RiskPage, type ToRisk } from "../../core/risk-view.js";
import { useHostMessages } from "../useHostMessages.js";
import { TargetSelect } from "../TargetSelect.js";
import { HandoffLink } from "../shared/HandoffLink.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { parseRiskSample } from "../../core/tour-risk.js";
import { defaultSum } from "../../core/sums.js";
import riskTour from "../tours/risk.yml";
import { Captioned, Factor } from "./Factor.js";
import { Sum } from "./Sum.js";
import { post } from "./post.js";
import { countText, findText } from "./text.js";
import { Code } from "../shared/Code.js";
import { foldFind } from "../shared/find.js";
import { draftOf, emptyFactor, formOf, keyer, loadOpen, openedFromIds, saveOpen, type Draft } from "./state.js";

/** 中身が読めなかったときの錠。画面は保存させない（押せる形で出して異常終了させない） */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

const EMPTY_DRAFT: Draft = { levels: { medium: "", high: "", critical: "" }, rows: [] };

/**
 * 案内（`tours/risk.yml`）と、案内の間だけ描く見本。今の配点は使わず、画面が持つ編集中の内容にも触れない。
 * 読み込み時に形を確かめる（壊れていればテストが先に落とす）。
 */
const TOUR = tourOf(riskTour);
const SAMPLE = parseRiskSample(TOUR.sample);
/** 見本の行のキー。実際の行のキー（`f1`…）と重ならない */
function sampleDraft(page: RiskPage): Draft {
  let seq = 0;
  return draftOf(page.model.form, () => `s${(seq += 1)}`);
}
const SAMPLE_DRAFT = sampleDraft(SAMPLE.page);
const SAMPLE_MISSING_DRAFT = sampleDraft(SAMPLE.missing);
const NO_SCENE: Scene = {};

/** 手順の `scene` の文字列を `,` で割る */
function sceneList(value: Scene[string] | undefined): readonly string[] {
  return typeof value === "string" && value !== "" ? value.split(",").map((item) => item.trim()) : [];
}

/** 直前の操作の一言。生きている画面にしか届かないので持ち越さない */
interface Status {
  readonly text: string;
  readonly error: boolean;
}

interface Editing {
  readonly draft: Draft;
  /** 開いている行のキー */
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
  // キーは 1 枚の画面の中で数え上げる。描き直しで配り直さない
  const nextKey = useRef(keyer()).current;
  const [data, setData] = useState<RiskData>(initial);
  const [editing, setEditing] = useState<Editing>(() => editingOf(initial, nextKey));
  const [dirty, setDirty] = useState(false);
  /** 保存や作成の往復の間。欄を止める（通ると中身が入れ替わり、その間の編集は消えるため） */
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<Lock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  /** ファイルが外で変わった。破棄して読み直すかはユーザが決める */
  const [changed, setChanged] = useState(false);
  /** 保存が通ったときの検証の warn の本文。帯に出し、次の編集か更新（中身の入れ替え）で消す */
  const [saveWarns, setSaveWarns] = useState<readonly string[]>([]);
  const [find, setFind] = useState("");
  /** 足した直後の行。id の欄に焦点を移したら忘れる */
  const [focusKey, setFocusKey] = useState<string | undefined>(undefined);

  const { draft, open } = editing;
  const page = pageOf(data);
  /**
   * 案内の間は見本を描き、手順ごとの様子（開く項目・絞り込み・統合結果など）は `scene` に入れる。実際の編集中の配点や開いた行は動かさないので、
   * 閉じたあとに戻すものは無い
   */
  const [scene, setScene] = useState<Scene>(NO_SCENE);
  const tour = useTour(data.kind === "page", {
    onStart: () => setScene(NO_SCENE),
    onEnd: () => {
      setScene(NO_SCENE);
      post({ type: "tourDone" });
    },
  });
  const requestTour = tour.request;
  /** 拡張ホストへの送信。案内の間は実際の操作を送らない（`tourDone` と最初の `ready` は直に送る） */
  const act = (message: RiskMessage): void => {
    if (!tour.touring) {
      post(message);
    }
  };

  /**
   * 未保存の変更の有無が変わったら拡張ホストに伝える。同じ種類のタブは 1 枚で、別の対象を開くと
   * このタブの中身が入れ替わるので、拡張ホストはこれを見て「破棄して切り替える？」を聞く。
   * 送るのは変わったときだけ（最初の「変更なし」は拡張ホストも同じ前提で始まるので送らない）
   */
  const sentDirty = useRef(false);
  useEffect(() => {
    if (sentDirty.current !== dirty) {
      sentDirty.current = dirty;
      post({ type: "dirty", dirty });
    }
  }, [dirty]);

  useHostMessages<ToRisk>((message) => {
    if (message.type === "data" && message.data !== undefined) {
      const next = message.data;
      setData(next);
      setEditing(editingOf(next, nextKey));
      setDirty(false);
      setBusy(false);
      setStatus(undefined);
      setChanged(false);
      setSaveWarns([]);
      setLock(pageOf(next)?.lock ?? NO_LOCK);
    } else if (message.type === "saved") {
      setSaveWarns(message.warns ?? []);
    } else if (message.type === "failed") {
      setBusy(false);
      setStatus({ text: String(message.message ?? ""), error: true });
    } else if (message.type === "lock" && message.lock !== undefined) {
      setLock(message.lock);
    } else if (message.type === "changed") {
      setChanged(true);
    } else if (message.type === "cancelled") {
      // 「破棄して読み直す？」の確認をやめたので、止めていた欄を戻す
      setBusy(false);
      setStatus(undefined);
    } else if (message.type === "tour") {
      requestTour();
    }
  }, () => post({ type: "ready" }));

  // 足した行の id へ焦点を移す。折りたたんだままでは何を足したか分からないので、行は開いて出してある
  useEffect(() => {
    if (focusKey === undefined) {
      return;
    }
    const input = document.querySelector<HTMLInputElement>(`.factor[data-key="${focusKey}"] input.f-id`);
    input?.focus();
    setFocusKey(undefined);
  }, [focusKey]);

  /**
   * 読み直しを頼む。押した時点で欄を止める。 拡張ホストは実行ファイルに聞いてから中身を返す
   * ことがあり（設定ファイルの場所を解く）、その間に打った内容は、届いた中身で気づかないうちに消えるため。
   * ユーザが「破棄して読み直す？」をやめたときは `cancelled` が返り、欄が戻る。
   */
  const reload = (): void => {
    if (tour.touring) {
      return;
    }
    setBusy(true);
    setStatus(undefined);
    post({ type: "reload", dirty });
  };

  if (data.kind === "loading") {
    return (
      <p className="empty" id="ccnavi-loading">
        {data.text}
      </p>
    );
  }

  if (data.kind === "error") {
    return (
      <>
        <p className="empty">
          リスク管理画面を読み込めませんでした。原因を直してから「更新」を押してください。別の設定を選べば、このタブの中身がその設定に変わります。
        </p>
        <TargetSelect target={data.target} targets={data.targets} onSwitch={(kind, name) => post({ type: "switchTarget", kind, name })} />
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={() => post({ type: "reload", dirty: false })}>
          更新
        </button>
      </>
    );
  }

  const editDraft = (next: Draft, open?: ReadonlySet<string>): void => {
    if (tour.touring) {
      return;
    }
    setEditing((now) => ({ draft: next, open: open ?? now.open }));
    setDirty(true);
    setSaveWarns([]);
  };

  const editRow = (key: string, factor: FactorForm): void => {
    editDraft({ ...draft, rows: draft.rows.map((row) => (row.key === key ? { ...row, factor } : row)) });
  };

  const toggle = (key: string): void => {
    if (tour.touring) {
      return;
    }
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
    if (tour.touring) {
      return;
    }
    const row = { key: nextKey(), factor: emptyFactor() };
    editDraft({ ...draft, rows: [...draft.rows, row] }, new Set([...open, row.key]));
    setFocusKey(row.key);
  };

  const courses = coursesOf(TOUR, setScene);

  // 案内の間は、今の配点ではなく見本を描く。画面の state（編集中の配点・開いた行・絞り込み・状態の一言）は触らない
  const touring = tour.touring;
  const sampleMissing = scene.missing === true;
  const shownPage: RiskPage | undefined = touring ? (sampleMissing ? SAMPLE.missing : SAMPLE.page) : page;
  const shownExists = shownPage?.exists === true;
  /** 組み込みの配点を読み取り専用で見せている。欄は止め、「作る」だけができる */
  const shownReadOnly = shownPage?.builtin === true;
  const blank = touring ? LEVEL_NAMES.find((name) => name === scene.blank) : undefined;
  const sampleBase = sampleMissing ? SAMPLE_MISSING_DRAFT : SAMPLE_DRAFT;
  const shownDraft: Draft = !touring
    ? draft
    : blank === undefined
      ? sampleBase
      : { ...sampleBase, levels: { ...sampleBase.levels, [blank]: "" } as Readonly<Record<LevelName, string>> };
  const shownDirty = touring ? false : dirty;
  const shownBusy = touring ? false : busy;
  const shownLock: Lock = touring ? { locked: false, reason: "", doing: [] } : lock;
  const sceneOpen = new Set(sceneList(scene.open));
  const isShownOpen = (row: { readonly key: string; readonly factor: FactorForm }): boolean => (touring ? sceneOpen.has(row.factor.id) : open.has(row.key));
  const shownFind = touring ? (typeof scene.find === "string" ? scene.find : "") : find;

  const query = foldFind(shownFind.trim());
  const rows = shownDraft.rows.map((row) => {
    const text = findText(row.factor);
    return { ...row, find: text, hidden: query !== "" && !text.includes(query) };
  });
  const shown = rows.filter((row) => !row.hidden).length;
  const kept = rows.filter((row) => row.hidden && isShownOpen(row)).length;
  /** 案内が指す項目（開いている最初の行）。中の部品の `data-tour` はこの行にだけ付く */
  const tourRowKey = touring ? rows.find((row) => !row.hidden && isShownOpen(row))?.key : undefined;
  const sceneSum = touring && typeof scene.sum === "string" && scene.sum !== "" ? scene.sum : undefined;

  return (
    <>
      {(touring ? [] : page?.notices ?? []).map((notice, index) => (
        <div className="banner warn" key={index}>
          {notice}
        </div>
      ))}
      {(touring ? [] : saveWarns).map((warn, index) => (
        <div className="banner warn" data-banner="save-warn" key={`save-warn-${index}`}>
          <Code text={warn} />
        </div>
      ))}
      {touring ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      <div id="changed" className={changed && !touring ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。更新してください。
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={shownBusy} onClick={reload}>
          更新
        </button>
      </div>
      <ScreenHeader courses={courses} onPick={tour.start}>
        <div className="summary">
          <TargetSelect
            target={touring ? undefined : page?.target}
            targets={touring ? undefined : page?.targets}
            disabled={shownBusy}
            onSwitch={(kind, name) => act({ type: "switchTarget", kind, name })}
          />
          <span className="path" title={touring ? "" : page?.root ?? ""}>
            {touring ? `（見本）${shownPage?.riskPath ?? ""}` : page?.riskPath ?? ""}
          </span>
          <span id="dirty" className={shownDirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <HandoffLink entry={touring ? undefined : page?.handoff} disabled={touring} onOpen={() => act({ type: "openHandoff" })} />
          <button type="button" className="action" data-action="open-risk" disabled={!shownExists} onClick={() => act({ type: "openFile" })}>
            エディタで開く
          </button>
          <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={shownBusy} onClick={reload}>
            更新
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-action="save"
            data-tour="save"
            disabled={!shownDirty || shownLock.locked || shownBusy || shownReadOnly}
            onClick={() => {
              if (tour.touring) {
                return;
              }
              setBusy(true);
              setStatus({ text: "検証して保存中…", error: false });
              post({ type: "save", form: formOf(draft) });
            }}
          >
            保存
          </button>
        </div>
      </ScreenHeader>
      <p id="lock" className={lockClass(shownLock)}>
        {shownLock.reason}
      </p>
      {!touring && page !== undefined && page.model.problems.length > 0 && (
        <ul className="problems">
          {page.model.problems.map((problem, index) => (
            <li key={index}>
              <Code text={problem} />
            </li>
          ))}
        </ul>
      )}
      {shownPage !== undefined && shownReadOnly && (
        <div className="banner missing" data-tour="missing">
          <span>{shownPage.riskPath} がありません。実行ファイルは組み込みの配点で数えています（画面の値はその組み込みの配点です）。配点を直すには、まずファイルを作ってください。</span>
          <button
            type="button"
            className="action primary"
            data-action="create"
            data-tour="create"
            disabled={shownBusy}
            onClick={() => {
              if (tour.touring) {
                return;
              }
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
          リスクレベルの境目の点 <span className="count">リスクの合計点がしきい値以上になると、リスクレベルが上がります。HIGH 以上の場合は、次のフェーズに進む前にユーザのレビューが必須になります
          </span>
        </h2>
        <details className="help">
          <summary>説明</summary>
          <p className="hint">
            子チケットを閉じるとき、下の「項目」で該当した点を合計して、その変更のリスクの点を出します。
            合計点がしきい値以上になると、リスクレベルが LOW → MEDIUM → HIGH → CRITICAL の順に上がっていきます。
            <strong>HIGH 以上になったフェーズは、レビューが終わるまで先へ進めません。</strong>
            チケット作成時に「レビュー不要」と宣言していても、ユーザのレビューが必要になります。
            値は MEDIUM ≤ HIGH ≤ CRITICAL となるように入れてください。空欄にしたリスクレベルは、組み込みの値（
            {LEVEL_NAMES.map((name) => `${name} ${BUILTIN_LEVELS[name]}`).join(" / ")}）を使います。
          </p>
        </details>
        <div className="levels" id="levels" data-tour="levels">
          {LEVEL_NAMES.map((name) => (
            <Captioned key={name} name={name.toUpperCase()} yamlKey={`levels.${name}`} data-tour={name === "high" ? "level-high" : undefined}>
              <input
                type="text"
                className="f-level"
                spellCheck={false}
                inputMode="numeric"
                title={`${name.toUpperCase()} 以上になる点`}
                placeholder={`既定 ${BUILTIN_LEVELS[name]}`}
                value={shownDraft.levels[name]}
                disabled={shownBusy || shownReadOnly}
                onChange={(event) => editDraft({ ...draft, levels: { ...draft.levels, [name]: event.target.value } as Readonly<Record<LevelName, string>> })}
              />
            </Captioned>
          ))}
        </div>
      </section>
      <section className="block">
        <h2>
          項目{" "}
          <span className="count" id="factor-count" data-tour="factor-count">
            {countText(shownDraft.rows.length, query, shown, kept)}
          </span>
          <button type="button" className="action small" data-action="add" data-tour="add" disabled={shownBusy || shownReadOnly} onClick={add}>
            ＋ 項目を追加
          </button>
        </h2>
        <div className="find">
          <input
            id="find"
            data-tour="find"
            type="search"
            placeholder="id・加点条件・値・理由で絞り込む"
            spellCheck={false}
            value={shownFind}
            onChange={(event) => {
              if (!tour.touring) {
                setFind(event.target.value);
              }
            }}
          />
        </div>
        <details className="help">
          <summary>説明</summary>
          <p className="hint">
            子チケットの完了時、その子の差分（base_sha..HEAD）で判定して加点します。1 件につき加点条件は 1 つです。
            <code>script</code> が失敗したときと出力を読み込めないときは、安全を優先して points をそのまま加点します。<code>judge</code> の項目があると、判定が揃うまで子を閉じられません。
          </p>
        </details>
        <ul className="list" id="factors" data-tour="factors">
          {rows.map((row, index) => (
            <Factor
              key={row.key}
              factorKey={row.key}
              factor={row.factor}
              find={row.find}
              hidden={row.hidden}
              open={isShownOpen(row)}
              disabled={shownBusy || shownReadOnly}
              tour={row.key === tourRowKey}
              onToggle={() => toggle(row.key)}
              onChange={(factor) => editRow(row.key, factor)}
              finding={query !== ""}
              first={index === 0}
              last={index === rows.length - 1}
              onMove={(delta) => move(row.key, delta)}
              onRemove={() => remove(row.key)}
            />
          ))}
          {rows.length > 0 && shown + kept === 0 && <li className="empty">絞り込みにヒットする項目がありません。語を変えるか、絞り込み欄を空にしてください</li>}
          {shownDraft.rows.length === 0 && <li className="empty">項目がありません。加点する項目がなければ、どの子も LOW のまま閉じられます</li>}
        </ul>
      </section>
      {shownPage?.sums !== undefined && (
        <Sum
          sums={shownPage.sums}
          initial={defaultSum(shownPage.sums, touring ? undefined : shownPage.target)}
          // 見本は別の枠として作る（閉じたあと、見本で選んだ統合結果が実際の枠に残らない）
          key={touring ? "tour" : `${shownPage.target?.kind ?? ""}:${shownPage.target?.name ?? ""}`}
          tour={touring ? { open: sceneSum !== undefined, picked: sceneSum ?? shownPage.sums[0]?.name ?? "" } : undefined}
        />
      )}
      {tour.touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
      <footer className={!touring && status?.error === true ? "foot error" : "foot"}>
        <span id="status">
          <Code text={touring ? "" : (status?.text ?? "")} />
        </span>
      </footer>
    </>
  );
}
