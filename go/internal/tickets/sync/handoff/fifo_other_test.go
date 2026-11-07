//go:build !unix

package handoff

import "errors"

func mkfifo(string) error { return errors.New("名前付きパイプを作れない") }
