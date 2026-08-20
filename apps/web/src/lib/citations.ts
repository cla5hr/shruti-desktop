/** Split assistant text on [m:ss] / [h:mm:ss] citations so the UI can render
 *  them as click-to-seek buttons. */

export type Chunk =
  | { type: "text"; value: string }
  | { type: "cite"; label: string; ms: number };

const CITE = /\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g;

export function splitCitations(text: string): Chunk[] {
  const out: Chunk[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE)) {
    if (m.index! > last) out.push({ type: "text", value: text.slice(last, m.index) });
    const [a, b, c] = [Number(m[1]), Number(m[2]), m[3] === undefined ? null : Number(m[3])];
    const ms = c === null ? (a * 60 + b) * 1000 : (a * 3600 + b * 60 + c) * 1000;
    out.push({ type: "cite", label: m[0], ms });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push({ type: "text", value: text.slice(last) });
  return out;
}
