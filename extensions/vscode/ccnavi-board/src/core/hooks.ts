/**
 * `.claude/settings.json`と`.claude/settings.local.json`のhooksを読み、
 * 「このツール名でどのhookが走るか」を答える。
 *
 * 読むだけで書かない。ユーザごとの設定（~/.claude/settings.json）は見ない。
 * ワークスペースの外を読む手段にしないため。ユーザごとの設定が載らないことは画面に書く。
 *
 * matcherの意味はClaude Codeのもので、ccnaviの判定ではない。空か`*`なら全部、
 * それ以外はツール名に対する正規表現（`Write|Edit`のように書ける）。正規表現として
 * 読めなければ文字列そのものと比べる。ここはhookの登録を見せる場所。
 */

export type HookSource = "settings" | "settings-local";

export interface HookEntry {
  readonly source: HookSource;
  readonly event: string;
  readonly matcher: string;
  readonly command: string;
  readonly timeout: number | null;
}

/** ツール名を持つイベント。これ以外はmatcherに関係なく常に走る */
export const TOOL_EVENTS = ["PreToolUse", "PostToolUse", "PostToolUseFailure"] as const;

export function parseHooks(text: string, source: HookSource): HookEntry[] {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return [];
  }
  if (!isRecord(raw) || !isRecord(raw.hooks)) {
    return [];
  }
  const entries: HookEntry[] = [];
  for (const [event, groups] of Object.entries(raw.hooks)) {
    if (!Array.isArray(groups)) {
      continue;
    }
    for (const group of groups) {
      if (!isRecord(group)) {
        continue;
      }
      const matcher = typeof group.matcher === "string" ? group.matcher : "";
      const hooks = Array.isArray(group.hooks) ? group.hooks : [];
      for (const hook of hooks) {
        if (!isRecord(hook)) {
          continue;
        }
        entries.push({
          source,
          event,
          matcher,
          command: typeof hook.command === "string" ? hook.command : "",
          timeout: typeof hook.timeout === "number" ? hook.timeout : null,
        });
      }
    }
  }
  return entries;
}

/** matcherがこのツール名にヒットするか */
export function matcherHits(matcher: string, tool: string): boolean {
  const trimmed = matcher.trim();
  if (trimmed === "" || trimmed === "*") {
    return true;
  }
  try {
    return new RegExp(`^(?:${trimmed})$`).test(tool);
  } catch {
    return trimmed === tool;
  }
}

/** このツール名で走るhook。ツール名を持つイベントはmatcherで絞り、それ以外は常に含める */
export function hooksFor(entries: readonly HookEntry[], tool: string): HookEntry[] {
  return entries.filter((e) =>
    (TOOL_EVENTS as readonly string[]).includes(e.event) ? matcherHits(e.matcher, tool) : true,
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
