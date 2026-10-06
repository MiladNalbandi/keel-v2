import { cents } from "./money.js";

/** What a customer is about to buy: product id -> { product, quantity }. */
export class Cart {
  constructor(customerId) {
    this.customerId = customerId;
    this.items = new Map();
  }

  add(product, quantity = 1) {
    if (!product.inStock(quantity)) throw new Error(`${product.name} is out of stock`);
    const line = this.items.get(product.id) ?? { product, quantity: 0 };
    line.quantity += quantity;
    this.items.set(product.id, line);
  }

  remove(productId) {
    this.items.delete(productId);
  }

  totalCents() {
    let sum = 0;
    for (const { product, quantity } of this.items.values()) sum += product.priceCents * quantity;
    return cents(sum);
  }
}
