export class OrderRepo {
  constructor() {
    this.orders = [];
  }

  save(order) {
    const saved = { ...order, id: this.orders.length + 1 };
    this.orders.push(saved);
    return saved;
  }

  byCustomer(customerId) {
    return this.orders.filter((o) => o.customerId === customerId);
  }
}
