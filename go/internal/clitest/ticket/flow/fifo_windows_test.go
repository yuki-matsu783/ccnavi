//go:build windows

package flow

import (
	"errors"
	"time"
)

const hasFifo = false

func mkfifo(string) error { return errors.New("mkfifo が無い") }

func withFifoWatchdog(_ string, _ time.Duration, fn func()) bool { fn(); return false }
