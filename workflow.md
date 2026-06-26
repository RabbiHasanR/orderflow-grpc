# OrderFlow — Request Workflow

What happens, end to end, when a client sends an order request. This traces a
single `POST /orders` from the host all the way down to inventory's Postgres and
back. It complements [architecture.md](architecture.md) (topology) — this file is
about the **runtime flow of one request**.

> **Implementation status (2026-06-26).** This is the *target* flow.
> Today only the inventory **service layer** exists
> ([`reserve_stock`](inventory-service/inventory_app/services.py)) plus its
> models. The proto contract, the gRPC server/servicer, and the entire
> `order-service` are **not built yet**. Steps below are tagged
> **[done]** / **[todo]** so this doubles as a build checklist.

---

## The hop in one line

```
client → REST (FastAPI) → gRPC client (round-robin) → gRPC server (Django)
       → reserve_stock() → inventory Postgres → back up the same chain
```

The key distributed-systems idea: a single REST call **fans out** to a second
service over gRPC, and the two services own **separate databases**. There is no
shared transaction across the network — consistency is coordinated by the
protocol (the reservation result), not by a database foreign key.

---

## Step-by-step (happy path)

### 1. Client → `POST /orders`  **[todo]**
The client hits the only host-published port, `order-service:8000`. Body is the
order: a list of line items, each `{ product_id, quantity }`.

```bash
curl -X POST localhost:8000/orders \
  -H 'Content-Type: application/json' \
  -d '{"items": [{"product_id": 1, "quantity": 2}]}'
```

### 2. FastAPI validates the request  **[todo]**
- Pydantic validates the payload shape (items present, quantities are positive
  ints). Malformed body → `422` immediately, before any gRPC or DB work.
- This is cheap, local rejection — keep it ahead of the network hop.

### 3. order-service decides on its order id  **[todo]**
The order needs an id *before* the gRPC call, because that id is the
**cross-service reference** (`order_ref`) inventory will store on each
reservation. Two valid options:
- Insert the `Order` row as `PENDING` first, use its PK as `order_ref`, then
  confirm/cancel after reservation (closest to a real outbox/saga); or
- Generate the id up front (UUID) and only persist the order *after* a
  successful reservation (simpler — matches the architecture.md sketch).

The architecture doc's happy path persists the order **after** reservation
succeeds (step 7), so the id is generated up front.

### 4. gRPC client call → `ReserveStock`  **[todo]**
order-service acts as a **gRPC client**:
- The channel is configured with **both** backends
  (`inventory-service-1:50051`, `inventory-service-2:50051`) and the
  `round_robin` load-balancing policy, so successive requests alternate
  replicas (client-side load balancing — no proxy).
- A **client-side auth interceptor** attaches the auth token
  (`GRPC_AUTH_TOKEN`) as call metadata to every outgoing RPC.
- It sends a `ReserveStockRequest { order_ref, items[] }` and awaits the
  response (`grpc.aio`, so the FastAPI event loop is not blocked).

### 5. inventory-service receives the RPC  **[todo: server / done: logic]**
On the Django side, the request passes through server interceptors first, then
the servicer:
- **Auth interceptor** — rejects the call with `UNAUTHENTICATED` if the token
  metadata is missing/invalid. **[todo]**
- **Logging interceptor** — records method, peer, status, latency. **[todo]**
- **Servicer** — translates the protobuf `ReserveStockRequest` into plain
  `ReserveItem` dataclasses and calls the service layer. The servicer is a thin
  adapter; it holds no business logic. **[todo]**

### 6. `reserve_stock()` — the atomic core  **[done]**
See [`services.py`](inventory-service/inventory_app/services.py). This is the
heart of the flow and it already exists:

- Runs inside a single `transaction.atomic()`.
- For each line item it takes a **row-level lock**
  (`Product.objects.select_for_update()`) so two concurrent orders can't both
  read the same `available_quantity` and oversell. The second transaction
  **blocks** until the first commits or rolls back.
- Per item it checks: positive quantity → product exists → enough stock. If OK,
  it decrements `available_quantity` and creates a `StockReservation`
  (status `RESERVED`, carrying `order_ref`).
- **All-or-nothing:** if *any* item fails, the whole transaction is rolled back
  (`transaction.set_rollback(True)`) — no partial reservations — but a per-item
  breakdown is still returned so the caller knows exactly what failed and why.

Returns a `ReservationOutcome { success, results[] }`.

### 7. Response travels back up  **[todo]**
- Servicer maps `ReservationOutcome` → `ReserveStockResponse` protobuf and
  returns it. **[todo]**
- order-service receives the result over the channel. **[todo]**
- **On success:** persist `Order` + `OrderItem`(s) to order-db, return `201`
  with the order id. **[todo]**
- **On failure:** do **not** persist the order; map the failure to an HTTP error
  (see below). **[todo]**

---

## Failure mapping (what the client sees)

| Where it fails | gRPC / cause | HTTP returned to client |
|----------------|--------------|-------------------------|
| Bad request body | Pydantic validation | `422 Unprocessable Entity` |
| Insufficient stock / unknown product / bad qty | `reserve_stock` → `success=False` (app-level, RPC still `OK`) | `409 Conflict` (with per-item reasons) |
| Missing/invalid auth token | `UNAUTHENTICATED` | `500`/`502` (server-config issue, not client's fault) |
| Backend unreachable / both replicas down | `UNAVAILABLE` | `503 Service Unavailable` |
| RPC deadline exceeded | `DEADLINE_EXCEEDED` | `504 Gateway Timeout` |

Note the important distinction: **insufficient stock is not a gRPC error.** The
RPC completes with status `OK`; the *business* outcome (`success=False`) is
carried in the response payload. gRPC status codes are reserved for
transport/protocol failures, not domain rejections.

---

## Concurrency & consistency notes

- **No distributed transaction.** order-db and inventory-db are separate.
  Reservation commits in inventory **before** the order is persisted in
  order-service. If order persistence then fails, you have an orphaned
  reservation — that's the gap a future **saga / outbox + `RELEASED` reservation
  rollback** would close (the `StockReservation.RELEASED` status already exists
  for this). Out of scope for v1.
- **Round-robin load balancing** means request N and request N+1 may be served
  by different replicas — but both replicas share one inventory-db, so the
  row-lock in `reserve_stock` still serializes conflicting reservations
  correctly regardless of which replica handled the call.
- **Duplicate `product_id` in one order** is handled: stock is decremented as
  the loop proceeds, so a later line sees the reduced balance.

---

## Watching it run  **[todo: needs full stack]**

```bash
docker compose up --build
# in another shell — watch requests alternate across replicas:
docker compose logs -f order-service inventory-service-1 inventory-service-2
```

---

## Build order implied by this flow

1. Define `proto/order_inventory.proto` (`ReserveStock` RPC + messages).
2. Regenerate stubs into both services — `bash scripts/regen_proto.sh`.
3. inventory-service: servicer (adapter over the existing `reserve_stock`) +
   server entrypoint + auth/logging interceptors.
4. order-service: FastAPI app, models/db, gRPC client + auth interceptor,
   `POST /orders` handler with the failure mapping above.
5. Wire up `docker-compose.yml` and verify the round-robin.
