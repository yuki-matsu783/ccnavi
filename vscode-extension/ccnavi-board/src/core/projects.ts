/**
 * プロジェクト管理画面の判断。ワークスペース内のプロジェクト（`projects/` の直下で `.git` を持つもの、
 * 設計 §25.2）を一覧し、clone の入力を検査し、ターミナルへ送るコマンド行を組む。
 *
 * ここは vscode にも子プロセスにも触れない。ファイルの有無や git の答えは呼び手が渡す。
 * 何がプロジェクトかは実行ファイルの答え（`--explain --json` の trees、`--lint --json` の苦情）に
 * 従い、拡張が決め直すことはしない。拡張が自分で決めるのは、入力の形の検査と、
 * プロジェクトになっていない `.git` の探し方だけ。
 */
import { shellQuote, toPosixPath } from "./commands.js";
import { problemsOfProject, problemsOfProjectsDir, type LintJson, type LintProblem } from "./lintmodel.js";
import type { BoardJson } from "./model.js";

// ---- clone の入力

export interface RemoteInfo {
  /** 打たれたままの URL（前後の空白だけ落とす） */
  readonly url: string;
  /** 同じリポジトリかを比べる鍵。scheme・ユーザ・ポート・末尾の `.git` と `/` を落とし、小文字にした `host/path` */
  readonly key: string;
  /** URL の末尾から採った、`projects/<名前>` の既定の名前 */
  readonly name: string;
}

export type RemoteCheck = { readonly ok: true; readonly remote: RemoteInfo } | { readonly ok: false; readonly error: string };

const FORMS = "受け付ける形は https://host/path、ssh://host/path、git@host:path の 3 つ。ローカルのパスと file:// は通さない";

/**
 * clone 元の URL を検査する。通すのは https / ssh / scp 風の 3 形だけ。
 * 資格情報（`user:token@`）が入っていれば通さない。ターミナルへ送った 1 行は履歴に残り、
 * `.git/config` にも平文で入るので、伏せても意味が無い。
 */
export function checkRemote(raw: string): RemoteCheck {
  const url = raw.trim();
  if (url === "") {
    return { ok: false, error: "URL が空" };
  }
  if (/\s/.test(url)) {
    return { ok: false, error: "URL に空白がある" };
  }
  let userinfo = "";
  let host: string;
  let repoPath: string;
  const full = /^(https|ssh):\/\/([^/]*)(\/.*)?$/i.exec(url);
  if (full !== null) {
    const authority = full[2];
    const at = authority.lastIndexOf("@");
    userinfo = at >= 0 ? authority.slice(0, at) : "";
    host = at >= 0 ? authority.slice(at + 1) : authority;
    repoPath = full[3] ?? "";
  } else {
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) {
      return { ok: false, error: FORMS };
    }
    // scp 風。`git@host:path`。`C:\path` のような Windows のパスは host が 1 文字になるので弾く。
    const scp = /^(?:([^@/:\\]+)@)?([^@/:\\]{2,}):([^\\]+)$/.exec(url);
    if (scp === null) {
      return { ok: false, error: FORMS };
    }
    userinfo = scp[1] ?? "";
    host = scp[2];
    repoPath = scp[3];
  }
  if (userinfo.includes(":")) {
    return {
      ok: false,
      error: "URL に資格情報（user:token@）が入っている。ターミナルの履歴と .git/config に平文で残るので受け付けない。credential helper か SSH 鍵を使う",
    };
  }
  host = host.replace(/:\d+$/, "").toLowerCase();
  if (host === "") {
    return { ok: false, error: "ホストが無い" };
  }
  const cleaned = repoPath.replace(/^\/+/, "").replace(/\/+$/, "").replace(/\.git$/i, "");
  if (cleaned === "") {
    return { ok: false, error: "リポジトリのパスが無い" };
  }
  const name = cleaned.split("/").pop() ?? "";
  return { ok: true, remote: { url, key: `${host}/${cleaned.toLowerCase()}`, name } };
}

/** origin の URL から比べる鍵を出す。読めない綴り（ローカルパスなど）なら空 */
export function remoteKeyOf(url: string): string {
  const checked = checkRemote(url);
  return checked.ok ? checked.remote.key : "";
}

/** `projects/<名前>` に使える綴り。英数字で始まり、英数字と `. _ -` だけ */
export const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export type NameCheck = { readonly ok: true; readonly name: string } | { readonly ok: false; readonly error: string };

/**
 * 名前を検査する。名前はツリーの名前、`wip/<名前>/tickets/`、`logs/<名前>/`、frontmatter の
 * `project:` にそのまま使われるので ASCII に絞る。既存のツリー名（プロジェクト・作業ツリー）と
 * 大文字小文字だけ違う名前も衝突扱い（Windows では同じディレクトリになる）。
 */
export function checkName(raw: string, existing: readonly string[]): NameCheck {
  const name = raw.trim();
  if (name === "") {
    return { ok: false, error: "名前が空" };
  }
  if (!NAME_PATTERN.test(name)) {
    return { ok: false, error: "名前は英数字で始まり、英数字と . _ - だけ（ASCII）。frontmatter の project: と置き場のパスにそのまま使われる" };
  }
  if (/^\.+$/.test(name)) {
    return { ok: false, error: "名前が . だけ" };
  }
  const lower = name.toLowerCase();
  const clash = existing.find((e) => e !== "" && e.toLowerCase() === lower);
  if (clash !== undefined) {
    return { ok: false, error: `${clash} という名前のツリーが既にある` };
  }
  return { ok: true, name };
}

// ---- ターミナルへ送るコマンド行

/** `git clone -- <url> <置き場>/<名前>` をワークスペースルートで。生の git を人が打つ形 */
export function cloneCommand(root: string, projectsDir: string, url: string, name: string): string {
  const target = `${toPosixPath(projectsDir)}/${name}`;
  return `cd ${shellQuote(toPosixPath(root))} && git clone -- ${shellQuote(url)} ${shellQuote(target)}`;
}

export function fetchCommand(projectRoot: string): string {
  return `cd ${shellQuote(toPosixPath(projectRoot))} && git fetch`;
}

export function pullCommand(projectRoot: string): string {
  return `cd ${shellQuote(toPosixPath(projectRoot))} && git pull`;
}

// ---- プロジェクトになっていない .git

export interface DirEntry {
  readonly name: string;
  readonly isDir: boolean;
}

export interface Stray {
  /** ワークスペースルートからの相対、"/" 区切り */
  readonly path: string;
  readonly reason: string;
}

/** 歩かないディレクトリ。作業ツリーは trees に既にあり、依存の置き場は深くて遅い */
export const SKIP_DIRS: ReadonlySet<string> = new Set([".git", "node_modules", ".venv", ".claude"]);

/** 置き場の中は 1 段深く歩き、2 段目に置かれた clone を「置き場の 2 段目以下」として拾う */
const DEPTH = 2;
const DEPTH_IN_PROJECTS = 3;

export const REASON_OUTSIDE = "置き場の外にある。プロジェクトになるのは projects/ の直下に置いたものだけ";
export const REASON_TOO_DEEP = "置き場の 2 段目以下にある。プロジェクトになるのは projects/ の直下に置いたものだけ";

export interface StrayInput {
  /** 置き場のルートからの相対（"/" 区切り）。空なら置き場が無効 */
  readonly projectsRel: string;
  /** ルートからの相対（空はルート自身）を受けて、そこにあるものを返す。読めなければ空 */
  readonly list: (rel: string) => readonly DirEntry[];
  /** trees にあるルート相対パス。既にプロジェクトか作業ツリーなので拾わない */
  readonly knownRels: ReadonlySet<string>;
}

/**
 * ワークスペース直下を深さ 2 まで歩き、`.git` を持つのにプロジェクトになっていないディレクトリを拾う。
 * 見つけたところで降りるのをやめる。表示だけで、操作は付けない。
 */
export function findStrayGitDirs(input: StrayInput): Stray[] {
  const found: Stray[] = [];
  const inProjects = (rel: string) =>
    input.projectsRel !== "" && (rel === input.projectsRel || rel.startsWith(`${input.projectsRel}/`));
  const walk = (rel: string, depth: number) => {
    for (const entry of input.list(rel)) {
      if (!entry.isDir || SKIP_DIRS.has(entry.name)) {
        continue;
      }
      const childRel = rel === "" ? entry.name : `${rel}/${entry.name}`;
      const children = input.list(childRel);
      const hasGit = children.some((c) => c.name === ".git");
      if (hasGit) {
        if (!input.knownRels.has(childRel)) {
          found.push({ path: childRel, reason: inProjects(childRel) ? REASON_TOO_DEEP : REASON_OUTSIDE });
        }
        continue;
      }
      const limit = inProjects(childRel) ? DEPTH_IN_PROJECTS : DEPTH;
      if (depth + 1 < limit) {
        walk(childRel, depth + 1);
      }
    }
  };
  walk("", 0);
  return found.sort((a, b) => a.path.localeCompare(b.path));
}

// ---- clone 後の設定

/** `.gitignore` の本文に置き場が書かれているか。`/projects/`、`projects/`、`/projects`、`projects` のどれか */
export function gitignoreHasProjects(text: string | undefined, projectsRel: string): boolean {
  if (text === undefined || projectsRel === "") {
    return false;
  }
  const accepted = new Set([projectsRel, `/${projectsRel}`, `${projectsRel}/`, `/${projectsRel}/`]);
  return text.split(/\r?\n/).some((line) => accepted.has(line.trim()));
}

/** `.gitignore` に置き場の行を足した本文。既にあればそのまま */
export function gitignoreWithProjects(text: string | undefined, projectsRel: string): string {
  if (gitignoreHasProjects(text, projectsRel)) {
    return text ?? "";
  }
  const head = text === undefined || text === "" ? "" : text.endsWith("\n") ? `${text}\n` : `${text}\n\n`;
  return `${head}# ccnavi のプロジェクト置き場。各プロジェクトは自分の git を持つ（設計 §25.2）。\n/${projectsRel}/\n`;
}

/**
 * ワークスペースのルールをプロジェクトの `config/rules.yml` に写すときの加工。
 * 先頭に出どころのコメントを足し、文面の `sh .claude/scripts/` を `sh {root}/.claude/scripts/` にする。
 * プロジェクトの中に cwd があるエージェントには `.claude/scripts/` が届かず、`{root}` はルールを
 * 読むときにワークスペースルートの絶対パスへ置き換わる（設計 §25.8）。置換は 1 種類だけ。
 */
export function rewriteRulesForProject(text: string, sourceRel: string, project: string, date: string): string {
  const header = [
    `# ${project} のルール。ワークスペースの ${sourceRel} を ${date} に写した（ccnavi ボード）。`,
    "# このプロジェクトへの Write / Edit はこのファイルで判定され、Bash はワークスペースと全プロジェクトの和。",
    "# 文面の sh の綴りは {root}/.claude/scripts/... に置き換えてある（{root} はワークスペースルートに展開される）。",
    "# 写したままでは当たる先の無いルール（.claude/ 向けの guard-*）が残る。害は無いので、要らなければ消す。",
    "",
  ].join("\n");
  return header + text.split("sh .claude/scripts/").join("sh {root}/.claude/scripts/");
}

// ---- 画面の中身

export interface ProjectRow {
  readonly name: string;
  readonly root: string;
  /** ルートからの相対、"/" 区切り */
  readonly rel: string;
  /** プロジェクトのルールファイル。ルートからの相対、"/" 区切り */
  readonly rulesRel: string;
  readonly rulesExists: boolean;
  readonly hasClaudeDir: boolean;
  readonly origin: string;
  readonly originKey: string;
  readonly worktrees: readonly string[];
  readonly tickets: number;
  readonly doing: number;
  readonly problems: readonly LintProblem[];
}

export interface ProjectsPage {
  readonly root: string;
  readonly generatedAt: string;
  readonly ticketsEnabled: boolean;
  /** 置き場（絶対）。空なら置き場が無効（CCNAVI_PROJECTS が空） */
  readonly projectsDir: string;
  readonly projectsRel: string;
  readonly projectsDirExists: boolean;
  readonly ignored: boolean;
  readonly lintError: string;
  readonly dirProblems: readonly LintProblem[];
  readonly rows: readonly ProjectRow[];
  readonly strays: readonly Stray[];
  readonly workspaceWorktrees: readonly string[];
  /** 名前の衝突を見る既存のツリー名（ワークスペース自身の空は除く） */
  readonly existingNames: readonly string[];
}

export interface PageInput {
  readonly board: BoardJson;
  readonly lint: LintJson | undefined;
  readonly lintError: string;
  /** プロジェクト名 → origin の URL（読めなければ空） */
  readonly origins: Readonly<Record<string, string>>;
  readonly strays: readonly Stray[];
  readonly projectsRel: string;
  readonly projectsDirExists: boolean;
  readonly ignored: boolean;
  /** プロジェクトのルールファイルの、git プロジェクトルートからの相対（既定 `config/rules.yml`） */
  readonly projectRules: string;
  readonly rulesExists: Readonly<Record<string, boolean>>;
  readonly hasClaudeDir: Readonly<Record<string, boolean>>;
}

export function buildProjectsPage(input: PageInput): ProjectsPage {
  const { board } = input;
  const trees = board.trees;
  const rows: ProjectRow[] = trees
    .filter((t) => t.kind === "project")
    .map((t) => {
      const origin = input.origins[t.name] ?? "";
      const own = board.tickets.filter((k) => k.project === t.name);
      return {
        name: t.name,
        root: t.root,
        rel: `${input.projectsRel}/${t.name}`,
        rulesRel: `${input.projectsRel}/${t.name}/${input.projectRules}`,
        rulesExists: input.rulesExists[t.name] === true,
        hasClaudeDir: input.hasClaudeDir[t.name] === true,
        origin,
        originKey: remoteKeyOf(origin),
        worktrees: trees.filter((w) => w.kind === "worktree" && w.project === t.name).map((w) => w.name),
        tickets: own.length,
        doing: own.filter((k) => k.proposal !== null && k.proposal.state === "doing").length,
        problems: input.lint === undefined ? [] : problemsOfProject(input.lint, t.name),
      };
    });
  return {
    root: board.root,
    generatedAt: board.generated_at,
    ticketsEnabled: board.settings.ticket_control !== "disable",
    projectsDir: board.settings.projects,
    projectsRel: input.projectsRel,
    projectsDirExists: input.projectsDirExists,
    ignored: input.ignored,
    lintError: input.lintError,
    dirProblems: input.lint === undefined ? [] : problemsOfProjectsDir(input.lint),
    rows,
    strays: input.strays,
    workspaceWorktrees: trees.filter((w) => w.kind === "worktree" && w.project === "").map((w) => w.name),
    existingNames: trees.map((t) => t.name).filter((n) => n !== ""),
  };
}

/** 同じリポジトリを既に clone しているプロジェクト。無ければ undefined */
export function duplicateOf(rows: readonly ProjectRow[], key: string): ProjectRow | undefined {
  if (key === "") {
    return undefined;
  }
  return rows.find((r) => r.originKey === key);
}
