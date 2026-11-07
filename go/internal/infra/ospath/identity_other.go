//go:build !linux && !darwin && !windows

package ospath

// Identity は配る 3 つの OS 以外では読まない（false）。
func Identity(path string) (ino uint64, ctimeNs int64, ok bool) { return 0, 0, false }
