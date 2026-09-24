/**
 * ルール設定画面の本体。ルールの一覧・判定を試す・hook の 3 タブ。
 *
 * 見せる中身は拡張ホストが渡す（`RulesData`）。画面が持つのは、人が触って決めるもの
 * （編集中のルール、開いている行、畳んだタイプ、絞り込み、開いているタブ、直前の操作の一言）だけ。
 * **判定はしない。** 「判定」も「サンプルを一括で判定」も、編集中の内容を拡張ホストへ渡し、
 * 実行ファイルが返した結果を出すだけ（ADR-0035）。
 *
 * **中身（`data`）が届いたら、編集中のルールはその中身で置き換える。** 届くのは編集を捨ててよい
 * ときだけ（人が「再読込」を押した、保存が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ（ADR-0062）。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";

import type { Lock } from "../../core/lock.js";
import { KNOWN_TOOLS, SECTIONS, SECTION_LABELS, type FileField, type RuleForm, type RulesData, type RulesPage, type Section, type ToRules } from "../../core/rules-view.js";
import type { SamplesJson } from "../../core/testmodel.js";
import { applyAppearance } from "../appearance.js";
import { Tour, useTour, type TourStep } from "../Tour.js";
import { Hooks } from "./Hooks.js";
import { JudgeResult, SamplesResult, type Judged } from "./Judge.js";
import { post } from "./post.js";
import { Rule } from "./Rule.js";
import {
  draftOf,
  EMPTY_DRAFT,
  emptyRule,
  findRow,
  keyer,
  loadOpen,
  loadTab,
  openedFromIds,
  saveOpen,
  saveTab,
  sectionsOf,
  type Draft,
  type TabName,
} from "./state.js";
import { countText, findText, hasContext } from "./text.js";

/** 中身が読めなかったときの錠。画面は保存させない（押せる形で出して落とさない） */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

/** 直前の操作の一言。生きている画面にしか届かないので持ち越さない */
interface Status {
  readonly text: string;
  readonly error: boolean;
}

interface Editing {
  readonly draft: Draft;
  /** 人が開いた行の鍵。控え（state）に入るのはこちらだけ */
  readonly open: ReadonlySet<string>;
}

function pageOf(data: RulesData): RulesPage | undefined {
  return data.kind === "page" ? data.page : undefined;
}

function editingOf(data: RulesData, nextKey: () => string): Editing {
  const page = pageOf(data);
  const draft = page === undefined ? EMPTY_DRAFT : draftOf(page.model.sections, nextKey);
  return { draft, open: openedFromIds(draft, loadOpen()) };
}

export function App({ initial }: { readonly initial: RulesData }): JSX.Element {
  // 鍵は 1 枚の画面の中で数え上げる。描き直しで配り直さない
  const nextKey = useRef(keyer()).current;
  const [data, setData] = useState<RulesData>(initial);
  const [editing, setEditing] = useState<Editing>(() => editingOf(initial, nextKey));
  /**
   * 判定で当たってその場だけ開いた行。**控えには入れない**（判定を繰り返しても、人が決めた
   * 既定の畳みが崩れない）。次の判定で入れ替わる。
   */
  const [transient, setTransient] = useState<ReadonlySet<string>>(new Set());
  /** 直前の判定で当たったルールの id。畳んだままでも分かるように縁を付ける */
  const [hits, setHits] = useState<ReadonlySet<string>>(new Set());
  /** 「コンテキストの追加」の開閉。最初は値の有無で決め、以後は人の操作を鍵で覚える */
  const [moreOpen, setMoreOpen] = useState<ReadonlyMap<string, boolean>>(new Map());
  const [folded, setFolded] = useState<ReadonlySet<Section>>(new Set());
  const [dirty, setDirty] = useState(false);
  /** 実行ファイルへの往復の間。判定・サンプル・再読込・保存のボタンを止める */
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<Lock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  /** ファイルが外で変わった。破棄して読み直すかは人が決める */
  const [changed, setChanged] = useState(false);
  const [find, setFind] = useState("");
  const [tab, setTab] = useState<TabName>(() => loadTab());
  const [judged, setJudged] = useState<Judged | undefined>(undefined);
  const [sampled, setSampled] = useState<SamplesJson | undefined>(undefined);
  const [tool, setTool] = useState<string>(KNOWN_TOOLS[0]);
  const [subject, setSubject] = useState("");
  /** 選択肢が開いている行。開くのは画面ぜんたいで 1 つだけ */
  const [pickerKey, setPickerKey] = useState<string | undefined>(undefined);
  /** 足した直後の行。id の欄に焦点を移したら忘れる */
  const [focusKey, setFocusKey] = useState<string | undefined>(undefined);

  const { draft, open } = editing;
  const page = pageOf(data);

  /**
   * 案内はタブを切り替えて中を指すので、始める前のタブを控え、閉じたら戻す。**案内の間の切り替えは
   * 控え（`saveTab`）に書かない**（途中でタブを閉じたときに、次から別のタブで開く、ということを起こさない）
   */
  const tabBeforeTour = useRef<TabName | undefined>(undefined);
  const tour = useTour(data.kind === "page", {
    onStart: () => {
      tabBeforeTour.current = tab;
    },
    onEnd: () => {
      const was = tabBeforeTour.current;
      tabBeforeTour.current = undefined;
      if (was !== undefined) {
        setTab(was);
      }
      post({ type: "tourDone" });
    },
  });
  const requestTour = tour.request;

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

  /**
   * id を打っている途中は控えを書き直さない（打ちかけの id が控えに入る）。書くのは欄を
   * 確定した（native の `change`）ときだけ。React の `onChange` は打つたびに呼ばれるので、
   * ここは素の DOM の口で受ける。いまの編集は描き直しのたびに `latest` へ写す
   * （`useLayoutEffect` は描き直しと同じ順番で走るので、確定が届いた時点では今の編集が入っている）。
   */
  const latest = useRef<Editing>(editing);
  useLayoutEffect(() => {
    latest.current = editing;
  });
  /**
   * 受け口は一覧そのものに張る。`useEffect` で 1 度だけ張ると、**読み直せなかった画面
   * （`kind: "error"`）から始まったときは一覧がまだ無く、あとで中身が届いても張られない。**
   * ref のコールバックなら、一覧が出た時点で張り、消えた時点で外れる。
   */
  const list = useCallback((element: HTMLElement | null): (() => void) | undefined => {
    if (element === null) {
      return undefined;
    }
    const onCommit = (event: Event): void => {
      const target = event.target as HTMLElement | null;
      if (target?.classList.contains("f-id") === true) {
        saveOpen(latest.current.draft, latest.current.open);
      }
    };
    element.addEventListener("change", onCommit);
    return () => element.removeEventListener("change", onCommit);
  }, []);

  useEffect(() => {
    const onMessage = (event: MessageEvent): void => {
      const message = (event.data ?? {}) as Partial<ToRules> & Record<string, unknown>;
      if (message.type === "data" && message.data !== undefined) {
        const next = message.data as RulesData;
        setData(next);
        setEditing(editingOf(next, nextKey));
        setTransient(new Set());
        setHits(new Set());
        setMoreOpen(new Map());
        setDirty(false);
        setBusy(false);
        setStatus(undefined);
        setChanged(false);
        setLock(pageOf(next)?.lock ?? NO_LOCK);
        if (next.kind === "loading") {
          // 別の対象へ切り替わった。前の対象のルールで出した判定を、いまのルールの結果として残さない
          setJudged(undefined);
          setSampled(undefined);
        }
      } else if (message.type === "judged") {
        const result = message.result as Judged["result"];
        setBusy(false);
        setStatus(undefined);
        setJudged({ result, hooks: (message.hooks ?? []) as Judged["hooks"] });
        unfoldHits(result.rules.map((rule) => rule.id));
        showTab("judge");
      } else if (message.type === "sampled") {
        setBusy(false);
        setStatus(undefined);
        setSampled(message.result as SamplesJson);
        showTab("judge");
      } else if (message.type === "failed") {
        setBusy(false);
        setStatus({ text: String(message.message ?? ""), error: true });
      } else if (message.type === "lock" && message.lock !== undefined) {
        setLock(message.lock as Lock);
      } else if (message.type === "changed") {
        setChanged(true);
      } else if (message.type === "cancelled") {
        // 「破棄して読み直す？」をやめた。止めたボタンを戻す
        setBusy(false);
        setStatus(undefined);
      } else if (message.type === "picked") {
        pick(String(message.key), message.field as FileField, String(message.path));
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
    // 受け口は 1 度だけ張る。中身は setState の更新関数で今の値から作る
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nextKey]);

  // 選択肢は、その欄と選択肢の外を押したとき、または Esc で閉じる
  useEffect(() => {
    const onDown = (event: Event): void => {
      const target = event.target as HTMLElement | null;
      if (target === null || target.closest(".picker.open") === null) {
        setPickerKey(undefined);
      }
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setPickerKey(undefined);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  /**
   * 判定で当たった行を、畳んであってもその場だけ開く。見えないところで光っても分からないので、
   * タイプの畳みも外す。**控えには入れない**ので、次の判定で元の畳みに戻る。
   */
  const unfoldHits = (ids: readonly string[]): void => {
    const wanted = new Set(ids);
    const keys = new Set<string>();
    const unfold = new Set<Section>();
    for (const section of SECTIONS) {
      for (const row of latest.current.draft[section]) {
        if (row.rule.id !== "" && wanted.has(row.rule.id)) {
          keys.add(row.key);
          unfold.add(section);
        }
      }
    }
    setHits(wanted);
    setTransient(keys);
    setFolded((now) => new Set([...now].filter((section) => !unfold.has(section))));
  };

  // 足した行の id へ焦点を移す。移したら忘れる（同じ行を描き直すたびに焦点を奪わない）
  useEffect(() => {
    if (focusKey === undefined) {
      return;
    }
    document.querySelector<HTMLInputElement>(`.rule[data-key="${focusKey}"] input.f-id`)?.focus();
    setFocusKey(undefined);
  }, [focusKey]);

  const showTab = (name: TabName): void => {
    setTab(name);
    saveTab(name);
  };

  const pick = (key: string, field: FileField, path: string): void => {
    // ダイアログを開いている間に行を消せる。行が無ければ欄も「未保存」も動かさない
    const found = findRow(latest.current.draft, key);
    if (found === undefined) {
      return;
    }
    setEditing((now) => ({ ...now, draft: replace(now.draft, found.section, key, { ...found.row.rule, [field]: path }) }));
    setDirty(true);
  };

  /**
   * 読み直しを頼む。**押した時点でボタンを止める。** 拡張ホストは実行ファイルに聞いてから中身を
   * 返すことがあり（層の置き場を解く）、その間に押し直せると往復が重なる。人が
   * 「破棄して読み直す？」をやめたときは `cancelled` が返り、ボタンが戻る。
   */
  const reload = (): void => {
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
          ルール設定画面を読み直せなかった。原因を直してから「再読込」を押す（同じ対象を開き直しても前面に出るだけ。別の対象を開けば、このタブの中身がその対象に替わる）。
        </p>
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
          再読込
        </button>
      </>
    );
  }

  const isOpen = (key: string): boolean => open.has(key) || transient.has(key);

  const editDraft = (next: Draft, opened?: ReadonlySet<string>): void => {
    setEditing((now) => ({ draft: next, open: opened ?? now.open }));
    setDirty(true);
  };

  const editRule = (section: Section, key: string, rule: RuleForm): void => {
    editDraft(replace(draft, section, key, rule));
  };

  const toggle = (key: string): void => {
    const next = new Set(open);
    // その場だけ開いていた行も「開いている」。人が押したらそこから閉じる
    if (isOpen(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    setTransient(withoutKey(transient, key));
    setEditing((now) => ({ ...now, open: next }));
    saveOpen(draft, next);
  };

  const move = (section: Section, key: string, delta: number): void => {
    const rows = draft[section].slice();
    const from = rows.findIndex((row) => row.key === key);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= rows.length) {
      return;
    }
    const moved = rows[from];
    rows[from] = rows[to];
    rows[to] = moved;
    editDraft({ ...draft, [section]: rows });
  };

  /** タイプを移す。移した先の末尾に置く */
  const moveSection = (from: Section, key: string, to: Section): void => {
    if (from === to) {
      return;
    }
    const row = draft[from].find((item) => item.key === key);
    if (row === undefined) {
      return;
    }
    editDraft({ ...draft, [from]: draft[from].filter((item) => item.key !== key), [to]: [...draft[to], row] });
  };

  const remove = (section: Section, key: string): void => {
    editDraft({ ...draft, [section]: draft[section].filter((row) => row.key !== key) });
  };

  const add = (section: Section): void => {
    const row = { key: nextKey(), rule: emptyRule() };
    // 足したルールは開いて出す。畳んだままでは何を足したか分からない
    editDraft({ ...draft, [section]: [...draft[section], row] }, new Set([...open, row.key]));
    setFolded(without(folded, section));
    setFocusKey(row.key);
  };

  const fold = (section: Section): void => {
    setFolded(folded.has(section) ? without(folded, section) : new Set([...folded, section]));
  };

  const judge = (): void => {
    if (subject.trim() === "") {
      setStatus({ text: "対象を入れる", error: true });
      return;
    }
    setBusy(true);
    setStatus({ text: "判定中…", error: false });
    post({ type: "judge", sections: sectionsOf(draft), tool, subject });
  };

  const query = find.trim().toLowerCase();

  return (
    <>
      {page !== undefined && page.mode !== "enable" && page.mode !== "" && (
        // 未設定は実行ファイルが enable として扱う（ccnavi/modes.py「どこにも値が無ければ enable」）ので帯は出さない
        <div className="banner warn">
          現在の <code>CCNAVI_MODE</code>: <strong>{page.mode}</strong>。判定と記録はするが、deny や ask にヒットしてもツールの呼び出し（tool_use）を止めない
        </div>
      )}
      {(page?.notices ?? []).map((notice, index) => (
        <div className="banner warn" key={index}>
          {notice}
        </div>
      ))}
      <div id="changed" className={changed ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。再読込してください。
        <button type="button" className="action" data-action="reload" disabled={busy} onClick={reload}>
          再読込
        </button>
      </div>
      <header className="toolbar">
        <div className="summary">
          <span className="path" title={page?.root ?? ""}>
            {page?.rulesPath ?? ""}
          </span>
          <span id="dirty" className={dirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <button type="button" className="action" data-action="tour" title="この画面の案内をもう一度見る" onClick={tour.start}>
            ？ 案内
          </button>
          <button type="button" className="action" data-action="open-rules" onClick={() => post({ type: "openFile", which: "rules" })}>
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
            disabled={!dirty || lock.locked || busy}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "検証して保存中…", error: false });
              post({ type: "save", sections: sectionsOf(draft) });
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
      <nav className="tabs" role="tablist">
        {([
          ["rules", "ルール"],
          ["judge", "判定を試す"],
          ["hooks", "hook"],
        ] as const).map(([name, label]) => (
          <button key={name} type="button" className={tab === name ? "tab active" : "tab"} data-tab={name} role="tab" onClick={() => showTab(name)}>
            {label}
          </button>
        ))}
      </nav>
      <section id="tab-rules" className={`pane${tab === "rules" ? " active" : ""}${query === "" ? "" : " finding"}`} ref={list}>
        <div className="find">
          <input id="find" type="search" placeholder="id・ツール・パターン・文面で絞り込む" spellCheck={false} value={find} onChange={(event) => setFind(event.target.value)} />
          <span className="hint">判定は強い順に deny &gt; ask &gt; allow</span>
        </div>
        {SECTIONS.map((section) => {
          const rows = draft[section].map((row) => {
            const text = findText(row.rule);
            return { ...row, find: text, hidden: query !== "" && !text.includes(query) };
          });
          const shown = rows.filter((row) => !row.hidden).length;
          const kept = rows.filter((row) => row.hidden && isOpen(row.key)).length;
          // 絞り込み中は畳んだタイプの中も見えるので、矢印も開いた向きにする
          const shownAsOpen = query !== "" || !folded.has(section);
          return (
            <section className={folded.has(section) ? "rule-section folded" : "rule-section"} data-section={section} key={section}>
              <h2>
                <button
                  type="button"
                  className="twist"
                  data-action="fold-section"
                  data-section={section}
                  aria-expanded={shownAsOpen}
                  title="このタイプを開く／畳む"
                  onClick={() => fold(section)}
                >
                  {shownAsOpen ? "▾" : "▸"}
                </button>{" "}
                <span className={`section-name ${section}`}>{section}</span> <span className="section-label">{SECTION_LABELS[section]}</span>{" "}
                <span className="count" data-count={section}>
                  {countText(query, shown, draft[section].length, kept)}
                </span>
                <button type="button" className="action small" data-action="add" data-section={section} onClick={() => add(section)}>
                  ＋ ルールを追加
                </button>
              </h2>
              <ul className="list" data-list={section}>
                {rows.map((row) => (
                  <Rule
                    key={row.key}
                    ruleKey={row.key}
                    section={section}
                    rule={row.rule}
                    find={row.find}
                    hidden={row.hidden}
                    open={isOpen(row.key)}
                    hit={row.rule.id !== "" && hits.has(row.rule.id)}
                    pickerOpen={pickerKey === row.key}
                    moreOpen={moreOpen.get(row.key) ?? hasContext(row.rule)}
                    onToggle={() => toggle(row.key)}
                    onChange={(rule) => editRule(section, row.key, rule)}
                    onMoveSection={(to) => moveSection(section, row.key, to)}
                    onMove={(delta) => move(section, row.key, delta)}
                    onRemove={() => remove(section, row.key)}
                    onOpenPicker={() => setPickerKey(row.key)}
                    onPickFile={(field) => post({ type: "pickFile", key: row.key, field })}
                    onToggleMore={(value) => setMoreOpen(new Map([...moreOpen, [row.key, value]]))}
                  />
                ))}
              </ul>
            </section>
          );
        })}
      </section>
      <section id="tab-judge" className={tab === "judge" ? "pane active" : "pane"}>
        <p className="hint">
          判定は実行ファイルの <code>--test</code> で行う。編集中の内容で試すので保存は要らない。セッションが dry-run でも、ここは enable のときの判定を返す。
        </p>
        <div className="judge-form">
          <label>
            ツール{" "}
            <select id="tool" value={tool} onChange={(event) => setTool(event.target.value)}>
              {KNOWN_TOOLS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="grow" title="--test の subject">
            対象{" "}
            <input
              id="subject"
              type="text"
              placeholder="Bash / PowerShell ならコマンド、Read / Grep / Glob / Edit / Write なら絶対パス、Skill ならスキル名、Agent なら見出し、WebFetch なら URL"
              spellCheck={false}
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  judge();
                }
              }}
            />
          </label>
          <button type="button" className="action primary" data-action="judge" disabled={busy} onClick={judge}>
            判定
          </button>
        </div>
        {judged !== undefined && <JudgeResult judged={judged} />}
        <div className="samples-head">
          <button
            type="button"
            className="action"
            data-action="samples"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "サンプルを判定中…", error: false });
              post({ type: "samples", sections: sectionsOf(draft) });
            }}
          >
            サンプルを一括で判定
          </button>
          <span className="path">{page?.samplesPath ?? ""}</span>
          <button type="button" className="action" data-action="open-samples" onClick={() => post({ type: "openFile", which: "samples" })}>
            エディタで開く
          </button>
        </div>
        {sampled !== undefined && <SamplesResult result={sampled} />}
      </section>
      <section id="tab-hooks" className={tab === "hooks" ? "pane active" : "pane"}>
        <Hooks hooks={page?.hooks ?? []} files={page?.hookFiles ?? { settings: false, settingsLocal: false }} />
      </section>
      {tour.touring && <Tour steps={tourSteps(setTab, () => tabBeforeTour.current ?? tab)} onClose={tour.end} />}
      <footer className={status?.error === true ? "foot error" : "foot"}>
        <span id="status">{status?.text ?? ""}</span>
      </footer>
    </>
  );
}

/**
 * ルール設定画面の案内。`peek` は案内の間だけのタブの切り替え（控えに書かない）、`before` は始める前のタブ。
 * 最後の段に入る前に始める前のタブへ戻す（「？ 案内」はどのタブにも出ている）
 */
function tourSteps(peek: (tab: TabName) => void, before: () => TabName): readonly TourStep[] {
  return [
    {
      target: ".tabs",
      title: "3 つのタブ",
      body: "「ルール」で deny・ask・allow のルールを直し、「判定を試す」で編集中の内容がどう判定するかを確かめ、「hook」で登録されている hook を眺める。",
      before: () => peek("rules"),
    },
    {
      target: "#tab-rules",
      title: "ルール",
      body: "deny（止める）・ask（確かめる）・allow（通す）のタイプごとに並ぶ。判定は強い順に deny > ask > allow。行を押すと欄が開き、「＋ ルールを追加」で足せる。見出しの ▾ でタイプを畳める。",
      before: () => peek("rules"),
    },
    {
      target: "#find",
      title: "絞り込み",
      body: "id・ツール・パターン・文面で絞り込む。畳んだタイプの中も探す。",
      before: () => peek("rules"),
    },
    {
      target: "#tab-judge .judge-form",
      title: "判定を試す",
      body: "ツールと対象（コマンドやパス）を入れて「判定」を押すと、編集中の内容でどのルールに当たるかを実行ファイルが返す。保存は要らない。「サンプルを一括で判定」は、サンプルのファイルに並べた例をまとめて確かめる。",
      before: () => peek("judge"),
    },
    {
      target: "#tab-hooks",
      title: "hook",
      body: ".claude/settings.json に登録された hook の一覧。直すときは settings.json を開いて編集する。",
      before: () => peek("hooks"),
    },
    {
      target: "#save",
      title: "保存",
      body: "保存すると実行ファイルが検証してから書き込む。通らなければ、下に理由が出る。ファイルが外で変わったときは上に帯が出るので、再読込する。",
      before: () => peek(before()),
    },
    {
      target: '[data-action="tour"]',
      title: "案内",
      body: "この案内は、ここからもう一度見られる。",
      before: () => peek(before()),
    },
  ];
}

function replace(draft: Draft, section: Section, key: string, rule: RuleForm): Draft {
  return { ...draft, [section]: draft[section].map((row) => (row.key === key ? { ...row, rule } : row)) };
}

function without(set: ReadonlySet<Section>, section: Section): ReadonlySet<Section> {
  const next = new Set(set);
  next.delete(section);
  return next;
}

function withoutKey(set: ReadonlySet<string>, key: string): ReadonlySet<string> {
  const next = new Set(set);
  next.delete(key);
  return next;
}
