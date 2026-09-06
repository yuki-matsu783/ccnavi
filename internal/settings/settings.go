// Package settings resolves ccnavi's configuration.
//
// The configuration is carried in environment variables, which a project sets
// through the env block of the agent's own settings file. That keeps one place
// to look, and it is the only place the agent's settings schema leaves open to
// a tool that is not the agent.
//
// The environment alone cannot say where a value came from, so this package
// also reads that settings file. A value declared in it is inside the working
// tree, where the agent can edit it and the change would take effect on the
// very next tool call. Knowing which values came from there is what lets the
// caller refuse the one setting that must never be reachable from a file the
// agent can write: turning the guard off.
package settings

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

// Environment variables ccnavi reads.
const (
	ModeEnv  = "CCNAVI_MODE"
	RulesEnv = "CCNAVI_RULES"
	LogEnv   = "CCNAVI_LOG"
)

// Defaults, relative to the project root.
var (
	DefaultLog   = filepath.Join(".claude", "ccnavi", "log.jsonl")
	DefaultRules = filepath.Join(".claude", "ccnavi", "rules.json")
)

// Settings is the resolved configuration. Every path in it is absolute, so
// nothing later depends on the working directory.
type Settings struct {
	Mode string
	// ModeDeclaredInFile is the mode the project's settings file asks for, or
	// empty if it asks for none. Comparing it against Mode is what tells a
	// value written inside the working tree from one supplied by whoever
	// started the session: the two differ only when something outside the file
	// set it.
	ModeDeclaredInFile string

	Log   string
	Rules string
}

// Load resolves the configuration for a project rooted at root, reading the
// agent's settings file at settingsPath to learn which values it declared.
//
// A missing or unreadable settings file is not a failure. The defaults have to
// carry a fresh checkout on their own, or installing ccnavi would mean editing
// a file before the guard works at all.
func Load(root, settingsPath string) (*Settings, []string, error) {
	s := &Settings{
		Log:   filepath.Join(root, DefaultLog),
		Rules: filepath.Join(root, DefaultRules),
	}

	declared, err := declaredEnv(settingsPath)
	if err != nil {
		// Report it, but carry on with the environment as given. Refusing to
		// judge because a file will not parse would let a broken settings file
		// disable the guard.
		return s.withEnv(root), nil, err
	}

	s.ModeDeclaredInFile = declared[ModeEnv]
	return s.withEnv(root), nil, nil
}

// withEnv folds the process environment in. The environment is the only source,
// so a value the settings file declared arrives here the same way as one from
// the session's own environment; what the file declared only says where it was
// written.
func (s *Settings) withEnv(root string) *Settings {
	s.Mode = os.Getenv(ModeEnv)
	if p := os.Getenv(RulesEnv); p != "" {
		s.Rules = resolve(root, p)
	}
	if p, ok := os.LookupEnv(LogEnv); ok {
		// An explicit empty value is how recording is turned off.
		if p == "" {
			s.Log = ""
		} else {
			s.Log = resolve(root, p)
		}
	}
	return s
}

// declaredEnv returns the env block of the agent's settings file.
func declaredEnv(path string) (map[string]string, error) {
	raw, err := os.ReadFile(path)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return nil, nil
	case err != nil:
		return nil, fmt.Errorf("read settings: %w", err)
	}

	var file struct {
		Env map[string]string `json:"env"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		return nil, fmt.Errorf("parse settings %s: %w", path, err)
	}
	return file.Env, nil
}

func resolve(root, path string) string {
	if filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(root, path)
}
