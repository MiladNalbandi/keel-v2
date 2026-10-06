import { format } from "../domain/money.js";

export function listProducts(products) {
  return products.all().map((p) => ({ sku: p.sku, name: p.name, price: format(p.priceCents), inStock: p.stock > 0 }));
}
