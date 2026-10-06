import { Product } from "../domain/product.js";

/** Products in memory (the SQL schema is in db/migration). */
export class ProductRepo {
  constructor(rows = []) {
    this.rows = new Map(rows.map((r) => [r.id, new Product(r)]));
  }

  find(id) {
    const p = this.rows.get(id);
    if (!p) throw new Error(`no product ${id}`);
    return p;
  }

  all() {
    return [...this.rows.values()];
  }
}
