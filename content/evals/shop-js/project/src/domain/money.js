/** Money in cents, so sums never round. */
export function cents(n) {
  if (!Number.isInteger(n) || n < 0) throw new Error(`not an amount: ${n}`);
  return n;
}

export function format(c) {
  return `$${(c / 100).toFixed(2)}`;
}
