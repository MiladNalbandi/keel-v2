import { format } from "../domain/money.js";

/** Turns a cart into an order: checks stock, takes it, saves the order. */
export function checkout(cart, orders, now = new Date()) {
  if (cart.items.size === 0) throw new Error("the cart is empty");
  for (const { product, quantity } of cart.items.values()) {
    if (!product.inStock(quantity)) throw new Error(`${product.name} is out of stock`);
  }
  for (const { product, quantity } of cart.items.values()) product.stock -= quantity;
  const total = cart.totalCents();
  const order = orders.save({ customerId: cart.customerId, totalCents: total, status: "new", placedAt: now.toISOString() });
  return { ...order, total: format(total) };
}
