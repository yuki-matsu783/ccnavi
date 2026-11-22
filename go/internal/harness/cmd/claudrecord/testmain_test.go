package main

import (
	"testing"

	"ccnavi/internal/harness/base"
)

// TestMain は base.Main を呼ぶ（clitest/doc.go）。ここのテストは本物に繋がない純粋な部分だけを見る。
func TestMain(m *testing.M) { base.Main(m) }
