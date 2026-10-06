import { listProducts } from "../app/catalog.js";
import { checkout } from "../app/checkout.js";
import { Cart } from "../domain/cart.js";

/** The shop's HTTP routes, as plain functions (the server wires them to paths). */
export function routes({ products, orders }) {
  return {
    "GET /products": () => listProducts(products),
    "POST /checkout": ({ customerId, items }) => {
      const cart = new Cart(customerId);
      for (const { productId, quantity } of items) cart.add(products.find(productId), quantity);
      return checkout(cart, orders);
    },
  };
}
