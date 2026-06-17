# helix-dsl-macros

Procedural macros for the [`helix-dsl`](../README.md) query registration system.

This crate provides the `#[register]` attribute macro, which transforms query-building functions into self-registering queries with compile-time parameter validation and runtime metadata.

## Usage

```rust
use helix_db::prelude::*;

#[register]
fn find_user(username: String) -> ReadBatch {
    read_batch()
        .var_as("user", g().n_where(SourcePredicate::eq("username", username)))
        .returning(["user"])
}

#[register]
fn create_post(payload: ParamObject) -> WriteBatch {
    write_batch()
        .create_node("Post", payload)
}
```

The macro:

1. **Strips parameters** from the function signature and replaces them with `let` bindings to `Expr::param(...)`, making them available as typed query expressions inside the body.
2. **Generates a metadata function** (`__helix_dsl_params_{name}`) that returns a `Vec<QueryParameter>` describing each parameter's name and type.
3. **Registers the query** via [`inventory`](https://docs.rs/inventory) as a `RegisteredReadQuery` or `RegisteredWriteQuery`, making it discoverable at runtime by `helix_db::generate()`.

## Supported parameter types

| Rust type | Mapped to |
|---|---|
| `bool` | `Bool` |
| `i64` | `I64` |
| `f32` | `F32` |
| `f64` | `F64` |
| `String` | `String` |
| `Vec<u8>` | `Bytes` |
| `PropertyValue` / `ParamValue` | `Value` |
| `ParamObject` / `HashMap<String, T>` / `BTreeMap<String, T>` | `Object` |
| `Vec<T>` | `Array(T)` (recursive) |

Nested arrays are supported (e.g. `Vec<Vec<f64>>` maps to `Array(Array(F64))`).

## Constraints

The macro rejects functions that are:

- `async`
- Generic (type parameters)
- Methods (have a `self` receiver)
- Using destructuring patterns in parameters
- Returning anything other than `ReadBatch` or `WriteBatch`

## License

Apache-2.0
