/**
 * ボードの DOM を組む。判定は出さず、Python の答えを並べるだけ（ADR-0035）。
 *
 * 素の文字列はすべて `textContent`。Markdown の本文だけを消毒した断片で入れる（5.5 の 2）。
 * 段階 3 から、承認と取り下げのボタンを出す。出すかは Python の答え（`write.allowed`・
 * `withdrawable` の理由）で決まり、ここは答えのとおりに並べるだけ。押したときの動き（`Actions`）は
 * 呼び手が渡す。渡さなければボタンを出さない（読み取りだけ）。
 * 段階 4 から、依頼済みのフェーズのレビューの欄（MR のスレッドと、通らない理由）と「レビュー済みにする」を出す。
 * スレッドの本文は承認の画面と同じ規則で描く（Markdown は消毒した断片、隠れる書き方は通さない、HTML コメントは
 * 見える印。5.5 の 2・段階 3 のレビューの決定 A）。「始める」は段階 5。
 */
import type { Renderer } from "./sanitize.js";
import type { ReviewPanel } from "./reviewed.js";
import { ALLOWED_URI } from "./sanitize.js";
import type { FamilyBoard, RepoBoard } from "./snapshot.js";
import { repoKey } from "./settings.js";

function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

/** ボタンを押したときの動き。呼び手（ボード）が渡す */
export interface Actions {
  approve(repo: RepoBoard, family: FamilyBoard): void;
  withdraw(repo: RepoBoard, family: FamilyBoard, ticket: string): void;
  /** レビュー済みにする（段階 4）。渡さなければ「レビュー済みにする」を出さない */
  review?(repo: RepoBoard, family: FamilyBoard, phase: number): void;
}

export function renderRepo(doc: Document, md: Renderer, board: RepoBoard, actions?: Actions): HTMLElement {
  const section = el(doc, "section", "repo");
  section.dataset.repo = repoKey(board.repo);
  const head = el(doc, "header", "repo-head");
  head.append(el(doc, "h2", "", `${board.repo.owner}/${board.repo.repo}`));
  if (board.integration) {
    const source = board.integration.source === "setting" ? "設定" : "ホストのデフォルトブランチ";
    const line = el(doc, "p", "integration", `統合先: ${board.integration.name}（${source}） ${board.integration.head.slice(0, 7)}`);
    line.dataset.testid = "integration";
    head.append(line);
  }
  section.append(head);

  if (board.error) {
    section.append(notice(doc, "error", board.error));
    return section;
  }
  if (board.compat && !board.compat.same) {
    section.append(notice(doc, "warn", `${board.compat.message}（承認と取り下げは出さない。表示だけ）`));
  }
  if (board.missingExtras.length > 0) {
    section.append(notice(doc, "warn", `指定したブランチがリモートに無い: ${board.missingExtras.join(", ")}`));
  }
  const scope = el(doc, "p", "scope", `見たブランチ（表示用）: ${board.candidates.length === 0 ? "なし" : board.candidates.join(", ")}`);
  section.append(scope);
  if (board.families.length === 0) {
    section.append(el(doc, "p", "empty", "見たブランチに親のブランチ（家族）は無い"));
  }
  for (const f of board.families) {
    section.append(
      renderFamily(
        doc,
        md,
        f,
        actions && {
          approve: () => actions.approve(board, f),
          withdraw: (t) => actions.withdraw(board, f, t),
          review: actions.review ? (n: number) => actions.review?.(board, f, n) : undefined,
        },
      ),
    );
  }
  const s = board.stats;
  section.append(el(doc, "p", "stats", `読み取り: REST ${s.rest} 回・GraphQL ${s.graphql} 回・blob ${s.blobsFetched} 件（控えから ${s.blobsCached} 件）`));
  return section;
}

function notice(doc: Document, kind: "error" | "warn", text: string): HTMLElement {
  const p = el(doc, "p", `notice ${kind}`, text);
  p.setAttribute("role", kind === "error" ? "alert" : "status");
  return p;
}

export interface FamilyActions {
  approve(): void;
  withdraw(ticket: string): void;
  review?(phase: number): void;
}

function button(doc: Document, text: string, action: string, onClick: () => void): HTMLButtonElement {
  const b = el(doc, "button", "action", text);
  b.type = "button";
  b.dataset.action = action;
  b.addEventListener("click", onClick);
  return b;
}

export function renderFamily(doc: Document, md: Renderer, f: FamilyBoard, actions?: FamilyActions): HTMLElement {
  const box = el(doc, "article", "family");
  box.dataset.family = f.family.name;
  const title = el(doc, "h3");
  title.append(el(doc, "code", "", f.family.name), doc.createTextNode(` ${f.family.title}`));
  box.append(title);
  if (f.error) {
    box.append(notice(doc, "error", f.error));
    return box;
  }
  const r = f.result;
  if (r === null) {
    return box;
  }
  const others = r.closure.families.filter((n) => n !== f.family.name);
  if (others.length > 0) {
    box.append(el(doc, "p", "closure", `判定に入れた家族（先行の閉包）: ${others.join(", ")}`));
  }
  if (r.closure.absent.length > 0) {
    box.append(notice(doc, "warn", `先行の家族のブランチがリモートに無い: ${r.closure.absent.join(", ")}`));
  }
  if (r.undecided) {
    box.append(notice(doc, "error", r.undecided));
    return box;
  }
  if (r.refused) {
    box.append(notice(doc, "error", r.refused));
    return box;
  }
  const batch = r.batch ?? [];
  if (batch.length === 0) {
    box.append(el(doc, "p", "empty", "承認待ちは無い"));
  }
  if (r.text) {
    // ccnavi が出した承認の画面の本文（範囲・リスク・計画など）を、最初に開いた形で出す（REQ-APV-01 の
    // 「範囲を最初に」。レビューの決定 A）。閉じた details には入れない。素の文字列なので textContent
    const screen = el(doc, "section", "screen");
    screen.dataset.testid = "screen";
    screen.append(el(doc, "h4", "", "承認の画面（ccnavi が出したもの。承認するとこのとおりに書く）"));
    screen.append(el(doc, "pre", "", r.text));
    box.append(screen);
  }
  for (const entry of batch) {
    const item = el(doc, "section", "entry");
    item.dataset.ticket = entry.ticket;
    const h = el(doc, "h4");
    h.append(el(doc, "code", "", entry.ticket), doc.createTextNode(` ${entry.title}`));
    if (entry.revision) h.append(el(doc, "span", "badge", "改版"));
    if (entry.phase !== null) h.append(el(doc, "span", "badge", `フェーズ ${entry.phase}`));
    item.append(h);
    item.append(el(doc, "p", "path", entry.path));
    for (const o of entry.overflow) item.append(notice(doc, "warn", o));
    const body = el(doc, "div", "markdown");
    body.append(md.markdown(entry.body));
    item.append(body);
    box.append(item);
  }
  const write = r.write ?? { allowed: false, reason: "" };
  if (batch.length > 0 && r.digest) {
    if (!write.allowed) {
      box.append(notice(doc, "warn", write.reason || "この家族は書けない"));
    } else if (actions) {
      const bar = el(doc, "div", "actions");
      bar.append(button(doc, `承認する（${batch.map((e) => e.ticket).join(", ")}）`, "approve", actions.approve));
      bar.append(el(doc, "span", "digest", `指紋 ${r.digest.slice(0, 12)}`));
      box.append(bar);
    }
  }
  const withdrawable = r.withdrawable ?? [];
  if (withdrawable.length > 0) {
    const list = el(doc, "section", "approved");
    list.append(el(doc, "h4", "", "作業中の承認済みチケット"));
    for (const w of withdrawable) {
      const item = el(doc, "div", "approved-item");
      item.dataset.ticket = w.ticket;
      item.append(el(doc, "code", "", w.ticket), doc.createTextNode(` ${w.title} `));
      if (w.problems.length === 0 && write.allowed && actions) {
        item.append(button(doc, "承認を取り下げる", "withdraw", () => actions.withdraw(w.ticket)));
      } else if (w.problems.length > 0) {
        const ul = el(doc, "ul", "why-not");
        for (const p of w.problems) ul.append(el(doc, "li", "", `取り下げられない: ${p}`));
        item.append(ul);
      }
      list.append(item);
    }
    box.append(list);
  }
  for (const panel of f.reviews ?? []) {
    box.append(renderReview(doc, md, panel, write.allowed, actions));
  }
  for (const rej of r.rejected ?? []) {
    const item = el(doc, "section", "rejected");
    item.dataset.ticket = rej.ticket;
    item.append(el(doc, "h4", "", `承認の対象にしない: ${rej.ticket}`));
    const ul = el(doc, "ul");
    for (const p of rej.problems) ul.append(el(doc, "li", "", p));
    item.append(ul);
    box.append(item);
  }
  if ((r.problems ?? []).length > 0) {
    const ul = el(doc, "ul", "problems");
    for (const p of r.problems ?? []) ul.append(el(doc, "li", "", p));
    box.append(ul);
  }
  return box;
}

/** リンクを http(s)・mailto の綴りのときだけ付ける。ほかは文字だけ（5.5 の 2） */
function link(doc: Document, href: string, text: string): HTMLElement {
  if (!ALLOWED_URI.test(href.trim())) return el(doc, "span", "", text);
  const a = el(doc, "a", "", text);
  a.setAttribute("href", href);
  a.setAttribute("target", "_blank");
  a.setAttribute("rel", "noopener noreferrer");
  return a;
}

/**
 * 依頼済みのフェーズのレビューの欄（8.9）。スレッドは未解決を先に、本文は承認の画面と同じ消毒で描く。
 * 「レビュー済みにする」は、Python が通さない理由を返さず、書ける家族で、読めたときだけ出す。
 */
export function renderReview(doc: Document, md: Renderer, panel: ReviewPanel, writable: boolean, actions?: FamilyActions): HTMLElement {
  const box = el(doc, "section", "review");
  box.dataset.phase = String(panel.phase);
  const h = el(doc, "h4");
  h.append(doc.createTextNode(`フェーズ ${panel.phase} のレビュー（`));
  h.append(panel.copy ? link(doc, panel.copy.mr.url, `MR #${panel.copy.mr.number}`) : doc.createTextNode(`MR #${panel.mr}`));
  h.append(doc.createTextNode(`）: ${panel.children.join(", ")}`));
  box.append(h);
  if (panel.error) {
    box.append(notice(doc, "error", panel.error));
    return box;
  }
  // GitHub では目印で始まるスレッドも人のものとして数える（ccnavi の依頼はスレッドにならない。11.8.1 の決定 C）
  const threads = [...(panel.copy?.threads ?? [])].sort((a, b) => Number(a.resolved) - Number(b.resolved));
  const unresolved = threads.filter((t) => !t.resolved).length;
  box.append(el(doc, "p", "threads-count", `スレッド ${threads.length} 件（未解決 ${unresolved} 件）・レビュー ${panel.copy?.reviews.length ?? 0} 件`));
  const list = el(doc, "ul", "threads");
  for (const t of threads) {
    const item = el(doc, "li", "thread");
    item.dataset.resolved = String(t.resolved);
    const head = el(doc, "p", "thread-head");
    head.append(el(doc, "span", "badge", t.resolved ? "解決済み" : "未解決"));
    head.append(doc.createTextNode(" "));
    head.append(link(doc, t.url, t.path ? `${t.path}:${t.line}` : "（行なし）"));
    item.append(head);
    const body = el(doc, "div", "markdown");
    body.append(md.markdown(t.body));
    item.append(body);
    list.append(item);
  }
  box.append(list);
  if (panel.problems.length > 0) {
    const ul = el(doc, "ul", "problems");
    for (const p of panel.problems) ul.append(el(doc, "li", "", p));
    box.append(ul);
  } else if (writable && actions?.review) {
    const review = actions.review;
    const bar = el(doc, "div", "actions");
    bar.append(button(doc, `フェーズ ${panel.phase} をレビュー済みにする`, "review", () => review(panel.phase)));
    box.append(bar);
  }
  return box;
}
