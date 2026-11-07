//go:build !windows && !js

package platformtag

import "syscall"

// executable は os.access(path, X_OK)。
func executable(path string) bool {
	const xOK = 1
	return syscall.Access(path, xOK) == nil
}
