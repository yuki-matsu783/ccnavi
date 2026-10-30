/**
 * ルール管理画面の本体。ルールの一覧・判定を試す・hook の 3 タブ。
 *
 * 見せる中身は拡張ホストが渡す（`RulesData`）。画面が持つのは、ユーザが触って決めるもの
 * （編集中のルール、開いている行、折りたたんだタイプ、絞り込み、開いているタブ、直前の操作の一言）だけ。
 * 「判定」も「期待する判定を一括で確かめる」も、編集中の内容を拡張ホストへ渡し、
 * 実行ファイルが返した結果を出すだけ。
 *
 * 中身（`data`）が届いたら、編集中のルールはその中身で置き換える。 届くのは編集を破棄してよい
 * ときだけ（ユーザが「再読み込み」を押した、保存が通った）で、ファイルが外で変わっただけのときは
 * 帯（`changed`）が出るだけ。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";

import { lockClass, type Lock } from "../../core/lock.js";
import { isToolName, KNOWN_TOOLS, SECTIONS, SECTION_LABELS, bareRuleId, modeBanner, tmpName, type FileField, type RuleForm, type RulesData, type RulesPage, type Section, type ToRules } from "../../core/rules-view.js";
import type { SuggestJson } from "../../core/suggestmodel.js";
import type { ExpectationsJson } from "../../core/testmodel.js";
import { ESCAPE_LAYER, useEscape } from "../useEscape.js";
import { useHostMessages } from "../useHostMessages.js";
import { courseOf, coursesOf, ScreenHeader, Tour, useTour } from "../Tour.js";
import { SAMPLE_NOTE, tourOf, type Scene } from "../../core/tour-def.js";
import { parseRulesSample } from "../../core/tour-rules.js";
import rulesTour from "../tours/rules.yml";
import { Hooks } from "./Hooks.js";
import { ExpectationsResult, JudgeResult, SuggestResult, type Judged } from "./Judge.js";
import { TargetSelect } from "../TargetSelect.js";
import { HandoffLink } from "../shared/HandoffLink.js";
import { defaultSum } from "../../core/sums.js";
import { Sum } from "./Sum.js";
import { mutePosts, post } from "./post.js";
import { Code } from "../shared/Code.js";
import { foldFind } from "../shared/find.js";
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

/**
 * 案内（`tours/rules.yml`）と、案内の間だけ描く見本。今の設定は使わず、画面が持つ編集中の内容にも触れない。
 * 読み込み時に形を確かめる（壊れていればテストが先に落とす）。
 */
const TOUR = tourOf(rulesTour);
const SAMPLE = parseRulesSample(TOUR.sample);
/** 見本の行のキー。実際の行のキー（`r1`…）と重ならない */
const SAMPLE_DRAFT: Draft = draftOf(SAMPLE.sections, ((): (() => string) => {
  let seq = 0;
  return () => `s${(seq += 1)}`;
})());
const NO_SCENE: Scene = {};

/** 手順の `scene` の文字列を `,` で割る */
function sceneList(value: Scene[string] | undefined): readonly string[] {
  return typeof value === "string" && value !== "" ? value.split(",").map((item) => item.trim()) : [];
}

/** 中身が読めなかったときの錠。画面は保存させない（押せる形で出して異常終了させない） */
const NO_LOCK: Lock = { locked: true, reason: "", doing: [] };

/** 直前の操作の一言。生きている画面にしか届かないので持ち越さない */
interface Status {
  readonly text: string;
  readonly error: boolean;
}

interface Editing {
  readonly draft: Draft;
  /** ユーザが開いた行のキー。state に入るのはこちらだけ */
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
  // キーは 1 枚の画面の中で数え上げる。描き直しで配り直さない
  const nextKey = useRef(keyer()).current;
  const [data, setData] = useState<RulesData>(initial);
  const [editing, setEditing] = useState<Editing>(() => editingOf(initial, nextKey));
  /**
   * 判定でヒットしてその場だけ開いた行。state には入れない（判定を繰り返しても、ユーザが決めた
   * 既定の折りたたみが変わらない）。次の判定で入れ替わる。
   */
  const [transient, setTransient] = useState<ReadonlySet<string>>(new Set());
  /** 直前の判定でヒットしたルールの id。折りたたんだままでも分かるように縁を付ける */
  const [hits, setHits] = useState<ReadonlySet<string>>(new Set());
  /** 「コンテキストの追加」の開閉。最初は値の有無で決め、以後はユーザの操作をキーで覚える */
  const [moreOpen, setMoreOpen] = useState<ReadonlyMap<string, boolean>>(new Map());
  const [folded, setFolded] = useState<ReadonlySet<Section>>(new Set());
  const [dirty, setDirty] = useState(false);
  /** 実行ファイルへの往復の間。判定・期待する判定の確認・再読み込み・保存のボタンを止める */
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | undefined>(undefined);
  const [lock, setLock] = useState<Lock>(() => pageOf(initial)?.lock ?? NO_LOCK);
  /** ファイルが外で変わった。破棄して読み直すかはユーザが決める */
  const [changed, setChanged] = useState(false);
  /** 保存が通ったときの検証の warn の本文。帯に出し、次の編集か更新（中身の入れ替え）で消す */
  const [saveWarns, setSaveWarns] = useState<readonly string[]>([]);
  const [find, setFind] = useState("");
  const [tab, setTab] = useState<TabName>(() => loadTab());
  const [judged, setJudged] = useState<Judged | undefined>(undefined);
  const [checked, setChecked] = useState<ExpectationsJson | undefined>(undefined);
  const [suggested, setSuggested] = useState<SuggestJson | undefined>(undefined);
  const [tool, setTool] = useState<string>(KNOWN_TOOLS[0]);
  const [subject, setSubject] = useState("");
  /** 選択肢が開いている行。開くのは画面ぜんたいで 1 つだけ */
  const [pickerKey, setPickerKey] = useState<string | undefined>(undefined);
  /** 足した直後の行。id の欄に焦点を移したら忘れる */
  const [focusKey, setFocusKey] = useState<string | undefined>(undefined);

  const { draft, open } = editing;
  const page = pageOf(data);

  /**
   * 案内の間は見本を描き、手順ごとの様子（開く行・タブ・選ぶ点など）は `scene` に入れる。実際のタブや開いた行は動かさないので、
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
  // 案内の間は、見本の上の操作を拡張ホストへ送らない（`tourDone` だけ返す）。描いたあとに切り替える
  const touringNow = tour.touring;
  useLayoutEffect(() => {
    mutePosts(touringNow);
    return () => mutePosts(false);
  }, [touringNow]);

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
   * id を打っている途中は state を書き直さない（打ちかけの id が state に入る）。書くのは欄を
   * 確定した（native の `change`）ときだけ。React の `onChange` は打つたびに呼ばれるので、
   * ここは素の DOM のイベントで受ける。いまの編集は描き直しのたびに `latest` へ入れる
   * （`useLayoutEffect` は描き直しと同じ順番で走るので、確定が届いた時点では今の編集が入っている）。
   */
  const latest = useRef<Editing>(editing);
  useLayoutEffect(() => {
    latest.current = editing;
  });
  /**
   * 受け取る側は一覧そのものに張る。`useEffect` で 1 度だけ張ると、読み直せなかった画面
   * （`kind: "error"`）から始まったときは一覧がまだ無く、あとで中身が届いても張られない。
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

  useHostMessages<ToRules>((message) => {
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
      setSaveWarns([]);
      setLock(pageOf(next)?.lock ?? NO_LOCK);
      if (next.kind === "loading") {
        // 別の対象へ切り替わった。前の対象のルールで出た判定を、いまのルールの結果として残さない
        setJudged(undefined);
        setChecked(undefined);
        setSuggested(undefined);
      }
    } else if (message.type === "judged") {
      const result = message.result as Judged["result"];
      setBusy(false);
      setStatus(undefined);
      setJudged({ result, hooks: (message.hooks ?? []) as Judged["hooks"] });
      unfoldHits(result.rules.map((rule) => bareRuleId(rule.id)));
      showTab("judge");
    } else if (message.type === "expectationsChecked") {
      setBusy(false);
      setStatus(undefined);
      setChecked(message.result as ExpectationsJson);
      showTab("judge");
    } else if (message.type === "suggested") {
      setBusy(false);
      setStatus(undefined);
      setSuggested(message.result as SuggestJson);
      showTab("judge");
    } else if (message.type === "failed") {
      setBusy(false);
      setStatus({ text: tmpName(String(message.message ?? "")), error: true });
    } else if (message.type === "notice") {
      setBusy(false);
      setStatus({ text: String(message.message ?? ""), error: false });
    } else if (message.type === "lock" && message.lock !== undefined) {
      setLock(message.lock as Lock);
    } else if (message.type === "saved") {
      setSaveWarns(message.warns ?? []);
    } else if (message.type === "changed") {
      setChanged(true);
    } else if (message.type === "cancelled") {
      // 「破棄して読み直す？」の確認をやめたので、止めていたボタンを戻す
      setBusy(false);
      setStatus(undefined);
    } else if (message.type === "picked") {
      pick(String(message.key), message.field as FileField, String(message.path));
    } else if (message.type === "tour") {
      requestTour();
    }
  }, () => post({ type: "ready" }));

  // 選択肢は、その欄と選択肢の外を押したとき、または Esc で閉じる
  useEffect(() => {
    const onDown = (event: Event): void => {
      const target = event.target as HTMLElement | null;
      if (target === null || target.closest(".picker.open") === null) {
        setPickerKey(undefined);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);
  useEscape(pickerKey !== undefined, ESCAPE_LAYER.popup, () => setPickerKey(undefined));

  /**
   * 判定でヒットした行を、折りたたんであってもその場だけ開く。見えないところで光っても分からないので、
   * タイプの折りたたみも外す。state には入れないので、次の判定で元の折りたたみに戻る。
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
    if (tour.touring) {
      return;
    }
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
    setSaveWarns([]);
  };

  /**
   * 読み直しを頼む。押した時点でボタンを止める。 拡張ホストは実行ファイルに聞いてから中身を
   * 返すことがあり（設定ファイルの場所を解く）、その間に押し直せると往復が重なる。ユーザが
   * 「破棄して読み直す？」をやめたときは `cancelled` が返り、ボタンが戻る。
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
          ルール管理画面を読み込めませんでした。原因を直してから「更新」を押してください。別の設定を選べば、このタブの中身がその設定に変わります。
        </p>
        <TargetSelect target={data.target} targets={data.targets} onSwitch={(kind, name) => post({ type: "switchTarget", kind, name })} />
        <pre className="load-error">{data.error}</pre>
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={reload}>
          更新
        </button>
      </>
    );
  }

  const isOpen = (key: string): boolean => open.has(key) || transient.has(key);

  const editDraft = (next: Draft, opened?: ReadonlySet<string>): void => {
    if (tour.touring) {
      return;
    }
    setEditing((now) => ({ draft: next, open: opened ?? now.open }));
    setDirty(true);
    setSaveWarns([]);
  };

  const editRule = (section: Section, key: string, rule: RuleForm): void => {
    editDraft(replace(draft, section, key, rule));
  };

  const toggle = (key: string): void => {
    if (tour.touring) {
      return;
    }
    const next = new Set(open);
    // その場だけ開いていた行も「開いている」。ユーザが押したらそこから閉じる
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
    if (tour.touring) {
      return;
    }
    const row = { key: nextKey(), rule: emptyRule() };
    // 足したルールは開いて出す。折りたたんだままでは何を足したか分からない
    editDraft({ ...draft, [section]: [...draft[section], row] }, new Set([...open, row.key]));
    setFolded(without(folded, section));
    setFocusKey(row.key);
  };

  const fold = (section: Section): void => {
    if (tour.touring) {
      return;
    }
    setFolded(folded.has(section) ? without(folded, section) : new Set([...folded, section]));
  };

  const judge = (): void => {
    if (tour.touring) {
      return;
    }
    if (!isToolName(tool.trim())) {
      setStatus({ text: "ツール名は英字で始まり、英数字・_・- だけで入れてください", error: true });
      return;
    }
    if (subject.trim() === "") {
      setStatus({ text: "対象を入れてください", error: true });
      return;
    }
    setBusy(true);
    setStatus({ text: "判定中…", error: false });
    post({ type: "judge", sections: sectionsOf(draft), tool: tool.trim(), subject });
  };

  const courses = coursesOf(TOUR, setScene);

  // 案内の間は、今の設定ではなく見本を描く。画面の state（編集中のルール・開いた行・タブ・判定の結果）は触らない
  const touring = tour.touring;
  const sceneOpen = new Set(sceneList(scene.open));
  const shownTab: TabName = touring ? (scene.tab === "judge" || scene.tab === "hooks" ? scene.tab : "rules") : tab;
  const shownDraft = touring ? SAMPLE_DRAFT : draft;
  // 畳んだタイプの中は指しても見えない（0×0 の枠を強調することになる）。案内の間は、手順の `fold` が名指ししたタイプだけを畳む（見出しを指す手順でだけ使う）
  const shownFolded: ReadonlySet<Section> = touring ? new Set(SECTIONS.filter((section) => sceneList(scene.fold).includes(section))) : folded;
  const shownFind = touring ? (typeof scene.find === "string" ? scene.find : "") : find;
  const sceneJudge = typeof scene.judge === "string" ? SAMPLE.judges[scene.judge] : undefined;
  const shownJudged: Judged | undefined = touring
    ? sceneJudge === undefined
      ? undefined
      : { result: sceneJudge, hooks: SAMPLE.hooks.filter((hook) => hook.event === "PreToolUse") }
    : judged;
  const shownChecked = touring ? (scene.checked === true ? SAMPLE.checked : undefined) : checked;
  const shownSuggested = touring ? (scene.suggested === true ? SAMPLE.suggested : undefined) : suggested;
  const shownHits: ReadonlySet<string> = touring ? new Set(sceneJudge?.rules.map((rule) => bareRuleId(rule.id)) ?? []) : hits;
  const shownDirty = touring ? false : dirty;
  // 保存前の検証エラーの見本（`sample.save_error`）。実際の直前の操作の一言は、案内の間は出さない
  const shownStatus: Status | undefined = touring ? (scene.error === true && SAMPLE.saveError !== "" ? { text: SAMPLE.saveError, error: true } : undefined) : status;
  const shownLock: Lock = touring ? { locked: false, reason: "", doing: [] } : lock;
  const shownTool = touring ? (typeof scene.tool === "string" ? scene.tool : KNOWN_TOOLS[0]) : tool;
  const shownSubject = touring ? (typeof scene.subject === "string" ? scene.subject : "") : subject;
  const isShownOpen = (row: { readonly key: string; readonly rule: RuleForm }): boolean => (touring ? sceneOpen.has(row.rule.id) : isOpen(row.key));

  const query = foldFind(shownFind.trim());
  // 3 タイプとも、絞り込みに一致する行も開いたまま見えている行も無いときだけ案内を出す
  const allRows = SECTIONS.flatMap((section) => shownDraft[section]);
  const nothingFound = query !== "" && allRows.length > 0 && allRows.every((row) => !findText(row.rule).includes(query) && !isShownOpen(row));

  return (
    <>
      {!touring && page !== undefined && modeBanner(page.mode) !== "" && (
        // モードは実行ファイルの答え。enable と、答えが取れないときは帯を出さない
        <div className="banner warn">
          現在の <code>CCNAVI_MODE</code>: <strong>{page.mode}</strong>。{modeBanner(page.mode)}
        </div>
      )}
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
      <div id="changed" className={changed ? "banner warn" : "banner warn hidden"}>
        ファイルの変更を検知しました。更新してください。
        <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={reload}>
          更新
        </button>
      </div>
      {touring ? <div className="banner tour-sample">{SAMPLE_NOTE}</div> : null}
      <ScreenHeader courses={courses} onPick={tour.start}>
        <div className="summary" data-tour="target">
          <TargetSelect
            target={touring ? undefined : page?.target}
            targets={touring ? undefined : page?.targets}
            disabled={busy}
            onSwitch={(kind, name) => post({ type: "switchTarget", kind, name })}
          />
          <span className="path" title={touring ? "" : page?.root ?? ""}>
            {touring ? "（見本）rules.yml" : page?.rulesPath ?? ""}
          </span>
          <span id="dirty" className={shownDirty ? "dirty" : "dirty hidden"}>
            未保存
          </span>
        </div>
        <div className="controls">
          <HandoffLink entry={touring ? undefined : page?.handoff} disabled={touring} onOpen={() => post({ type: "openHandoff" })} />
          <button type="button" className="action" data-action="open-rules" disabled={page?.exists === false} onClick={() => post({ type: "openFile", which: "rules" })}>
            エディタで開く
          </button>
          <button type="button" className="action" data-action="reload" title="ファイルを読み直します" disabled={busy} onClick={reload}>
            更新
          </button>
          <button
            type="button"
            className="action primary"
            id="save"
            data-tour="save"
            data-action="save"
            disabled={!shownDirty || shownLock.locked || busy}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "検証して保存中…", error: false });
              post({ type: "save", sections: sectionsOf(draft) });
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
      <nav className="tabs" role="tablist" data-tour="tabs">
        {([
          ["rules", "ルール"],
          ["judge", "判定を試す"],
          ["hooks", "hook"],
        ] as const).map(([name, label]) => (
          <button key={name} type="button" className={shownTab === name ? "tab active" : "tab"} data-tab={name} role="tab" onClick={() => showTab(name)}>
            {label}
          </button>
        ))}
      </nav>
      <section id="tab-rules" data-tour="rules" className={`pane${shownTab === "rules" ? " active" : ""}${query === "" ? "" : " finding"}`} ref={list}>
        <div className="find">
          <input id="find" data-tour="find" type="search" placeholder="id・ツール・パターン・メッセージ・プロンプトで絞り込む" spellCheck={false} value={shownFind} onChange={(event) => (tour.touring ? undefined : setFind(event.target.value))} />
          <span className="hint" data-tour="order-hint">判定は強い順に deny &gt; ask &gt; allow</span>
        </div>
        {nothingFound && <p className="empty">絞り込みに一致するルールがありません。検索語を変えるか、検索欄を空にしてください</p>}
        {SECTIONS.map((section) => {
          const rows = shownDraft[section].map((row) => {
            const text = findText(row.rule);
            return { ...row, find: text, hidden: query !== "" && !text.includes(query) };
          });
          const shown = rows.filter((row) => !row.hidden).length;
          const kept = rows.filter((row) => row.hidden && isShownOpen(row)).length;
          // 絞り込み中は折りたたんだタイプの中も見えるので、矢印も開いた向きにする
          const shownAsOpen = query !== "" || !shownFolded.has(section);
          return (
            <section className={shownFolded.has(section) ? "rule-section folded" : "rule-section"} data-section={section} data-tour={`section-${section}`} key={section}>
              <h2>
                <button
                  type="button"
                  className="twist"
                  data-action="fold-section"
                  data-section={section}
                  data-tour={`fold-${section}`}
                  aria-expanded={shownAsOpen}
                  title="このタイプを開く／畳む"
                  onClick={() => fold(section)}
                >
                  {shownAsOpen ? "▾" : "▸"}
                </button>{" "}
                <span className={`section-name ${section}`}>{section}</span> <span className="section-label">{SECTION_LABELS[section]}</span>{" "}
                <span className="count" data-count={section} data-tour={`count-${section}`}>
                  {countText(query, shown, shownDraft[section].length, kept)}
                </span>
                <button type="button" className="action small" data-action="add" data-section={section} data-tour={`add-${section}`} onClick={() => add(section)}>
                  ＋ ルールを追加
                </button>
              </h2>
              <ul className="list" data-list={section}>
                {rows.map((row, index) => (
                  <Rule
                    key={row.key}
                    ruleKey={row.key}
                    section={section}
                    rule={row.rule}
                    find={row.find}
                    hidden={row.hidden}
                    open={isShownOpen(row)}
                    hit={row.rule.id !== "" && shownHits.has(row.rule.id)}
                    pickerOpen={pickerKey === row.key}
                    moreOpen={touring ? scene.more === true : moreOpen.get(row.key) ?? hasContext(row.rule)}
                    onToggle={() => toggle(row.key)}
                    onChange={(rule) => editRule(section, row.key, rule)}
                    onMoveSection={(to) => moveSection(section, row.key, to)}
                    finding={query !== ""}
                    first={index === 0}
                    last={index === rows.length - 1}
                    onMove={(delta) => move(section, row.key, delta)}
                    onRemove={() => remove(section, row.key)}
                    onOpenPicker={() => (tour.touring ? undefined : setPickerKey(row.key))}
                    onPickFile={(field) => post({ type: "pickFile", key: row.key, field })}
                    pickNode={touring ? (typeof scene.when === "string" ? scene.when : "r") : undefined}
                    onToggleMore={(value) => (tour.touring ? undefined : setMoreOpen(new Map([...moreOpen, [row.key, value]])))}
                  />
                ))}
              </ul>
            </section>
          );
        })}
        {touring ? (
          // 案内の間の見本はキーを替えて別に作る（閉じたあとの実際の枠に、案内で選んだ統合結果を持ち越さない）
          <Sum
            sums={SAMPLE.sums}
            initial={defaultSum(SAMPLE.sums, undefined)}
            key="tour-sum"
            tour={{ open: typeof scene.sum === "string" && scene.sum !== "", picked: typeof scene.sum === "string" && scene.sum !== "" ? scene.sum : SAMPLE.sums[0]?.name ?? "" }}
          />
        ) : (
          page?.sums !== undefined && <Sum sums={page.sums} initial={defaultSum(page.sums, page.target)} key={`${page.target?.kind ?? ""}:${page.target?.name ?? ""}`} />
        )}
      </section>
      <section id="tab-judge" className={shownTab === "judge" ? "pane active" : "pane"}>
        <p className="hint">
          dry-run モードの場合でも、ここは enable モードのときの判定を返します。
        </p>
        <div className="judge-form" data-tour="judge-form">
          <label>
            ツール{" "}
            <input
              id="tool"
              type="text"
              list="tool-names"
              title="候補は判定が対象を取り出せるツールです。一覧にない名前も入れられます（判定には届きません）"
              spellCheck={false}
              value={shownTool}
              onChange={(event) => setTool(event.target.value)}
            />
            <datalist id="tool-names">
              {KNOWN_TOOLS.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          <label className="grow" title="--test の subject">
            対象{" "}
            <input
              id="subject"
              type="text"
              placeholder="Bash / PowerShell ならコマンド、Read / Grep / Glob / Edit / Write なら絶対パス、Skill ならスキル名、Agent なら見出し、WebFetch なら URL"
              spellCheck={false}
              value={shownSubject}
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
        {shownJudged !== undefined && <JudgeResult judged={shownJudged} />}
        <div className="expectations-head" data-tour="expectations">
          <button
            type="button"
            className="action"
            data-action="expectations"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setStatus({ text: "期待する判定を確認中…", error: false });
              post({ type: "expectations", sections: sectionsOf(draft) });
            }}
          >
            期待する判定を一括で確認する
          </button>
          <span className="path">{touring ? "（見本）rule-expectations.yml" : page?.expectationsPath ?? ""}</span>
          <button type="button" className="action" data-action="open-expectations" data-tour="open-expectations" onClick={() => post({ type: "openFile", which: "expectations" })}>
            エディタで開く
          </button>
        </div>
        {shownChecked !== undefined && <ExpectationsResult result={shownChecked} />}
        <div className="expectations-head" data-tour="suggest">
          <button
            type="button"
            className="action"
            data-action="suggest"
            disabled={busy}
            title="ccnavi --suggest --json を実行し、保存済みのルールと判定の記録から deny / ask の下書きを起こします"
            onClick={() => {
              setBusy(true);
              setStatus({ text: "記録から候補を起こしています…", error: false });
              post({ type: "suggest" });
            }}
          >
            記録から候補を出す
          </button>
          <span className="hint">保存済みのルールで確認した候補です。編集中の内容は使いません</span>
        </div>
        {shownSuggested !== undefined && <SuggestResult result={shownSuggested} />}
      </section>
      <section id="tab-hooks" data-tour="hooks" className={shownTab === "hooks" ? "pane active" : "pane"}>
        <Hooks hooks={touring ? SAMPLE.hooks : page?.hooks ?? []} files={touring ? { settings: true, settingsLocal: false } : page?.hookFiles ?? { settings: false, settingsLocal: false }} />
      </section>
      {tour.touring && <Tour steps={courseOf(courses, tour.course).steps} onClose={tour.end} />}
      <footer className={shownStatus?.error === true ? "foot error" : "foot"} data-tour="status">
        <span id="status">
          <Code text={shownStatus?.text ?? ""} />
        </span>
      </footer>
    </>
  );
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
