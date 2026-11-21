//go:build !windows

package flow

import (
	"os"
	"sync"
	"syscall"
	"time"
)

const hasFifo = true

func mkfifo(path string) error { return syscall.Mkfifo(path, 0o644) }

// withFifoWatchdog は fn を回す。FIFO を読みに行った側が固まっても止まらないよう、seconds 経っても終わらなければ
// 書き手として FIFO を開いて閉じる（読み手は EOF を受けて戻る）。固まって起こしたら true を返す。
func withFifoWatchdog(path string, seconds time.Duration, fn func()) (fired bool) {
	done := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		select {
		case <-done:
		case <-time.After(seconds):
			fired = true
			if f, err := os.OpenFile(path, os.O_WRONLY|syscall.O_NONBLOCK, 0); err == nil {
				_ = f.Close()
			}
		}
	}()
	fn()
	close(done)
	wg.Wait()
	return fired
}
