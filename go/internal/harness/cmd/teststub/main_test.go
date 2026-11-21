package main

import (
	"bytes"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

func TestMain(m *testing.M) { base.Main(m) }

// 知らないサブコマンドは使い方を言って 2 で終わる。名前は表（commands）から並べる。
func TestUnknownSubcommandSaysTheUsage(t *testing.T) {
	t.Parallel()
	cases := [][]string{{}, {"nope"}, {"gitlab-host"}, {"github-host", "nope"}}
	for _, args := range cases {
		var stderr bytes.Buffer
		if code := run(args, strings.NewReader(""), &bytes.Buffer{}, &stderr); code != 2 || !strings.Contains(stderr.String(), "使い方: teststub ") {
			t.Errorf("%v: code = %d, stderr = %q", args, code, stderr.String())
		}
	}
	for name := range commands {
		var stderr bytes.Buffer
		run(nil, strings.NewReader(""), &bytes.Buffer{}, &stderr)
		if !strings.Contains(stderr.String(), name) {
			t.Errorf("使い方に %s が無い: %q", name, stderr.String())
		}
	}
}
