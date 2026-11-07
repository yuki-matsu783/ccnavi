// Package archive は、閉じた親子のチケットの退避（ワークスペースルートの logs/archive/<リポジトリ>/）を持つ。
// ready が Draft を外す直前に、親のツリーの承認済みの領域から、閉じた親のファイルを退避へ移す。移すのは、done/ の親と子、
// phases/<親>/ の下、flows/ の子のフローである。親のツリーでは、git 上の削除にする。退避の下の構成は承認済みの領域と
// 同じである。
//
// logs/ は git が追跡しない。そのため退避は、手元の機械にだけ残るバックアップになる。読むのは、子の連番、ready の
// 打ち直し、ボードと親のワークツリーの見分け、write-and-push の見分け（ready のマーカー）だけである。
//
// リンクは辿らない。置き場の途中（logs・archive・<リポジトリ>・その下）がリンクなら読まず、書きもしない。退避の側の
// 書き込みは write-and-push の一覧に載らない（リポジトリの外）ので、fsio を通さずに書く。
//
// Python からの対応: archive を移した。ids → IDs、find（project=None）→ FindAny、closed_tickets → ClosedTickets、
// archived_parent → ArchivedParent、archived_name → ArchivedName、archived_fields → ArchivedFields、same_id →
// ids.SameID、closed_parents → ClosedParents、archived_parents → ArchivedParents、plan → PlanMove（Plan）、move →
// Move、archived_bytes → ArchivedBytes、holds → Holds、in_ready_places → InReadyPlaces、tree_head → TreeHead、
// ready_files → ReadyFiles、ready_started → ReadyStarted、base_dir → BaseDir。置き場の名前は place の
// ArchiveDir・PhasesDir・FlowsDir・ReadyDir。
package archive
