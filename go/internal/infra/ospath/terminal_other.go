//go:build !linux && !darwin && !windows

package ospath

// IsTerminal は配る 3 つの OS 以外ではターミナルと見なさない。
func IsTerminal(fd uintptr) bool { return false }
