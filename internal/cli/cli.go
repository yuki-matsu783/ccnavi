// Package cli wires stdin, stdout and the command line to a single decision.
//
// Run returns an exit code instead of calling os.Exit so that the whole tool
// can be exercised from a test without a subprocess.
package cli

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/yuki-matsu783/ccnavi/internal/audit"
	"github.com/yuki-matsu783/ccnavi/internal/hookio"
	"github.com/yuki-matsu783/ccnavi/internal/rules"
	"github.com/yuki-matsu783/ccnavi/internal/settings"
)

// Deadline bounds one invocation. The caller cancels a hook that runs too long
// and throws its output away, which lets the tool call through. Landing on a
// verdict of our own before that happens keeps a slow decision from becoming a
// silent allow.
const Deadline = 3 * time.Second

// Exit codes.
const (
	exitOK    = 0 // a verdict was written, or there was nothing to say
	exitError = 1 // bad usage, or a settings file that cannot be read
	exitBlock = 2 // fail closed when no verdict could be written
)

// Mode decides what happens to a verdict, not how it is reached. The two modes
// that keep judging run the same code, so what warn reports is what block would
// have stopped.
//
// The names say what becomes of the tool call, and they read from weakest to
// strongest the way a linter's severities do.
type Mode string

const (
	ModeOff   Mode = "off"   // no judgment at all
	ModeWarn  Mode = "warn"  // judge and report, but let the call run
	ModeBlock Mode = "block" // judge and stop the call
)

// Run performs one invocation.
func Run(ctx context.Context, stdin io.Reader, stdout, stderr io.Writer, args []string) int {
	fs := flag.NewFlagSet("ccnavi", flag.ContinueOnError)
	fs.SetOutput(stderr)
	root := fs.String("root", defaultRoot(), "project root, the directory that holds .claude")
	modeFlag := fs.String("mode", "", "override the configured mode: warn or block")
	rulesPath := fs.String("rules", "", "override the rule file named in the settings")
	logPath := fs.String("log", "", "override the record destination named in the settings")
	fs.Usage = func() { usage(stderr, fs) }

	if err := fs.Parse(args); err != nil {
		return exitError
	}

	conf, problems := settings.Load(*root)
	for _, p := range problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", p)
	}
	// A flag beats everything, so a diagnostic run can point elsewhere without
	// touching a file the whole project shares.
	if *logPath != "" {
		conf.Log = *logPath
	}
	if *rulesPath != "" {
		conf.Rules = *rulesPath
	}

	mode := resolveMode(stderr, *modeFlag, conf)
	log := audit.Open(conf.Log)

	in, err := hookio.Decode(stdin)
	if err != nil {
		if errors.Is(err, hookio.ErrNoPayload) {
			// A person at a terminal, not a hook. Nothing was judged, so
			// nothing is recorded.
			usage(stderr, fs)
			return exitError
		}
		fmt.Fprintf(stderr, "ccnavi: %v\n", err)
		record(stderr, log, audit.Record{
			Mode:     string(mode),
			Decision: audit.Skip,
			Reason:   audit.ReasonPayloadUnusable,
		})
		return failClosed(mode)
	}

	rec := audit.Record{
		Mode:    string(mode),
		Event:   string(in.Event),
		Tool:    in.ToolName,
		Subject: subjectOf(in),
		Session: in.SessionID,
	}

	code := decide(ctx, stdout, stderr, mode, conf.Rules, in, &rec)
	record(stderr, log, rec)
	return code
}

// decide reaches the verdict and fills in what happened, so that the record and
// the response are always built from the same conclusion.
func decide(ctx context.Context, stdout, stderr io.Writer, mode Mode, rulesPath string, in *hookio.Input, rec *audit.Record) int {
	if mode == ModeOff {
		rec.Decision, rec.Reason = audit.Skip, audit.ReasonModeOff
		return exitOK
	}
	if in.Event != hookio.PreToolUse {
		// An event we have no checks for is not an error: a registration we did
		// not expect must not stop the work.
		rec.Decision, rec.Reason = audit.Skip, audit.ReasonEventNotChecked
		return exitOK
	}
	if rec.Subject == "" {
		rec.Decision, rec.Reason = audit.Skip, audit.ReasonNoSubject
		return exitOK
	}

	set, problems, err := rules.Load(rulesPath)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: %v\n", err)
		rec.Decision, rec.Reason, rec.Detail = audit.Skip, audit.ReasonRulesUnreadable, rulesPath
		return failClosed(mode)
	}
	for _, p := range problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", p)
	}

	var reasons []string
	for i := range set.Rules {
		if ctx.Err() != nil {
			fmt.Fprintf(stderr, "ccnavi: deadline reached before the decision was complete\n")
			rec.Decision, rec.Reason = audit.Skip, audit.ReasonDeadlineExceeded
			return failClosed(mode)
		}
		if set.Rules[i].Matches(in.ToolName, rec.Subject) {
			reasons = append(reasons, set.Rules[i].Message)
			rec.Rules = append(rec.Rules, set.Rules[i].ID)
		}
	}

	if len(reasons) == 0 {
		rec.Decision, rec.Enforced = audit.Allow, true
		return exitOK
	}

	// Every matching reason goes back at once. Returning one at a time makes the
	// agent fix them one at a time, which costs a round trip each.
	reason := strings.Join(reasons, "\n")

	if mode == ModeWarn {
		rec.Decision, rec.Enforced = audit.Deny, false
		if err := hookio.WriteContext(stdout, hookio.PreToolUse,
			"[ccnavi warn] block mode would have stopped this call:\n"+reason); err != nil {
			fmt.Fprintf(stderr, "ccnavi: %v\n", err)
			return failClosed(mode)
		}
		return exitOK
	}

	rec.Decision, rec.Enforced = audit.Deny, true
	if err := hookio.WriteVerdict(stdout, hookio.Deny, reason); err != nil {
		fmt.Fprintf(stderr, "ccnavi: %v\n", err)
		return failClosed(mode)
	}
	return exitOK
}

// failClosed is the exit code for "the decision could not be made". Block stops
// the call, because a judgment that cannot be reached must not become an allow.
// Warn lets it through: a mode whose whole purpose is to change nothing would be
// worthless if its own failures stopped the work.
func failClosed(mode Mode) int {
	if mode == ModeWarn {
		return exitOK
	}
	return exitBlock
}

// record writes one line. A failure to record is reported and then dropped: it
// must not be able to change whether a tool call runs.
func record(stderr io.Writer, log *audit.Log, rec audit.Record) {
	if err := log.Write(rec); err != nil {
		fmt.Fprintf(stderr, "ccnavi: %v\n", err)
	}
}

// subjectOf picks the field a rule is matched against for this tool.
func subjectOf(in *hookio.Input) string {
	switch in.ToolName {
	case "Bash":
		return in.Field("command")
	case "Read", "Write", "Edit", "MultiEdit", "NotebookEdit":
		return in.Field("file_path")
	default:
		return ""
	}
}

// resolveMode turns the configured value into a mode.
//
// The settings files decide between warn and block, and an edit to them takes
// effect on the next tool call. Only the environment can say off. Those files
// sit in the working tree, the agent can edit them, and the change lands on the
// very next tool call, so an off written there would let the agent switch off
// the thing watching it.
//
// A flag beats both, so a test can pin the mode without depending on the
// environment it happens to run in.
//
// An absent value everywhere means block, so a missing setting is never a
// missing guard.
func resolveMode(stderr io.Writer, flagValue string, conf *settings.Settings) Mode {
	source, value := settings.ModeEnv, conf.Mode
	if flagValue != "" {
		source, value = "--mode", flagValue
	}

	switch Mode(strings.ToLower(value)) {
	case ModeWarn:
		return ModeWarn

	case ModeOff:
		// Off has one route: the environment of whoever started the session,
		// and only when nothing inside the working tree asked for it. Both the
		// settings files and the flag come from places the agent can edit, and
		// an edit there lands on the very next tool call, so honouring off from
		// either would let the agent switch off the thing watching it.
		fromFile := Mode(strings.ToLower(conf.ModeDeclaredInFile))
		fromEnv := Mode(strings.ToLower(conf.ModeFromEnvironment))
		if flagValue == "" && fromEnv == ModeOff && fromFile != ModeOff {
			return ModeOff
		}
		fmt.Fprintf(stderr,
			"ccnavi: %s=off is ignored because it comes from inside the project; "+
				"start the session with %s=off in the environment instead\n", source, settings.ModeEnv)
		return ModeBlock

	case "", ModeBlock:
		return ModeBlock

	default:
		// An unreadable value still lands on the strongest mode, but saying so
		// matters: a renamed or mistyped setting would otherwise look like a
		// deliberate choice, and the guard would tighten for reasons nobody
		// could see.
		fmt.Fprintf(stderr, "ccnavi: %s=%q is not a mode; using %s. Valid modes are %s, %s and %s\n",
			source, value, ModeBlock, ModeOff, ModeWarn, ModeBlock)
		return ModeBlock
	}
}

// defaultRoot finds the project root, the directory that holds .claude.
//
// Nothing here may depend on the working directory as such, because a hook does
// not choose the directory it runs in. The search walks upward instead, so any
// directory inside the project resolves to the same root, and a hook started
// somewhere unexpected still reads the project's configuration.
func defaultRoot() string {
	// Claude Code offers this, but nothing may depend on it being there.
	if dir := os.Getenv("CLAUDE_PROJECT_DIR"); dir != "" {
		return dir
	}
	if root := findProjectRoot(); root != "" {
		return root
	}
	return "."
}

// findProjectRoot walks up from the working directory to the filesystem root
// looking for the .claude directory, the way version control finds its own.
func findProjectRoot() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for {
		if info, err := os.Stat(filepath.Join(dir, ".claude")); err == nil && info.IsDir() {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

func usage(w io.Writer, fs *flag.FlagSet) {
	fmt.Fprint(w, `ccnavi guards agent tool calls and guides the agent to a safer alternative.

It is a hook command, not something to run by hand: it reads one hook payload
as JSON on stdin and writes its response to stdout.

Register it on the tool-call events of your agent, then exercise it with

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push"}}' | ccnavi

Flags:
`)
	fs.PrintDefaults()
	fmt.Fprintf(w, `
Environment, which a project sets through the env block of .claude/settings.json:
  %s	block (default) or warn; off is honoured only from the environment
	of whoever started the session, never from the settings file
  %s	path to the rule file, relative to the project root
  %s	path to the record destination; an empty value turns recording off
`, settings.ModeEnv, settings.RulesEnv, settings.LogEnv)
}
