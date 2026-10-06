import { test } from "node:test";
import assert from "node:assert/strict";
import { routes } from "../src/web/routes.js";
import { ProductRepo } from "../src/data/productRepo.js";
import { OrderRepo } from "../src/data/orderRepo.js";

test("checkout saves an order with the total and takes the stock", () => {
  const products = new ProductRepo([{ id: 1, sku: "MUG", name: "Mug", priceCents: 1250, stock: 5 }]);
  const orders = new OrderRepo();
  const order = routes({ products, orders })["POST /checkout"]({ customerId: 7, items: [{ productId: 1, quantity: 2 }] });
  assert.equal(order.total, "$25.00");
  assert.equal(products.find(1).stock, 3);
  assert.equal(orders.byCustomer(7).length, 1);
});
