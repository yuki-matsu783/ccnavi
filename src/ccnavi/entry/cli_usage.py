"""`ccnavi --help` の本文。

cli から分けた。cli を読まない。
"""

from __future__ import annotations

USAGE = """ccnavi guards agent tool calls and guides the agent to a safer alternative.

It is a hook command, not something to run by hand: it reads one hook payload
as JSON on stdin and writes its response to stdout.

Register it on PreToolUse to judge calls before they run, and on PostToolUse to
watch the working tree for protected files that changed anyway. Exercise it with

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi

To say which build this is, run

    ccnavi --version [--json]

It prints the version, the commit it was built from ("unknown" when run from
source), the compat version the scripts in .ccnavi/scripts/ and the VS Code
extension compare with their own, and every flag it accepts. It reads no
payload and no settings. The --json shape is documented in README.md
("版の JSON").

To check the rules file and the settings without making a decision, run

    ccnavi --lint [--json]

It reads no payload, reports anything that could disable the guard as an error
or a warning, and exits non-zero when it reports an error. --json prints the
shape documented in README.md ("lint の JSON"); the VS Code extension reads it.

To try or lint one project's rules before saving them, hand the edited file in
by the project's name (this flag is for --test, --test-samples, --lint and
--explain only; anything else - a hook invocation, a ticket or review
subcommand - drops it and says so on stderr):

    ccnavi --test Write projects/lib/src/a.py --project-rules-file lib=/tmp/rules.yml

The phase types of one layer are handed in the same way (self is the
workspace's own layer; the common layer uses --phases):

    ccnavi --lint --project-phases-file self=/tmp/phases.yml

One child ticket's flow is checked the same way, read by the same reader and
the same checks that SubagentStart uses (--lint only; the VS Code extension
hands the edited flow in before it opens or saves one):

    ccnavi --lint --json --flow /tmp/flow.yml

To find a markdown document by what it is rather than by a line in its body,
search the frontmatter index of the workspace and its projects (paths are
from the workspace root, e.g. projects/lib/docs/x):

    ccnavi --docs [--type T] [--tag T] [--keyword K] [--path SUB] [--text SUB]
                  [--since DATE] [--until DATE] [--sort path|mtime|type|title]
                  [-r] [--limit N] [--format table|path|detail|json|jsonl|count]
                  [--no-refresh]

The same filter given twice is OR, different filters are AND, and case is
ignored. --type, --tag and --keyword match whole values; --path matches part
of the path without .md; --text matches part of the path, the mtime or any
frontmatter value; strings are compared in NFC. --since and --until take
YYYY-MM-DD[THH[:MM[:SS]]] and --until runs to the end of what it names (a date
to 23:59:59, THH to :59:59, THH:MM to :59). No match is still exit 0. Before
searching it brings the per-directory index.jsonl up to date (it writes only
where git ignores index.jsonl and the file is ccnavi's own; a tree that
ignores none is left out and named on stderr). The first run reads the head
of every markdown file; later runs read only those whose mtime moved.
--no-refresh reuses what is there. --json is --format json. The rows are
documented in README.md ("ドキュメントの索引"). These filters are for --docs
only; anywhere else they stop the run with exit 1, and flags that belong to
other runs stop --docs the same way.

The common layer's own three files are moved by --rules, --phases and --risk,
and where the layers are looked for by --projects and --project-home. All five
are on the same gate: diagnosis only, dropped everywhere else.

--root and --cwd say where this run is happening. The wrapper scripts in
.ccnavi/scripts/ work them out and pass them, so they are accepted once only;
a second one is refused rather than taken as an override.

To list the tickets, their places, the phase marks and the review holds
in a machine-readable form (the VS Code board extension reads this), run

    ccnavi --explain --json

It reads no payload and never reaches the remote. The shape is documented
in README.md ("ボードの JSON").

To try one call against the rules without running it, or to run every sample
in a file against them, run

    ccnavi --test Bash "git push" [--json]
    ccnavi --test-samples .ccnavi/common/rule-samples.yml [--json]

Both go through the same decision as the hook. --json prints the shape
documented in README.md ("試験の JSON"); the VS Code extension reads it.

To rotate the decision log and remove old logs and finished sessions' state
(SessionStart does the same on its own), run

    ccnavi --prune [--preview]

--preview only lists what would move. Without it, --prune needs a terminal.

To draft rules from the decision log (logs/decisions.jsonl and its rotated
decisions.*.jsonl), run

    ccnavi --suggest [--json]

It lists deny/ask drafts only: calls no rule mentioned that kept being handed
over, and denies that kept stopping the same call (review their message).
Each draft passed the same checks as --lint and --test-samples; the rest are
counted and dropped. Nothing is written. --json prints the shape documented in
README.md ("候補の JSON"); the VS Code extension reads it.

To review the pending tickets and agree to the work areas they declare, run

    ccnavi --agree
    ccnavi --agree i0002 i0002-01-01     (only these, e.g. from a filtered board;
                                            ids go last, after every flag)

It scans wip/proposals/todo/ in every worktree, shows what each ticket makes
writable and whether it needs a human review, then moves the approved ticket to
.ccnavi/approved/doing/. Only that place is consulted when judging calls, so
writing a proposal never widens the area on its own. Ids only narrow the batch:
an id that is not pending, or a child listed without its pending parent or
its parent's pending revision, agrees to nothing.

Before asking the user to agree, the agent verifies that the proposal it just
wrote is in a state that can be approved:

    ccnavi --agree --preview --verify [--json] [<id>...]

It places nothing and needs no terminal. Exit 0 is yes: every named ticket (or
every pending one, when no id is given) goes into the batch as it stands, so the
user can be asked. Exit 3 is no: an id that is not pending, no pending ticket at
all, or a proposal the approval drops. The reasons are printed per ticket. Exit
1 stays what it is everywhere else - a usage or settings error, not an answer -
so a wrong spelling is never read as a proposal to fix.

Two things are not a no, because --agree does not drop them either: scope that
exceeds the parent or the phase type (writes there stay blocked after approval),
and a proposal that cannot be read (the scan covers every worktree, before the
ids narrow it, so another session's draft would answer no). Both are printed.
Having nothing pending is the one place where the two differ: --agree calls
that a success with nothing to do, the verify calls it a no.

The VS Code board extension agrees from an overlay instead of the terminal:

    ccnavi --agree --preview --json [<id>...]  (show the batch; places nothing)
    ccnavi --agree --yes <id,id,...> --digest <hex> --json [<id>...]
        (agree to exactly what was shown; --digest is the preview's `digest`,
         the trailing ids are the same filter)

--yes needs no terminal; it refuses when the batch or the text changed since it
was shown, and when --digest is missing.
The hooks do not tell the model about approvals. The extension hands the
model the same text as --yes prints in `prompt`; otherwise the parent agent asks
with `ccnavi ticket status [<parent>]`.

The parent agent moves tickets between states and asks for reviews through the
scripts in .ccnavi/scripts/, which call

    ccnavi ticket start|finish|cancel <id> [--reason <why>]
        (start writes the base point into .ccnavi/approved/doing/<id>.md; finish moves
         it to wip/proposals/review/ when the phase is reviewed, else to
         .ccnavi/approved/done/; cancel moves it to .ccnavi/approved/done/)
    ccnavi ticket record-risk <child> <factor> yes|no --reason <why>   (qualitative risk)
    ccnavi ticket status [<parent>]
        (reads only: place, approval time, started or not, uncommitted / unpushed
         by the local remote-tracking ref, why it is stopped, and the next step;
         a subagent may run it too)
    ccnavi review prepare   --cwd <dir> --phase N --body-file <path>
    ccnavi review requested --cwd <dir> --phase N --result <json>
    ccnavi review confirm   --cwd <dir> --phase N --result <json> [--actor <account>]
    ccnavi review ready     --cwd <dir> --result <json>
    ccnavi sync paths
        (for .ccnavi/scripts/ccnavi-sync.sh: one "<key> <value>" per line - the
         approved and proposal places, the ccnavi directory, and the integration
         branch written in .claude/settings.local.json env; reads no environment
         for the integration branch)
    ccnavi sync check <parent> [<repo>]
        (for ccnavi-sync.sh after it took in <parent>: re-judges the family's
         approved tickets and its authority; a "check 1" line, then one
         "<severity> <detail>" per line; exit 1 when an error stops the family)

ccnavi never reaches the remote itself. The script fetches the merge request,
its threads and reviews, and hands them over as --result <json>.

A human accepts unresolved review threads, from the parent worktree, with

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh decide N

(the scripts live only in the workspace, so a worktree cut from a project
cannot reach them by the relative path)

which fetches the threads and runs

    ccnavi --reviewed N --accept-unresolved --result <json> --cwd <parent worktree>
        [--actor=<account> --via=terminal|board]
        (the script passes the token owner when it can read it; the reviewed mark
         keeps the account and the way. Without it the mark is as before)

A phase whose type says `review: chat` is reviewed in the session itself. There
is no merge request and no copy to read, so a human opens that gate from the
terminal with

    ccnavi --reviewed N --chat --cwd <parent worktree>

which only applies to phases whose type declared `chat`.

A human closes a parent early ("good enough for now") with

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh close-early --reason <why>

which runs `ccnavi --close-early --reason <why> --result <json>` and then
un-drafts the merge request and files the leftovers as a new issue.

When starting a project parent overwrote the project's .ccnavi/ with the common
layer, the first review shows it. A parent that closes without any review stops
until a human has seen it at the terminal with

    ccnavi --config-synced <parent>
"""
