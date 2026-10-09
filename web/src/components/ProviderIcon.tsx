// v0.15.2 Small marks for the model providers (the model picker, and any place that names a provider). Each is a
// simple shape in the company's colour, drawn inline: a hint that is easy to tell apart, not the company's real logo.
// The colours live in styles/models.css, so the marks read well in the light and the dark theme.

export type Brand = "anthropic" | "openai" | "github" | "google" | "openrouter" | "local" | "fake" | "other";

const BRANDS: [RegExp, Brand][] = [
  [/claude|anthropic/, "anthropic"],
  [/codex|openai|gpt/, "openai"],
  [/copilot|github/, "github"],
  [/gemini|google|vertex/, "google"],
  [/openrouter/, "openrouter"],
  [/ollama|local|lmstudio|llama/, "local"],
  [/fake|mock|^test$/, "fake"],
];
const find = (s: string) => BRANDS.find(([re]) => re.test(s.toLowerCase()))?.[1];

/** The company behind a provider: its id first ("claude", "codex"…), else its label ("Google Gemini"). */
export function brandOf(provider: string, label = ""): Brand {
  return find(provider) ?? find(label) ?? "other";
}

/** The company's name, for a title or a screen reader. */
export const BRAND_NAME: Record<Brand, string> = {
  anthropic: "Anthropic", openai: "OpenAI", github: "GitHub", google: "Google", openrouter: "OpenRouter", local: "Local model", fake: "Test model", other: "Other",
};

const tile = <rect className="pi-bg" x="0.5" y="0.5" width="15" height="15" rx="3.5" />;
const MARKS: Record<Exclude<Brand, "other">, JSX.Element> = {
  // a sunburst: four strokes through the middle
  anthropic: <>{tile}<path className="pi-st" d="M8 3.2v9.6M3.2 8h9.6M4.6 4.6l6.8 6.8M11.4 4.6l-6.8 6.8" /></>,
  // a hexagon ring
  openai: <>{tile}<path className="pi-st" d="M8 3.4l4 2.3v4.6l-4 2.3-4-2.3V5.7z" /></>,
  // a pilot's goggles
  github: <>{tile}<circle className="pi-st" cx="5.4" cy="8.6" r="2.1" /><circle className="pi-st" cx="10.6" cy="8.6" r="2.1" /><path className="pi-st" d="M7.5 8.3h1M4 5.2c1.2-1 2.6-1.4 4-1.4s2.8.4 4 1.4" /></>,
  // a four-pointed sparkle
  google: <>{tile}<path className="pi-fl" d="M8 2.6c.4 3 2.4 5 5.4 5.4-3 .4-5 2.4-5.4 5.4-.4-3-2.4-5-5.4-5.4 3-.4 5-2.4 5.4-5.4z" /></>,
  // one road that splits in two
  openrouter: <>{tile}<path className="pi-st" d="M3.2 8h3.2l2.4-3.2h3.4M6.4 8l2.4 3.2h3.4M10.8 3.4l1.4 1.4-1.4 1.4M10.8 9.8l1.4 1.4-1.4 1.4" /></>,
  // a small llama head: two ears on a round face
  local: <>{tile}<path className="pi-st" d="M5.6 6.4V3.6M10.4 6.4V3.6" /><rect className="pi-st" x="4.2" y="6.2" width="7.6" height="6.6" rx="3.3" /><circle className="pi-fl" cx="6.7" cy="9.2" r=".7" /><circle className="pi-fl" cx="9.3" cy="9.2" r=".7" /></>,
  // a test tube, in a dashed box
  fake: <>{tile}<path className="pi-st" d="M6.2 3.4h3.6M7 3.4v3.4l-2.6 4.6a.9.9 0 0 0 .8 1.3h5.6a.9.9 0 0 0 .8-1.3L9 6.8V3.4" /></>,
};

/** A provider's mark, 16 px by default. Without a title it is decoration (the name sits next to it). */
export function ProviderIcon({ provider, label = "", size = 16, title }: { provider: string; label?: string; size?: number; title?: string }) {
  const b = brandOf(provider, label);
  const letter = (label || provider || "?").trim().charAt(0).toUpperCase();
  return (
    <svg className={`pi pi-${b}`} data-brand={b} viewBox="0 0 16 16" width={size} height={size} focusable="false"
      {...(title ? { role: "img", "aria-label": title } : { "aria-hidden": true })}>
      {b === "other" ? <>{tile}<text className="pi-tx" x="8" y="11.3" textAnchor="middle">{letter}</text></> : MARKS[b]}
    </svg>
  );
}
