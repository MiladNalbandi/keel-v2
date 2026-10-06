create table category (
    id         serial primary key,
    tenant_id  uuid not null references tenant (id),
    parent_id  integer references category (id),
    name       varchar(120) not null
);

create table brand (
    id    serial primary key,
    name  varchar(120) not null unique
);

create table product (
    id           bigserial primary key,
    tenant_id    uuid not null references tenant (id) on delete cascade,
    category_id  integer references category (id) on delete set null,
    brand_id     integer references brand (id),
    sku          varchar(40)  not null,
    title        varchar(200) not null,
    description  text,
    price_cents  integer not null check (price_cents >= 0),
    currency     char(3) not null default 'EUR',
    active       boolean not null default true,
    created_at   timestamp with time zone not null default now(),
    updated_at   timestamp with time zone not null default now(),
    constraint uq_product_sku unique (tenant_id, sku)
);
create index ix_product_category on product (category_id);
create index ix_product_title on product using gin (to_tsvector('simple', title));

create table product_image (
    id          bigserial primary key,
    product_id  bigint not null references product (id) on delete cascade,
    url         varchar(500) not null,
    position    smallint not null default 0
);

create table tag (
    id    serial primary key,
    name  varchar(60) not null unique
);

create table product_tag (
    product_id bigint  not null references product (id) on delete cascade,
    tag_id     integer not null references tag (id) on delete cascade,
    primary key (product_id, tag_id)
);

create table warehouse (
    id         serial primary key,
    tenant_id  uuid not null references tenant (id),
    code       varchar(16) not null,
    city       varchar(100)
);

create table stock (
    warehouse_id integer not null,
    product_id   bigint  not null,
    quantity     integer not null default 0,
    reserved     integer not null default 0,
    constraint pk_stock primary key (warehouse_id, product_id),
    constraint fk_stock_warehouse foreign key (warehouse_id) references warehouse (id) on delete cascade,
    constraint fk_stock_product foreign key (product_id) references product (id) on delete cascade
);
