/* The business marker colour. The token lives in tokens.css; this only names it. */
export type Marker = 'livery' | 'software' | 'farm' | 'camping';

export function markerVar(marker?: Marker): string | undefined {
  return marker ? `var(--marker-${marker})` : undefined;
}

/* The inline style that hands a block its marker colour. Colour only, never position. */
export function markerStyle(marker?: Marker): string | undefined {
  const v = markerVar(marker);
  return v ? `--marker: ${v}` : undefined;
}
