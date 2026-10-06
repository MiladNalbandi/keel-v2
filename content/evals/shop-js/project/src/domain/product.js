import { cents } from "./money.js";

export class Product {
  constructor({ id, sku, name, priceCents, stock = 0 }) {
    this.id = id;
    this.sku = sku;
    this.name = name;
    this.priceCents = cents(priceCents);
    this.stock = stock;
  }

  inStock(quantity) {
    return this.stock >= quantity;
  }
}
