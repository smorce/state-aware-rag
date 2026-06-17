# Rust/TypeScript DSL Parity

This suite proves that the Rust DSL and TypeScript DSL emit the same dynamic query JSON, then executes the runtime-safe fixtures against separate fresh local Helix instances.

Run from `ts-dsl/`:

```sh
npm run test:parity
```

The suite does three things:

- `parity:generate:rust` writes Rust-generated requests to `tests/parity/generated/rust`.
- `parity:generate:ts` writes TypeScript-generated requests to `tests/parity/generated/typescript`.
- `parity:compare-json` structurally compares every Rust and TypeScript request, including unsafe integer values.
- `parity:helix` runs all files in `generated/*/runtime` with `helix query dev --json ... --compact`, first on a Rust instance and then on a separate TypeScript instance, and compares the outputs.

Runtime instances are CLI-managed local projects under `tests/parity/workspaces` and use ports `18080` and `18081`. Runtime outputs are written under `tests/parity/results`.

The `json-only` fixture bucket covers DSL shapes that must serialize identically but are not safe or useful to execute directly as a runtime query. The `runtime` bucket is ordered and replayed sequentially against Helix.
