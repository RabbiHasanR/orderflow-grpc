# OrderFlow gRPC — Architecture

A learning project for **gRPC service-to-service communication** between two
backend frameworks, deployed with Docker Compose.

- **order-service** (FastAPI) — public REST entrypoint, acts as a **gRPC client**.
- **inventory-service** (Django) — internal **gRPC server**, runs as two replicas
  to demonstrate **client-side load balancing**.

The whole point: a REST call to FastAPI internally fans out to Django over gRPC,
with each service owning its own Postgres database.

---

## 1. Goals

What this project is meant to teach / demonstrate:

1. **Cross-framework gRPC** — FastAPI (async client) ↔ Django (async server).
2. **A shared `.proto` contract** owned by neither service, generated into both.
3. **Client-side load balancing** — one client, two backend replicas, round-robin.
4. **gRPC interceptors** — client-side auth, server-side auth + logging.
5. **Realistic Compose topology** — internal-only gRPC backends, per-service DBs,
   service-name DNS resolution, named volumes for persistence.
6. **The day-to-day proto regeneration workflow** across two language setups.
7. **All three basic RPC shapes** — unary (`ReserveStock`), client-streaming
   (`ReserveStockBulk`, D-036), and server-streaming (`WatchLowStock`, D-037,
   re-streamed to REST as NDJSON at `GET /inventory/low-stock`).

Non-goals (for the first pass): TLS/mTLS, service mesh, a real LB proxy
(Envoy/nginx), Kubernetes, distributed tracing.

---

## 2. Directory structure

```
orderflow-grpc/
├── docker-compose.yml
├── architecture.md
├── README.md
├── proto/
│   └── order_inventory.proto          # shared contract, source of truth
│
├── order-service/                      # FastAPI — gRPC client
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                     # FastAPI app (REST entrypoint)
│       ├── models.py                   # SQLAlchemy models (Order, OrderItem)
│       ├── db.py                       # DB session/engine setup
│       ├── grpc_client/
│       │   ├── client.py               # grpc.aio channel + stub setup
│       │   └── interceptors.py         # client-side auth interceptor
│       └── generated/                  # protoc output: *_pb2.py, *_pb2_grpc.py
│
└── inventory-service/                  # Django — gRPC server
    ├── Dockerfile
    ├── requirements.txt
    ├── manage.py
    ├── inventory_project/
    │   └── settings.py
    ├── inventory_app/
    │   ├── models.py                   # Product, StockReservation
    │   └── migrations/
    ├── grpc_server/
    │   ├── server.py                   # grpc.aio server entrypoint
    │   ├── servicer.py                 # RPC implementations
    │   └── interceptors.py             # server-side logging + auth interceptor
    └── generated/                      # protoc output: *_pb2.py, *_pb2_grpc.py
```

### Deliberate choices worth flagging

- **`proto/` lives at the root**, outside both services. It is the contract both
  sides depend on, owned by neither. The `*_pb2.py` files are generated into each
  service **separately** (copy or mount). This mirrors how real orgs manage shared
  `.proto` files — often a separate versioned repo or published package.

- **Django's gRPC server is a separate process** from Django's web server, inside
  the same container (could later be split into its own container). Django's
  dev/WSGI server doesn't natively serve gRPC, so `grpc_server/` is its own async
  entrypoint.

---

## 3. Container / Compose topology

```
┌──────────────────────────────────────────────────────────────────┐
│                      docker-compose network                        │
│                                                                    │
│   ┌────────────────┐        gRPC (round-robin)   ┌───────────────┐ │
│   │  order-service  │ ──────────────────────────▶│ inventory-svc-1│ │
│   │   (FastAPI)     │                            │   :50051       │ │
│   │   REST :8000 ───┼──▶ published to host       └───────┬────────┘ │
│   │   gRPC client   │ ──────────────────────────▶┌───────────────┐ │
│   └────────┬────────┘                            │ inventory-svc-2│ │
│            │                                      │   :50051       │ │
│            │                                      └───────┬────────┘ │
│            ▼                                              ▼          │
│   ┌────────────────┐                            ┌────────────────┐  │
│   │   order-db      │                            │  inventory-db   │  │
│   │  (Postgres)     │                            │   (Postgres)    │  │
│   └────────────────┘                            └────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

Both `inventory-service-1` and `inventory-service-2` **build from the same image**.
They are stateless app replicas talking to a single shared `inventory-db` — a
realistic pattern (stateless app servers, shared backing store).

### Services

| service name        | image / build          | exposes        | depends_on        |
|---------------------|------------------------|----------------|-------------------|
| order-db            | postgres               | 5432 (internal)| —                 |
| inventory-db        | postgres               | 5432 (internal)| —                 |
| inventory-service-1 | build: ./inventory-service | 50051 (internal) | inventory-db  |
| inventory-service-2 | build: ./inventory-service | 50051 (internal) | inventory-db  |
| order-service       | build: ./order-service | 8000 (published) | order-db, inventory-service-1, inventory-service-2 |

> **Load balancing note:** both replicas listen on `50051` *inside their own
> containers*. The client targets them by service name (`inventory-service-1:50051`,
> `inventory-service-2:50051`). No host port collision because ports aren't
> published to the host.

---

## 4. Networking notes (the part that trips people up)

- Compose's default network lets containers resolve each other **by service name
  as hostname**. The gRPC client target is literally `inventory-service-1:50051` —
  no IP juggling.
- Each Postgres gets its **own named volume**, so data survives `docker compose
  down` (but not `down -v`).
- **Only `order-service:8000` is published** to the host. Everything else stays
  internal to the Compose network — gRPC backends are not exposed externally,
  which is the real-world default.

---

## 5. Load balancing (client-side)

First pass uses **static client-side load balancing** — no proxy:

- The gRPC client is configured with both backend addresses.
- Channel uses the `round_robin` load-balancing policy.
- Requests alternate across `inventory-service-1` and `inventory-service-2`.

This is verifiable by tailing both replicas' logs and watching requests land on
each in turn. A future pass could swap the static list for DNS-based resolution or
an L7 proxy (Envoy) without changing the servicer.

---

## 6. Interceptors

| side   | interceptor      | responsibility                                      |
|--------|------------------|-----------------------------------------------------|
| client | auth             | attach an auth token/metadata to every outgoing call|
| server | auth             | reject calls without a valid token                  |
| server | logging          | log method, peer, status, latency per request       |

Interceptors are the gRPC equivalent of middleware — the place for cross-cutting
concerns (auth, logging, metrics) so the servicer stays focused on business logic.

---

## 7. Data model (initial sketch)

**order-service (SQLAlchemy)**
- `Order` — id, status, created_at, total
- `OrderItem` — id, order_id (FK), product_id, quantity

**inventory-service (Django ORM)**
- `Product` — id, sku, name, available_quantity
- `StockReservation` — id, product_id (FK), order_ref, quantity, status

> The cross-service link (`OrderItem.product_id` ↔ `Product.id`) is **by reference
> only** — there is no shared database. Consistency is coordinated over gRPC, not
> via foreign keys. This is the core distributed-systems lesson of the project.

---

## 8. Request flow (happy path)

```
POST /orders  (host → FastAPI :8000)
        │
        ▼
FastAPI handler
        │  1. validate request
        │  2. open SQLAlchemy session
        ▼
gRPC client (round-robin) ──▶ inventory-service-{1|2}:50051
        │                          │  ReserveStock RPC
        │                          │  - check Product.available_quantity
        │                          │  - create StockReservation
        │                          │  - decrement stock
        │                          ◀── reservation result
        ▼
FastAPI handler
        │  3. persist Order + OrderItem(s)
        │  4. return 201 with order id
        ▼
host
```

If reservation fails (insufficient stock / auth error), FastAPI maps the gRPC
status to an appropriate HTTP error and does not persist the order.

---

## 9. Build / run workflow (day to day)

1. **Edit** `proto/order_inventory.proto`.
2. **Regenerate stubs** into both `order-service/app/generated/` and
   `inventory-service/generated/` (use a small helper script — same proto, two
   language setups).
3. `docker compose up --build`
4. Hit FastAPI from the host:
   ```bash
   curl -X POST localhost:8000/orders -d '{...}'
   ```
   → FastAPI calls Django over gRPC → Django talks to its own Postgres.
5. Watch the round-robin in action:
   ```bash
   docker compose logs -f order-service inventory-service-1 inventory-service-2
   ```

---

## 10. Future extensions (out of scope for v1)

- TLS / mTLS between client and server.
- Replace static LB list with Envoy or DNS-based resolution.
- Streaming RPCs (e.g. stock-level updates).
- OpenTelemetry tracing across the REST → gRPC hop.
- Split Django web + gRPC server into separate containers.
- Saga / outbox pattern for reservation rollback on order failure.
```
