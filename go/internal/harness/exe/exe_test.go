package exe

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/env"
)

func TestMain(m *testing.M) { base.Main(m) }

// 組んだ実行ファイルは、指紋の置き場に置かれ、互換の版を言う。環境変数があればそれを使う。
func TestPathBuildsOnceUnderTheFingerprint(t *testing.T) {
	if os.Getenv(env.TestExe) != "" {
		t.Skip("実行ファイルを渡されている")
	}
	path := Path(t)
	fp, err := fingerprint()
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(base.TempBase(), "ccnavi-test-exe", fp, "ccnavi"+suffix()); path != want {
		t.Errorf("Path = %q, want %q", path, want)
	}
	if Path(t) != path {
		t.Error("2 回目で別の物を返した")
	}
	if Compat(t) == "" {
		t.Error("compat が空")
	}
}

func TestHostTarget(t *testing.T) {
	t.Parallel()
	if got := HostTarget(); got == "" || got[0] == '-' {
		t.Errorf("HostTarget = %q", got)
	}
}
