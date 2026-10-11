import { test } from "node:test";
import assert from "node:assert/strict";
import { ticketControlOf } from "../../src/core/ticket-control.js";
import { fixture } from "../helpers/fixture.js";

function withValue(value: string) {
  const board = fixture();
  return { ...board, settings: { ...board.settings, ticket_control: value } };
}

test("CB-T55 実行ファイルが enable と答えれば enable", () => {
  assert.equal(ticketControlOf(withValue("enable")), "enable");
});

test("CB-T56 実行ファイルが disable と答えたときだけ disable", () => {
  assert.equal(ticketControlOf(withValue("disable")), "disable");
});

test("CB-T57 ボードが読めない・答えが空なら enable（入口を消さない）", () => {
  assert.equal(ticketControlOf(undefined), "enable");
  assert.equal(ticketControlOf(withValue("")), "enable");
});
