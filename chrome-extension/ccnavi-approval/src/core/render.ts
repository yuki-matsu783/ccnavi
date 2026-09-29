/**
 * 読み取り専用ボードの DOM を組む。判定は出さず、Python の答えを並べるだけ（ADR-0035）。
 *
 * 素の文字列はすべて `textContent`。Markdown の本文だけを消毒した断片で入れる（5.5 の 2）。
 * 承認・取り下げ・レビュー済み・「始める」のボタンは段階 1 では出さない。
 */
import type { Renderer } from "./sanitize.js";
import type { FamilyBoard, RepoBoard } from "./snapshot.js";
import { repoKey } from "./settings.js";

function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

export function renderRepo(doc: Document, md: Renderer, board: RepoBoard): HTMLElement {
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
    section.append(notice(doc, "warn", `${board.compat.message}（段階 1 は表示だけ）`));
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
    section.append(renderFamily(doc, md, f));
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

export function renderFamily(doc: Document, md: Renderer, f: FamilyBoard): HTMLElement {
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
  if (r.text) {
    const details = el(doc, "details", "screen");
    details.append(el(doc, "summary", "", "承認の画面の本文（ccnavi が出したもの）"));
    details.append(el(doc, "pre", "", r.text));
    box.append(details);
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
