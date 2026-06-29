---
id: 001
title: Reserve stock RPC
service: inventory-service
status: done
created: 2026-06-29
updated: 2026-06-29
related: [architecture.md, "workflow.md#6-reserve_stock--the-atomic-core", "proto/order_inventory.proto", "inventory-service/inventory_app/services.py"]
---

## Context / Why

Inventory must reserve stock for an order **atomically and all-or-nothing**: if
any line item can't be satisfied, nothing is reserved. The hard problem is
concurrency — two orders hitting the backend at once must not both read the same
`available_quantity` and oversell. This is the core RPC the whole system fans out
to (see [workflow.md](../workflow.md)).

## Requirements

- The system SHALL expose a unary gRPC RPC `InventoryService.ReserveStock` taking
  `ReserveStockRequest { order_ref, items[] }` and returning
  `ReserveStockResponse { success, results[] }`.
- The system SHALL reserve all items or none — on any item failure, no rows are
  persisted (`transaction.set_rollback`).
- For each item the system SHALL reject non-positive quantity, unknown product,
  and insufficient stock, with a per-item `reason`.
- On success the system SHALL decrement `Product.available_quantity` and create a
  `StockReservation` (status `RESERVED`) carrying `order_ref` as the cross-service
  reference (no FK across services).
- The system SHALL serialize conflicting reservations via a row-level lock
  (`select_for_update`) inside a single `transaction.atomic()`.
- Insufficient stock SHALL be an application-level outcome (`success=False`), NOT
  a gRPC error — the RPC still returns status `OK`.

## Design

- **Service layer** ([services.py](../inventory-service/inventory_app/services.py)):
  `reserve_stock(order_ref, items)` — framework-agnostic, knows nothing about
  gRPC/protobuf. Returns a `ReservationOutcome { success, results[] }`. Plain
  dataclasses `ReserveItem` / `ItemOutcome` / `ReservationOutcome` mirror the
  proto messages 1:1. **This is the atomic core and it is implemented.**
- **Contract** ([order_inventory.proto](../proto/order_inventory.proto)):
  `package orderflow.inventory.v1`; messages `ReserveItem`, `ReserveStockRequest`,
  `ItemOutcome`, `ReserveStockResponse`. `product_id` is `int64` (Django
  BigAutoField); `quantity` is `int32` so non-positive values reach server-side
  validation.
- **gRPC concept:** transport status codes are reserved for protocol failures;
  domain rejections (insufficient stock) ride in the response payload. Keeps
  retry/circuit-breaker logic from misfiring on business rejections.

## Tasks

- [done] `reserve_stock` service layer with row-lock + all-or-nothing rollback
- [done] `Product` / `StockReservation` models
- [done] `order_inventory.proto` contract + regenerated stubs in both services
- [done] gRPC server bootstrap + servicer (adapter over `reserve_stock`)
- [todo] auth interceptor (`UNAUTHENTICATED` on missing/invalid token) — see [002](002-order-creation.md)
- [todo] logging interceptor (method, peer, status, latency)

## Changelog
- 2026-06-29 — created; backfilled from existing service layer, proto, and the gRPC server commits.
