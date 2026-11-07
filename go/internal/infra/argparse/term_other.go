//go:build !linux && !darwin && !windows

package argparse

func stdoutColumns() (int, bool) { return 0, false }
