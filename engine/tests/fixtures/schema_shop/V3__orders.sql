create table cart (
    id          uuid primary key,
    user_id     uuid references app_user (id) on delete set null,
    created_at  timestamp with time zone not null default now()
);

create table cart_item (
    cart_id     uuid   not null references cart (id) on delete cascade,
    product_id  bigint not null references product (id),
    quantity    integer not null default 1,
    primary key (cart_id, product_id)
);

create table coupon (
    code        varchar(32) primary key,
    tenant_id   uuid not null references tenant (id),
    percent_off smallint,
    valid_until date
);

create table customer_order (
    id                bigserial primary key,
    tenant_id         uuid not null references tenant (id),
    user_id           uuid not null references app_user (id),
    shipping_address  bigint references address (id),
    billing_address   bigint references address (id),
    coupon_code       varchar(32) references coupon (code),
    status            varchar(16) not null default 'new',
    total_cents       integer not null,
    placed_at         timestamp with time zone not null default now()
);
create index ix_order_user on customer_order (user_id, placed_at desc);

create table order_line (
    order_id    bigint  not null references customer_order (id) on delete cascade,
    line_no     smallint not null,
    product_id  bigint  not null references product (id),
    quantity    integer not null,
    price_cents integer not null,
    primary key (order_id, line_no)
);

create table shipment (
    id            bigserial primary key,
    order_id      bigint not null references customer_order (id),
    warehouse_id  integer not null references warehouse (id),
    carrier       varchar(40),
    tracking_no   varchar(80),
    shipped_at    timestamp with time zone
);

create table shipment_line (
    shipment_id bigint   not null references shipment (id) on delete cascade,
    order_id    bigint   not null,
    line_no     smallint not null,
    quantity    integer  not null,
    primary key (shipment_id, order_id, line_no),
    foreign key (order_id, line_no) references order_line (order_id, line_no)
);
