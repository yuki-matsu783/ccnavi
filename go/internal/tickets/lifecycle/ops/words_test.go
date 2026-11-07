package ops

import (
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/ticket"
)

// start の案内は、親には識別子の名前のブランチを作らせず ccnavi-start.sh を指す。子は親のブランチから -b <子>。
func TestCreateWords(t *testing.T) {
	parent := createWords("/r", &ticket.Ticket{ID: "i0001"}, "/w/p")
	if !strings.Contains(parent, "ccnavi-start.sh --mr <MR の番号>") || strings.Contains(parent, "-b ") {
		t.Errorf("親: %s", parent)
	}
	child := createWords("/r", &ticket.Ticket{ID: "i0001-01-01", Parent: "i0001", Phase: 1}, "/w/c")
	if !strings.Contains(child, "-b i0001-01-01") {
		t.Errorf("子: %s", child)
	}
}
