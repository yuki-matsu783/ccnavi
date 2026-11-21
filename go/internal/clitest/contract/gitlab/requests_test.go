package gitlab

import (
	"encoding/json"
	"maps"
	"slices"
	"strings"
	"testing"

	cs "ccnavi/internal/harness/contractshape"
)

// 拡張だけの要求が使う名前。本物の側の Scene.build と TS の mock() が積む（模擬は持たない）。
var chromeScene = []string{"sha.main", "sha.feature", "blob.sha"}

// 2xx 以外を期待する要求（拒否の形を録る）。
var errorStatus = map[string]int{"error_401": 401, "error_404": 404, "branch_404": 404, "branch_exists": 400}

func TestRequestsFormat(t *testing.T) {
	t.Parallel()
	requests := loadAll(t)
	seen := map[string]bool{}
	for _, r := range requests {
		if seen[r.Name] {
			t.Errorf("名前が重複している: %s", r.Name)
		}
		seen[r.Name] = true
	}
	if len(requests) < 23 {
		t.Errorf("要求が %d 件しかない", len(requests))
	}
	known := slices.Collect(maps.Keys(cs.SceneContext()))
	known = append(known, "user.id", "discussion.id")
	known = append(known, chromeScene...)
	for _, r := range requests {
		t.Run(r.Name, func(t *testing.T) {
			if !slices.Contains([]string{"GET", "POST", "PUT"}, r.Method) {
				t.Errorf("method = %s", r.Method)
			}
			if r.Token != "main" && r.Token != "none" {
				t.Errorf("token = %s", r.Token)
			}
			if len(r.UsedBy) == 0 {
				t.Error("used_by が空")
			}
			for _, u := range r.UsedBy {
				if !slices.Contains([]string{"sh", "chrome", "scenarios"}, u) {
					t.Errorf("used_by に知らない値 %s", u)
				}
			}
			text, _ := json.Marshal(r)
			names := cs.Placeholders(string(text))
			for _, n := range names {
				if !slices.Contains(known, n) {
					t.Errorf("場面に無い置き換えの名前 %s", n)
				}
				if r.UsedByAny("sh", "scenarios") && slices.Contains(chromeScene, n) {
					t.Errorf("模擬に無い名前 %s を sh・scenarios の要求で使えない", n)
				}
			}
			if strings.Contains(string(text), "/projects/ns") {
				t.Error("実体の名前を入れない（${project} を使う）")
			}

			// 期待のステータス: 拒否の形を録る要求のほかは、読みが 200、作りが 201。
			want, ok := errorStatus[r.Name]
			if !ok {
				want = 200
				if r.Method == "POST" {
					want = 201
				}
			}
			if r.ExpectStatus != want {
				t.Errorf("expect_status = %d, want %d", r.ExpectStatus, want)
			}

			// reads: 読むキーを必ず書く。形から落とす欄は読むキーにできない。
			if len(r.Reads) == 0 {
				t.Error("読むキーが無い要求は reads を空にせず、理由を書く")
			}
			if len(slices.Compact(slices.Sorted(slices.Values(r.Reads)))) != len(r.Reads) {
				t.Error("reads に重複がある")
			}
			for _, key := range r.Reads {
				parts := strings.Split(strings.ReplaceAll(key, "[]", "."), ".")
				if last := parts[len(parts)-1]; cs.IsDropped(last) {
					t.Errorf("形から落とす欄 %s は読むキーにできない", last)
				}
			}
		})
	}
}

func TestRequestsLayout(t *testing.T) {
	t.Parallel()
	all := loadAll(t)
	names := map[string]bool{}
	var wantFake []string
	var methods []string
	for _, r := range all {
		names[r.Name] = true
		methods = append(methods, r.Method)
		if r.UsedByAny("sh", "scenarios") {
			wantFake = append(wantFake, r.Name)
		}
	}
	for _, n := range cs.NonEmpty {
		if !names[n] {
			t.Errorf("NonEmpty の %s が要求に無い", n)
		}
	}

	// 模擬に送るのは sh か scenarios が使う要求だけ。拡張だけの要求は 1 件以上ある。
	var fake []string
	for _, r := range loadFake(t) {
		fake = append(fake, r.Name)
	}
	if !slices.Equal(wantFake, fake) {
		t.Errorf("模擬に送る要求 = %v, want %v", fake, wantFake)
	}
	if len(wantFake) == len(all) {
		t.Error("拡張だけの要求が 1 件も無い")
	}

	// 書き込みは読みのあとにまとめる（読みの形が前の書き込みで変わらないように）。
	firstWrite := slices.IndexFunc(methods, func(m string) bool { return m != "GET" })
	if firstWrite < 0 {
		t.Fatal("書き込みが無い")
	}
	if slices.Contains(methods[firstWrite:], "GET") {
		t.Errorf("書き込みのあとに読みがある: %v", methods)
	}
}
