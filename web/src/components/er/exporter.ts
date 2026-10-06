// Export a drawn diagram as a standalone SVG or PNG: the scene is cloned with every colour and font the page's
// CSS gave it written into the elements, so the file looks the same outside keel (in either theme it was taken in).

const PROPS = ["fill", "fill-opacity", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "stroke-opacity",
  "opacity", "font-family", "font-size", "font-weight", "font-style", "letter-spacing", "color", "paint-order", "display"];

function inline(src: Element, dst: Element) {
  const cs = getComputedStyle(src);
  const style = PROPS.map((p) => {
    const v = cs.getPropertyValue(p);
    return v ? `${p}:${v}` : "";
  }).filter(Boolean).join(";");
  if (style) dst.setAttribute("style", style);
  for (const a of ["tabindex", "role", "aria-label", "aria-pressed", "aria-describedby", "class", "data-tid"]) dst.removeAttribute(a);
  for (let i = 0; i < src.children.length; i++) inline(src.children[i], dst.children[i]);
}

/** The scene <g> (untransformed) and <defs> -> an SVG document string showing the world box `b`. */
export function toSvg(scene: SVGGElement, defs: SVGDefsElement | null, b: { x: number; y: number; w: number; h: number }, background: string): string {
  const pad = 24;
  const { w, h } = b;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("xmlns", NS);
  svg.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
  svg.setAttribute("viewBox", `${Math.floor(b.x - pad)} ${Math.floor(b.y - pad)} ${Math.ceil(w + 2 * pad)} ${Math.ceil(h + 2 * pad)}`);
  svg.setAttribute("width", String(Math.ceil(w + 2 * pad)));
  svg.setAttribute("height", String(Math.ceil(h + 2 * pad)));
  const bg = document.createElementNS(NS, "rect");
  bg.setAttribute("x", String(Math.floor(b.x - pad)));
  bg.setAttribute("y", String(Math.floor(b.y - pad)));
  bg.setAttribute("width", String(Math.ceil(w + 2 * pad)));
  bg.setAttribute("height", String(Math.ceil(h + 2 * pad)));
  bg.setAttribute("fill", background);
  svg.appendChild(bg);
  if (defs) {
    const d = defs.cloneNode(true) as Element;
    inline(defs, d);
    svg.appendChild(d);
  }
  const g = scene.cloneNode(true) as Element;
  inline(scene, g);
  g.removeAttribute("transform");
  svg.appendChild(g);
  // <use href> is SVG 2; older viewers want xlink:href too
  svg.querySelectorAll("use").forEach((u) => {
    const href = u.getAttribute("href");
    if (href) u.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", href);
  });
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(svg);
}

export function download(name: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function svgToPng(svg: string, w: number, h: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const scale = Math.max(0.25, Math.min(2, 12000 / Math.max(w, h)));
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = Math.round(w * scale);
      c.height = Math.round(h * scale);
      const ctx = c.getContext("2d");
      if (!ctx) return reject(new Error("This browser cannot draw a PNG."));
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, w, h);
      c.toBlob((b) => (b ? resolve(b) : reject(new Error("The PNG came out empty."))), "image/png");
    };
    img.onerror = () => reject(new Error("The diagram could not be drawn as an image."));
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  });
}
