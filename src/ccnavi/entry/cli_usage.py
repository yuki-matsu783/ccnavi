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
by the project's name (this flag is for --test, --test-expectations, --lint and
--explain only; anything else - a hook invocation, a ticket or review
subcommand - drops it and says so on stderr):

    ccnavi --test Write projects/lib/src/a.py --project-rules-file lib=/tmp/rules.yml

The phase types of one layer are handed in the same way (self is the
workspace's own layer; the common layer uses --phases):

    ccnavi --lint --project-phases-file self=/tmp/phases.yml

The risk weights of one layer are handed in the same way (--project-risk-file;
the common layer uses --risk). They are checked together with the common
layer: the boundaries of levels after the merge, ids that collide, and whether
each script: exists under that layer's own git project root:

    ccnavi --lint --project-risk-file lib=/tmp/risks.yml

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
searching it brings the per-directory ccnavi-index.jsonl up to date (it writes only
where git ignores ccnavi-index.jsonl and the file is ccnavi's own; a tree that
ignores none gets one line added to its .gitignore first, and is left out and
named on stderr only if that fails). The first run reads the head
of every markdown file; later runs read only those whose mtime moved.
--no-refresh reuses what is there. --json is --format json. The rows are
documented in README.md ("ドキュメントの索引"). These filters are for --docs
only; anywhere else they stop the run with exit 1, and flags that belong to
other runs stop --docs the same way.

To list, search and read the work/review plan templates (a plan with its
child flows in one file, kept in .ccnavi/config/common/templates/ and
.ccnavi/config/project/templates/; it reads only and writes nothing):

    ccnavi --templates [--layer self|PROJECT] [--keyword K]... [--json]
    ccnavi --templates --show NAME [--layer self|PROJECT] [--json]

The list is the common layer plus one layer (self unless --layer says
otherwise). --keyword matches part of the name, description or when (given
twice, either one); --show prints one template with its plan, flows and
checks. A name that is in both layers with different content is an error, and
the layer's one is not used. --json is the complete answer the board screen
draws, documented in README.md ("作業・レビュー計画のテンプレート"). --keyword
is shared with --docs and is refused elsewhere; the other --docs filters are
refused here. --lint reports the same checks.

Templates are collected in two steps. An agent may type --draft: it copies the
plan and the child flows of a finished parent (still in done/) into a draft at
wip/proposals/templates/NAME.yml, which has no effect and is never overwritten.
Only the user types --import: without --yes it writes nothing and shows the
check and the diff against the same name in the target; with --yes and the
draft's hash as --expect it writes, then removes the draft if it is unchanged.

    ccnavi --templates --draft PARENT [--name NAME] [--json]
    ccnavi --templates --import DRAFT --to common|project [--layer NAME]
                       [--replace] [--yes --expect HASH] [--json]

The draft and the collected template can also be edited. Without --yes they
write nothing and show the change; with --yes and the subject's hash as
--expect they write. Only the user types --yes. --save-draft, --rename-template,
--move-template and --delete-template are user-only (the agent cannot type
them, not even the preview; it edits drafts with Write). --delete-draft
previews can be typed by the agent.

    ccnavi --templates --save-draft NAME --body-file FILE [--yes --expect HASH] [--json]
    ccnavi --templates --delete-draft NAME [--yes --expect HASH] [--json]
    ccnavi --templates --rename-template NAME --new-name NEW --on draft|common|project
                       [--layer NAME] [--yes --expect HASH] [--json]
    ccnavi --templates --move-template NAME --on common|project [--layer NAME]
                       --to common|project [--to-layer NAME] [--yes --expect HASH] [--json]
    ccnavi --templates --delete-template NAME --on common|project [--layer NAME]
                       [--yes --expect HASH] [--json]

A collected template is used when a parent proposal is started. An agent may
type it (the flow drafts have no effect either):

    ccnavi ticket new ID --title TITLE --template NAME [--layer self|PROJECT]
                         [--with-feedback] [--issue N]

It writes wip/proposals/todo/ID.md in the worktree named ID, with plan: taken
from the template (after is renumbered from the position inside the plan to
the proposal's numbers), then fills phases:. feedback: is written only with
--with-feedback (the feedback plan continues the main plan's numbers). Without
it the output says the template has a feedback plan: the feedback plan is added
once, by a revision, after the last review of the main plan, and writing it in
the first proposal would stop the main plan from being revised after approval.
--with-feedback is refused anywhere but ticket new --template. The flow of
the n-th plan item goes to wip/proposals/flows/ID-NN-01.yml, a draft that has
no effect until the user imports it in the flow editor. NAME is looked up in
the common layer plus one layer (the worktree's own layer unless --layer says
otherwise). It stops, and
writes nothing, for an unknown or unusable template (an error, or one dropped
by a clash), a type that phases.yml lacks, --plan given too, or a proposal or
flow draft that already exists. The proposal still goes through the usual
approval.

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

To try one call against the rules without running it, or to run every expectation
in a file against them, run

    ccnavi --test Bash "git push" [--json]
    ccnavi --test-expectations .ccnavi/config/common/rule-expectations.yml [--json]

Both go through the same decision as the hook. --json prints the shape
documented in README.md ("試験の JSON"); the VS Code extension reads it.
--test tries the call from the workspace root, with no permission mode, from
the main session. To try it the way another call would arrive, add

    --cwd <dir> --permission-mode <mode> --agent-id <id>

The mode is one of auto, default, acceptEdits, plan, dontAsk and
bypassPermissions; any other name, and a --cwd that is not a directory, is
refused. --permission-mode and --agent-id are for --test only (also not
with --lint or --explain). Put flags before or after "--test <TOOL> <SUBJECT>",
never between the two words; a flag there, or a word left over after them,
is refused.

To rotate the decision log and remove old logs and finished sessions' state
(SessionStart does the same on its own), run

    ccnavi --prune [--preview]

--preview only lists what would move. Without it, --prune needs a terminal.

To draft rules from the decision log (logs/decisions.jsonl and its rotated
decisions.*.jsonl), run

    ccnavi --suggest [--json]

It lists deny/ask drafts only: calls no rule mentioned that kept being handed
over, and denies that kept stopping the same call (review their message).
Each draft passed the same checks as --lint and --test-expectations; the rest are
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

A parent that has a plan carries a copy of the phase types its plan uses, as
`phases:` (keyed by type id, same fields as phases.yml). --agree refuses a copy
that differs from phases.yml as read. The agent writes the copy with

    ccnavi --plan-order <parent> --fill-phases

which replaces the `phases:` value of the pending parent proposal with the
types its plan and feedback use (taken from phases.yml; on a revision, a type
used by a number that already has an approved child is taken from the approved
ticket). Unused types are dropped; no other byte changes, and nothing is
written when another field would read differently. It is a flag of its own,
outside --agree, so the agent may run it. After approval, judging reads the
copy and never phases.yml.

The order of a pending parent's plan is set with the same flag. The VS Code
workflow tab runs it; the agent may run it too:

    ccnavi --plan-order <parent> --json [--order <JSON>]
        (read only; with --order, the plan renumbered from the lines drawn)
    ccnavi --plan-order <parent> --order <JSON> --expect <sha> --write [--json]
        (write the renumbered plan back to the proposal)

--order gives each item's direct predecessors by the numbers written in the
proposal now: {"plan": {"1": [], "2": [3], "3": [1]}, "feedback": {...}}. A part
that is passed takes every number's predecessors from it (a number left out
waits for nothing); a part not passed stays as proposed. Items are renumbered in
topological order, the smallest current number first among those whose
predecessors are all placed; `after` follows the new numbers and `review: defer`
moves with its item. On a revision, a number that already has an approved child
stays where it is and the others fill the free numbers. A line that makes a
cycle, changes what such a fixed number waits for, or cannot be packed around
the fixed numbers is refused (`refused`), and nothing is renumbered.

The answer carries `plans` (one per part: items with `number` and `from`,
`after` in the new numbers, `proposed`, `current`, `loose`, `ready`,
`problems`), `source_sha` (the parent proposal, its pending children and the
pending children whose `predecessors` name them), the order `problems`,
`loose`, `refused`, `revision_problems`, `children` (the pending children to
retarget: `ticket` -> `new_ticket`, `phase` -> `new_phase`, `predecessors` ->
`new_predecessors`), `child_problems` and `mismatched` (pending children whose
`phase:` is not in the plan).

--write rewrites the `plan:` and `feedback:` values of the parent proposal.
A pending child (todo/) that points at a number that moves gets the new number
in its id (`<parent>-<phase>-<seq>`; the sequence stays), its file name,
`ticket:` and `phase:`, and pending children that name it in `predecessors`
are fixed too. Approved children are never touched. Children are written
first and the parent last; on a failure everything written is put back. It
writes nothing when --expect differs from the current `source_sha`, when a
line is refused, when the renumbered plan has an order error or breaks the
revision lock, when another field would read differently, when a new child id
clashes with a file or ticket that stays, or when the approved side (approved
tickets, records, markers, flows, worktrees) refers to an id that would
change. It never approves; approving the rewritten proposal needs a fresh
preview, since the digest changes.

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
    ccnavi ticket learn [<id>] [--note <text> [--target <kind>] [--signal <kind>]]
        (writes the learning record scaffold and the decision-log digest into
         wip/handoff/learnings/<id>.*; with --note appends one learning,
         or writes it to docs/wiki/inbox/ outside a ticket. The <id> goes before the options.)
    ccnavi ticket new <id> --title <title> --plan <type,type,...> [--scope <glob>]... [--issue N]
        (writes the scaffold of a parent proposal to wip/proposals/todo/<id>.md of
         the worktree named <id>; run it inside that worktree. Collects the scope
         of the chosen phases from phases.yml into allow, adds each --scope glob
         (the files this work touches, relative to the worktree root; repeatable),
         chains the plan in a straight line and fills phases:. A scope: inherit
         type such as quick-work collects nothing by itself, so pass --scope.
         Stops without phases.yml, for a child id, or when allow would be empty.
         Typed directly, not via ccnavi-ticket.sh)
    ccnavi ticket new <id> --title <title> --template <name> [--layer <name>]
                           [--with-feedback] [--issue N]
        (the same, but the plan and the child flow drafts come from a template
         instead of --plan; the feedback plan is written only with --with-feedback;
         see the --templates section above)
    ccnavi ticket status [<parent>]
        (reads only: place, approval time, started or not, uncommitted / unpushed
         by the local remote-tracking ref, why it is stopped, and the next step;
         a subagent may run it too)
    ccnavi ticket next-id [<prefix> <slug>]
        (reads only: the next serial number for a parent that has no issue, or
         the identifier <prefix>-<number>-<slug> built from it; a subagent may
         run it too)
    ccnavi review prepare   --cwd <dir> --phase N --body-file <path>
    ccnavi review requested --cwd <dir> --phase N --result <json>
    ccnavi review confirm   --cwd <dir> --phase N --result <json> [--actor <account>]
    ccnavi review ready     --cwd <dir> --result <json>
        (stdout: the comment draft, "tree <dir>", then "wip-from <sha>" and one
         "wip <path>" per tracked file it removed from wip/)
    ccnavi worktree drop <name> [--cwd <dir>]
        (cleans the build outputs and runs `git worktree remove` without --force on
         .claude/worktrees/<name>; keeps the branch; does nothing when --cwd is
         inside it or it has uncommitted changes, and says so)
    ccnavi worktree tidy <parent> [--phase N] [--cwd <dir>]
        (drops the worktrees of the closed children - finished or cancelled -, of
         phase N when given; only children in doing/ keep theirs; a child id
         stands for its parent, read from the ticket)
    ccnavi worktree remaining <parent> [--cwd <dir>]
        (lists, and never removes, the worktrees of the family that are still
         there: one named differently from the parent that holds its tickets, and
         a closed child's that has unsaved drafts; prints nothing when none)
    ccnavi worktree name <name>
        (prints one "warn: ..." line when a direct-work worktree's name is not
         <prefix>-<number>-<slug>; prints nothing when it is. Never refuses)
    ccnavi sync paths
        (for .ccnavi/scripts/ccnavi-sync.sh: one "<key> <value>" per line - the
         approved and proposal places, the ccnavi directory, and the integration
         branch written in .claude/settings.local.json env; reads no environment
         for the integration branch)
    ccnavi sync check <parent> [<repo>]
        (for ccnavi-sync.sh after it took in <parent>: re-judges the family's
         approved tickets and its authority; a "check 1" line, then one
         "<severity> <detail>" per line; exit 1 when an error stops the family)
    ccnavi sync closed <parent> <repo> <integration> <tree>
        (for ccnavi-sync.sh: a "closed 1" line, then "integration yes|no" - the
         integration branch's done/ holds the same parent - and "archive yes|no" -
         the local archive holds it; reads only, git show is local)
    ccnavi tree parent <tree> <name>
        (for the scripts: a "tree 1" line, then "parent yes|no|unreadable" - the
         tree holds the parent ticket or proposal <name>; the scripts read no
         ticket YAML themselves)

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

A human closes a parent early ("good enough for now") with

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh close-early --reason <why>

which runs `ccnavi --close-early --reason <why> --result <json>` and then
un-drafts the merge request and files the leftovers as a new issue.
"""
