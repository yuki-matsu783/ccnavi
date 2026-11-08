package hookio

// IdleHandoffMarker は、ccnavi 自身が放置したセッションを起こす指示の先頭に付ける目印。
// 起こす側（records/handoff）が付け、ここでユーザの発言ではない prompt として除く。
const IdleHandoffMarker = "[idle-handoff]"
