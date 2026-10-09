// KeelBot's slot keelbot.card (step 3): a block of a kind no core card handles is the card a plugin put in the slot for
// that kind (the Database plugin's keel-query: plugins/db/web/test); without one, the block shows as its code.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SlotCard } from "../components/helper/Actions";
import { registerSlot } from "../sdk/registry";
import {
  SLOTS,
  type KeelbotCardItem,
  type KeelbotCardProps,
} from "../sdk/slots";

describe("KeelBot's cards from plugins (keelbot.card)", () => {
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
