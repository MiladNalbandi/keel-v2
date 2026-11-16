// keel 0.28 the menu keeps the six main places and the pins; every page is in All pages (the menu's foot, ⌘K). Tests
// that check a page's group and place open All pages and read its list.
import { fireEvent, screen, within } from "@testing-library/react";

/** Opens All pages and gives its list of links (group by group, in menu order). */
export async function allPagesNav(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "All pages" }));
  return screen.findByRole("navigation", { name: "All pages" });
}

/** The page names in All pages, in order. */
export async function allPageNames(): Promise<string[]> {
  const nav = await allPagesNav();
  return within(nav)
    .getAllByRole("link")
    .map((l) => l.textContent?.trim() ?? "");
}

/** The side menu (main places, pins, recent flows, foot). */
export const sideMenu = () => screen.getByRole("navigation", { name: "Screens" });
