// keel 0.18.63 KeelBot's "Switch to Edit" option: keel-questions keeps an option's "mode", and pickedMode finds it.
import { describe, expect, it } from "vitest";
import { parseQuestions, pickedMode } from "../questions";

describe("an option that switches KeelBot's mode", () => {
  const qs = parseQuestions('[{"id": "next", "question": "How do we go on?", "options": ['
    + '{"label": "Switch to Edit and fix it", "mode": "edit"}, {"label": "Leave it"}, {"label": "Odd", "mode": "side"}]}]');

  it("keeps edit or ask, and nothing else", () => {
    expect(qs[0].options.map((o) => o.mode)).toEqual(["edit", undefined, undefined]);
  });

  it("the picked option's mode, or null", () => {
    expect(pickedMode(qs, { next: "Switch to Edit and fix it" })).toBe("edit");
    expect(pickedMode(qs, { next: "Leave it" })).toBeNull();
    expect(pickedMode(qs, { next: "my own words" })).toBeNull();
    expect(pickedMode(qs, {})).toBeNull();
  });
});
