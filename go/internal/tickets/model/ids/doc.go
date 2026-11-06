// Package ids は識別子とブランチ名の規則を持つ。識別子の字と形、子の識別子の組み立てと読み、issue から親の
// 識別子を作る手順、親のブランチ名と識別子の変換。ファイルを読まない。
//
// 識別子の字の集合（IDChars）は拡張（TS）と sh（ccnavi-branches.sh の jq）が写している。変えるなら両方直す。
//
// Python からの対応: ticket_ids をそのまま移した。id_problem → IDProblem、is_valid_id → IsValidID、
// child_pattern().match → ParseChild、child_id → ChildID、child_tail_pattern().search → HasChildTail、
// form_of → FormOf、direct_name_warning → DirectNameWarning、identifier_number → IdentifierNumber、
// next_serial → NextSerial、issue_identifier_parts → IssueIdentifier（ValueError は error）、branch_to_id →
// BranchToID、adoption_id_problem → AdoptionIDProblem、id_answer → IDAnswer、branch_problem → BranchProblem。
// branch_name_problems はチケットを読むので ticket.Ticket.BranchNameProblems に置いた（部品は FormProblem 以外を
// ここから使う）。
package ids
