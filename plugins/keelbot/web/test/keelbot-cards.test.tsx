// KeelBot's slot keelbot.card (step 3): a block of a kind no KeelBot card handles is the card a plugin put in the slot
// for that kind (the Database plugin's keel-query: plugins/db/web/test); without one, the block shows as its code.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { registerSlot, SLOTS, type KeelbotCardItem, type KeelbotCardProps } from "@keel/web-sdk";
import { SlotCard, splitActions } from "../Actions";

describe("KeelBot's cards from plugins (keelbot.card)", () => {
  it("reads the plugins' blocks (keel-query, keel-git) among the others", () => {
    const segs = splitActions('Here:\n```keel-query\n{"sql": "select 1"}\n```\n```keel-git\n{"op": "push"}\n```');
    expect(segs.map((x) => x.kind)).toEqual(["text", "query", "git"]);
  });

  it("a plugin's card for the block's kind gets the block and the project", () => {
    const off = registerSlot<KeelbotCardItem>(SLOTS.keelbotCard, {
      id: "test",
      kind: "keel-test",
      component: ({ block, pid }: KeelbotCardProps) => (
        <p>
          {pid}: {block.kind} {block.body}
        </p>
      ),
    });
    try {
      const view = render(
        <SlotCard
          pid="ludus-engine"
          block={{ kind: "keel-test", body: '{"op": "x"}' }}
        />,
      );
      expect(
        screen.getByText('ludus-engine: keel-test {"op": "x"}'),
      ).toBeInTheDocument();
      view.unmount();
    } finally {
      off();
    }
  });

  it("without a card for its kind, the block shows as its code", () => {
    const { container } = render(
      <SlotCard
        pid="ludus-engine"
        block={{ kind: "keel-nope", body: '{"op": "x"}' }}
      />,
    );
    expect(container.querySelector("pre, code")).not.toBeNull();
    expect(container).toHaveTextContent('{"op": "x"}');
    expect(screen.queryByRole("region")).toBeNull();
  });
});
