import { test } from "node:test";
import assert from "node:assert/strict";
import { Cart } from "../src/domain/cart.js";
import { Product } from "../src/domain/product.js";

const mug = new Product({ id: 1, sku: "MUG", name: "Mug", priceCents: 1250, stock: 5 });

test("a cart adds up its lines", () => {
  const cart = new Cart(7);
  cart.add(mug, 2);
  assert.equal(cart.totalCents(), 2500);
});

test("a cart refuses what is not in stock", () => {
  assert.throws(() => new Cart(7).add(mug, 9), /out of stock/);
});
