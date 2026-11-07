package fsio

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestScanTail(t *testing.T) {
	path := filepath.Join(t.TempDir(), "log")
	if err := os.WriteFile(path, []byte("aaaa\nbb\ncc\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	var seen []string
	err := ScanTail(path, 4, 64, 2, func(tail []byte, whole bool) bool {
		seen = append(seen, string(tail)+"|"+map[bool]string{true: "whole", false: "part"}[whole])
		return strings.Contains(string(tail), "bb")
	})
	// 4 バイトでは先頭の欠けた行を捨てて "cc" だけ、8 バイトで "bb" が入る。
	want := []string{"cc\n|part", "bb\ncc\n|part"}
	if err != nil || strings.Join(seen, ",") != strings.Join(want, ",") {
		t.Errorf("ScanTail = %q, %v", seen, err)
	}
	seen = nil
	_ = ScanTail(path, 4, 64, 4, func(tail []byte, whole bool) bool {
		seen = append(seen, string(tail))
		return false
	})
	if len(seen) != 2 || seen[1] != "aaaa\nbb\ncc\n" {
		t.Errorf("全体を読んだら止まらない: %q", seen)
	}
	if err := ScanTail(filepath.Join(t.TempDir(), "none"), 4, 64, 2, func([]byte, bool) bool { return true }); err == nil {
		t.Error("無いファイルでエラーにならない")
	}
}

func TestRecordPathAndISOFormat(t *testing.T) {
	if got := RecordPath("st", "busy-", "a/b", JSONExt); got != filepath.Join("st", "busy-a_b.json") {
		t.Errorf("RecordPath = %q", got)
	}
	zone := time.FixedZone("", 9*3600)
	if got := ISOFormat(time.Date(2026, 1, 2, 3, 4, 5, 0, zone)); got != "2026-01-02T03:04:05+09:00" {
		t.Errorf("ISOFormat = %q", got)
	}
	if got := ISOFormat(time.Date(2026, 1, 2, 3, 4, 5, 1500, zone)); got != "2026-01-02T03:04:05.000001+09:00" {
		t.Errorf("ISOFormat = %q", got)
	}
}
