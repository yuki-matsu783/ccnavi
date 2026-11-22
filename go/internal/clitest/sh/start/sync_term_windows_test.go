//go:build windows

package start

import "testing"

// dash も、プロセスのまとまりへ TERM を送る手段も Windows には無い。
func TestSyncATerminatedRunReleasesItsLockUnderDash(t *testing.T) {
	t.Skip("Windows にプロセスグループへの TERM が無い")
}
