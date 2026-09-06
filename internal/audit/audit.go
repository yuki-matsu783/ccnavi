// Package audit writes one line per invocation to an append-only file.
//
// Calls that were let through are written too. A record only means something
// against the calls that produced no record, and that comparison is impossible
// if only the refusals are kept: a guard that stopped running looks exactly
// like a guard that had nothing to complain about.
package audit

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Decision is what happened to the call.
type Decision string

const (
	Allow Decision = "allow"
	Ask   Decision = "ask"
	Deny  Decision = "deny"
	// Skip means no judgment was reached. Skip always carries a Reason, so
	// "not judged" can be told apart from "judged and found nothing".
	Skip Decision = "skip"
)

// Reasons a call went unjudged.
const (
	ReasonModeOff          = "mode-off"
	ReasonEventNotChecked  = "event-not-checked"
	ReasonNoSubject        = "no-subject"
	ReasonPayloadUnusable  = "payload-unusable"
	ReasonRulesUnreadable  = "rules-unreadable"
	ReasonDeadlineExceeded = "deadline-exceeded"
)

// subjectLimit caps the command or path kept in a record. A heredoc can carry
// a whole file, and one call must not be able to bloat the log.
const subjectLimit = 1000

// Record is one line of the log.
type Record struct {
	Time  string `json:"ts"`
	Mode  string `json:"mode"`
	Event string `json:"event,omitempty"`
	Tool  string `json:"tool,omitempty"`

	Subject string `json:"subject,omitempty"`

	// Decision is the judgment that was reached, whatever was done with it.
	Decision Decision `json:"decision"`
	// Enforced says whether the judgment was applied to the call. Warn mode
	// reaches a Deny and leaves Enforced false, which is what makes one file
	// answer both "what would this stop" and "what did this stop". Counting
	// only the calls that were actually blocked would hide the whole point of
	// running in warn mode first.
	Enforced bool `json:"enforced"`

	Reason string `json:"reason,omitempty"`
	// Detail carries what the reason alone cannot say, such as the path that
	// could not be read. Without it a broken install shows up as a reason with
	// no way to tell which file it meant.
	Detail  string   `json:"detail,omitempty"`
	Rules   []string `json:"rules,omitempty"`
	Session string   `json:"session,omitempty"`
	Millis  float64  `json:"ms"`
}

// Log appends records to a file. A zero path disables writing, which is how a
// diagnostic run avoids leaving a trail behind.
type Log struct {
	path  string
	start time.Time
}

// Open names the file to append to and starts the clock for the elapsed time.
func Open(path string) *Log {
	return &Log{path: path, start: time.Now()}
}

// Write appends one record. It fills in the timestamp and the elapsed time, so
// a caller only supplies what it learned.
//
// A failure here is reported but never changes the verdict. Losing a line of
// the log is a smaller harm than letting a bookkeeping problem decide whether
// a tool call runs.
func (l *Log) Write(r Record) error {
	if l == nil || l.path == "" {
		return nil
	}

	now := time.Now()
	r.Time = now.Format(time.RFC3339Nano)
	r.Millis = float64(now.Sub(l.start).Microseconds()) / 1000

	if n := len([]rune(r.Subject)); n > subjectLimit {
		r.Subject = string([]rune(r.Subject)[:subjectLimit]) + fmt.Sprintf("…(+%d)", n-subjectLimit)
	}

	line, err := json.Marshal(r)
	if err != nil {
		return fmt.Errorf("encode audit record: %w", err)
	}
	line = append(line, '\n')

	if err := os.MkdirAll(filepath.Dir(l.path), 0o755); err != nil {
		return fmt.Errorf("create audit directory: %w", err)
	}

	f, err := os.OpenFile(l.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return fmt.Errorf("open audit log: %w", err)
	}

	// Hooks on one event run at the same time, so several processes append to
	// this file at once. The whole line goes out in a single write to an
	// append-only handle, which is what keeps two records from interleaving.
	_, writeErr := f.Write(line)
	closeErr := f.Close()

	switch {
	case writeErr != nil:
		return fmt.Errorf("write audit log: %w", writeErr)
	case closeErr != nil:
		return fmt.Errorf("close audit log: %w", closeErr)
	}
	return nil
}
