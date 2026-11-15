package cli

import (
	"bytes"
	"testing"
)

// `host mask`（sh の ccnavi_mask_url が聞く）。トークンに `@` が入っても、ホストの手前の最後の `@` まで伏せる。
// scheme の付いた形と、scheme の無い `user:token@host:path` の形の両方を見る（doctor の写しと同じ表）。
func TestHostMask(t *testing.T) {
	for _, c := range []struct{ url, want string }{
		{"https://oauth2:SEC@RET@host.example/g/p.git", "https://<伏せた>@host.example/g/p.git"},
		{"oauth2:SEC@RET@host.example:g/p.git", "<伏せた>@host.example:g/p.git"},
		{"git@host.example:g/p.git", "git@host.example:g/p.git"},
	} {
		var out, errs bytes.Buffer
		if code := runHost(&out, &errs, []string{"mask", c.url}); code != 0 || out.String() != "masked "+c.want+"\n" {
			t.Errorf("%s: %d %q %q", c.url, code, out.String(), errs.String())
		}
	}
}
