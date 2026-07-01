# Decisions Log

A running record of *why* key choices were made in OrderFlow gRPC, for future
reference. Newest decisions at the bottom of each section. This is a learning
project, so the reasoning matters more than the outcome.

> Where a decision diverges from `architecture.md`, that doc is the original
> sketch and this log is the current source of truth until the doc is updated.

---

## Process / sequencing

### D-001 — Build inventory-service before order-service
**Date:** 2026-06-24
**Decision:** Implement the Django gRPC **server** (inventory) before the FastAPI
gRPC **client** (order).
**Why:** Both depend on the shared proto contract, but a client is hard to
exercise without a server to call. Standing up the server first gives the client
something real to talk to and avoids building a throwaway stub server.

### D-002 — Proto contract is the unavoidable first step ("Phase 0")
**Date:** 2026-06-24
**Decision:** No matter which service we build first, `proto/order_inventory.proto`
must be defined and regenerated before service code that uses the stubs.
**Why:** The `*_pb2*` stubs are generated *from* the proto. The contract is the
thing both services bind to; it is owned by neither.

---

## Contract

### D-010 — Batch `ReserveStock` RPC (not per-item)
**Date:** 2026-06-24
**Decision:** One `ReserveStock` call carries all items of an order; the response
returns per-item results.
**Why:** Matches a real order (one logical unit), fewer network round-trips, and a
single place to map success/failure. Per-item RPCs would push partial-failure
coordination up into order-service.
**Alternatives:** Per-item RPC — rejected as chattier and harder to keep atomic.

---

## inventory-service (Django gRPC server)

### D-020 — Django used as ORM + migrations only
**Date:** 2026-06-24
**Decision:** No web server, no views, no DRF, no templates, no contrib apps. The
runtime is a standalone gRPC server that calls `django.setup()`. `INSTALLED_APPS`
contains only `inventory_app`.
**Why:** This service speaks gRPC, not HTTP. Pulling in auth/admin/sessions would
create tables and machinery we never use. Keeping it lean makes the "Django as a
library" idea explicit.

### D-021 — Sync gRPC server + plain Django ORM (not grpc.aio)
**Date:** 2026-06-24
**Decision:** Run a synchronous `grpcio` server backed by a `ThreadPoolExecutor`;
servicer methods are plain `def`; ORM, `transaction.atomic()` and
`select_for_update()` are used directly.
**Why:** Django's ORM is natively synchronous. An async (`grpc.aio`) server would
force `sync_to_async` wrappers that push DB work onto a threadpool *anyway* — so
async would add ceremony without removing threads. Client- and server-side async
are independent, so a sync server still serves the async order-service client
fine. Matching the runtime to the tools (sync ORM → sync server) is the lesson.
**Diverges from:** `architecture.md`, which sketches a `grpc.aio` server.
**Alternatives:** grpc.aio + sync_to_async (more ceremony, no benefit here);
django-grpc-framework (hides the mechanics we want to learn).

### D-022 — All-or-nothing batch reservation
**Date:** 2026-06-24
**Decision:** A reservation runs inside one `transaction.atomic()`. If any item
lacks stock, roll the whole batch back and return per-item results showing which
failed; nothing is reserved.
**Why:** Cleaner consistency story — an order reserves fully or not at all.
Partial commits would leave order-service to manage partial state and rollback.
**Alternatives:** Partial commit (shopping-cart feel) — rejected for v1.

### D-023 — Service-layer pattern, single app (not multi-app, not repository/DDD)
**Date:** 2026-06-24
**Decision:** One Django app (`inventory_app`). Business logic lives in
`services.py` (use-case/orchestration), the gRPC `servicer.py` is a thin transport
adapter (proto ↔ Python, error → status), and the ORM is the data-access layer.
Reads may later move to `selectors.py` if the service grows.
**Why:** A microservice is one bounded context, so feature-splitting into many
apps is monolith ceremony. The reservation use case spans two models in one
locked transaction — too much for a single model method, and it must not live in
the transport. A service layer keeps it framework-agnostic and unit-testable
without gRPC. Wrapping the ORM in repositories is needless indirection at this
scale (the ORM already *is* the repository).
**Alternatives:** App-based/feature split (monolith concern); repository/DDD over
the ORM (over-engineered).

### D-024 — `StockReservation.product` uses `on_delete=PROTECT`
**Date:** 2026-06-24
**Decision:** Foreign key from reservation to product protects against deletion.
**Why:** A product with live reservations should not be deletable — guards
inventory integrity.

### D-025 — Hand-written migrations
**Date:** 2026-06-24
**Decision:** `0001_initial` and `0002_seed_products` were hand-written to match
the models.
**Why:** Django is not installed locally (the service runs in Docker), so
`makemigrations` could not be run on the host. They are correct but unverified by
Django until first run — `python manage.py makemigrations --check --dry-run` in
the container will confirm zero drift. Prefer regenerating via `makemigrations`
once the container exists.

### D-026 — Single inventory replica for now (defer round-robin)
**Date:** 2026-06-24
**Decision:** Run one `inventory-service` replica initially; add a second (same
image) later to demonstrate client-side round-robin load balancing.
**Why:** Keeps the first pass simple. With one replica there is no concurrent
`migrate` race, so migrations can run in the container entrypoint and the
dedicated one-shot migrate service is not needed yet.
**Diverges from:** `architecture.md` (2 replicas + round-robin, project goal #3).
**Follow-up:** Re-introduce the 2nd replica + one-shot migrate service when
building the load-balancing demo.

### D-027 — Production-grade image: multi-stage, runtime-only deps, COPYed stubs
**Date:** 2026-06-30
**Decision:** Containerize inventory-service with a multi-stage Dockerfile that
installs runtime deps into a venv in a builder stage and copies only that venv +
app code into a slim non-root runtime. Split `requirements.txt` (runtime) from
`requirements-dev.txt` (adds `grpcio-tools`); the image installs runtime-only.
The `generated/` stubs are produced out-of-band by `regen_proto.sh` and COPYed
in, not generated during the build. Healthcheck is a Python TCP connect to the
gRPC port. Entrypoint runs `migrate` then `exec`s the server. See
[spec 003](specs/003-inventory-containerization.md).
**Why:** A runtime image should ship the minimum to *run*, not to *build* —
no protobuf compiler (`grpcio-tools`), no pip cache, no toolchain, no root.
`psycopg[binary]`/`grpcio` ship manylinux wheels, so no compiler is needed and
the image stays small without `libpq-dev`. The build context is
`./inventory-service`; `proto/` sits at the repo root *outside* it, and the
documented workflow already generates stubs per-service — so COPYing them is the
natural fit and avoids widening the context just to re-run protoc. `exec` makes
the server PID 1's successor so the existing SIGTERM graceful-drain works. The
server registers no gRPC health service, so a TCP probe is the honest
no-extra-binary liveness check.
**Alternatives:** Hermetic in-image stub generation (context = repo root +
grpcio-tools in builder) — rejected: heavier, contradicts per-service regen flow.
Single-stage slim image — works (wheels need no build deps) but still ships pip
cache and is less clean than copying a prebuilt venv.
**Scope:** inventory-service only; one replica (consistent with D-026). order-
service image + 2nd replica + one-shot migrate job are later phases.

---

## order-service (FastAPI gRPC client) — planned, not yet built

### D-030 — Async SQLAlchemy + asyncpg (not sync + psycopg)
**Date:** 2026-06-24
**Decision:** Use `AsyncSession` + asyncpg for order-db.
**Why:** order-service is the public edge and its whole value is a non-blocking
REST → gRPC hop. asyncpg is natively async, so the DB leg yields like the gRPC
leg (`grpc.aio`) — uniform and no threadpool. A sync driver would smuggle a
blocking call into an otherwise async handler. (Contrast D-021: inventory's ORM
is natively sync, so sync wins there — match the runtime to the tools.)

### D-031 — Endpoints: `POST /orders`, `GET /orders/{id}`, `GET /healthz`
**Date:** 2026-06-24
**Decision:** `POST /orders` is the core flow (validate → ReserveStock over gRPC →
persist → 201). `GET /orders/{id}` for read-back; `GET /healthz` for liveness.
**Why:** The read endpoint makes the service demonstrable without psql; health is
for compose `depends_on`. The read endpoint is optional for v1.

### D-032 — Defer client verification harness
**Date:** 2026-06-24
**Decision:** No throwaway stub server and no mock-based unit tests for the gRPC
client yet; verify end-to-end once inventory-service exists (consequence of D-001).

### D-033 — order-db schema via SQLAlchemy `create_all` (Alembic deferred)
**Date:** 2026-07-01
**Decision:** order-service creates its tables at startup with
`Base.metadata.create_all` (run in the FastAPI lifespan). No Alembic for v1.
**Why:** The schema is two tables (`orders`, `order_items`) and the service is a
learning demo — a migration tool would be ceremony before there is any schema to
migrate. `create_all` is idempotent and needs no extra process/entrypoint step.
Alembic is the documented future path (parallel to inventory's Django migrations)
for when the schema evolves and needs versioned, reversible changes.
**Contrast:** inventory-service uses real Django migrations (D-025) because Django
gives them for free and it already seeds data (`0002_seed_products`).
**Alternatives:** Alembic now (premature); a migrate step in the entrypoint like
inventory's (nothing to run without a migration tool).

### D-034 — Shared gRPC auth metadata key, enforced server-side
**Date:** 2026-07-01
**Decision:** The auth token travels as call metadata under the key
`x-auth-token`. The order-service client attaches it via a client-side
interceptor; the inventory server verifies it via a server-side auth interceptor
and aborts `UNAUTHENTICATED` on mismatch. If `GRPC_AUTH_TOKEN` is unset on the
server, enforcement is skipped (dev fallback, matching the settings convention).
**Why:** Interceptors keep auth out of the servicer/handlers (architecture §6).
Metadata is gRPC's per-call header channel — the natural place for a bearer-style
token, checked once at the edge of the server. A single shared constant on each
side prevents the client attaching a header the server never reads.
**Why not fail-closed when the server token is unset:** the whole stack must run
locally without configuring a secret; once `GRPC_AUTH_TOKEN` is set (compose/prod)
it is enforced. `UNAUTHENTICATED` maps to HTTP 502 at the client (a server-config
fault, not the caller's) per workflow.md.
**Alternatives:** channel-level call credentials (heavier, TLS-oriented — out of
scope, no TLS in v1); no server-side check (token attached but meaningless).
