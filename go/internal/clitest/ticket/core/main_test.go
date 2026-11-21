package core

import (
	"flag"
	"testing"

	"ccnavi/internal/harness/base"
)

// このパッケージのテストは、ほとんどの時間を子のプロセス（ccnavi・git）の終わりを待って過ごす。CPU の数（go test の -parallel の
// 既定）では待ちが重なり切らないので、既定を上げる（-parallel を渡せばそちらが優先）。
const waitingParallel = "16"

func TestMain(m *testing.M) {
	_ = flag.Set("test.parallel", waitingParallel)
	base.Main(m)
}
