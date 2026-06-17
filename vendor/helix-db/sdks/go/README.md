# HelixDB Go SDK

Dynamic-first Go SDK for building and executing HelixDB queries.

## Install

```sh
go get github.com/helixdb/helix-db/sdks/go
```

```go
import helix "github.com/helixdb/helix-db/sdks/go"
```

## Query Functions

Write normal Go functions that return `helix.Request`. Set the query name with `ReadQuery` or `WriteQuery`, declare runtime parameters inline, then pass the request to `Client.Exec`.

```go
type UserRow struct {
	ID       int64  `json:"$id"`
	Name     string `json:"name"`
	TenantID string `json:"tenantId"`
}

type FindUsersResponse struct {
	Users []UserRow `json:"users"`
}

func FindUsers(tenantID string, limit int64) helix.Request {
	q := helix.ReadQuery("find_users")

	tenant := q.ParamString("tenant_id", tenantID)
	maxRows := q.ParamI64("limit", limit)

	return q.
		VarAs("users",
			helix.G().
				NWithLabel("User").
				Where(helix.PredEq("tenantId", tenant)).
				Limit(maxRows).
				ValueMap("$id", "name", "tenantId"),
		).
		Returning("users")
}
```

## Execute

```go
client, err := helix.NewClient("http://localhost:6969")
if err != nil {
	return err
}

var out FindUsersResponse
err = client.Exec(ctx, FindUsers("acme", 25), &out)
```

## Writes

```go
type CreateUserResponse struct {
	User []UserRow `json:"user"`
}

func CreateUser(name string, tenantID string) helix.Request {
	q := helix.WriteQuery("create_user")

	nameParam := q.ParamString("name", name)
	tenant := q.ParamString("tenant_id", tenantID)

	return q.
		VarAs("user",
			helix.G().AddN("User", helix.Props{
				helix.Prop("name", nameParam),
				helix.Prop("tenantId", tenant),
			}),
		).
		Returning("user")
}

err = client.Exec(ctx, CreateUser("Alice", "acme"), &created,
	helix.WriterOnly(),
	helix.AwaitDurability(true),
)
```

## Parameters

Parameter helpers insert both runtime values and `parameter_types` metadata:

```go
q := helix.ReadQuery("recent_users")
tenant := q.ParamString("tenant_id", "acme")
createdAfter := q.ParamDateTime("created_after", "2026-01-01T00:00:00.000Z")
limit := q.ParamI64("limit", int64(10))
```

Parameter refs can be used in predicates, property inputs, and bounds.

Direct Go values are serialized as literals in the inline AST. For example,
`helix.SourceEq("id", "foo")` inlines the string `"foo"`; it does not create a
runtime parameter. For request-specific values, declare a `q.Param*` value and
pass the returned ref so stable query shapes can reuse server caches:

```go
id := q.ParamString("id", userID)
helix.G().NWhere(helix.SourceEq("id", id))
```

Always pass explicit names to `Returning(...)` for values you want back. A
zero-arg `Returning()` is supported for intentional empty responses and
serializes as `"returns":[]`.

## Conflicts And Retries

`Client.Exec` does not retry HTTP 409 conflicts automatically. Callers should
retry only when the operation is safe to replay. Remote errors are returned as
`*helix.HelixError` with `StatusCode` populated, and `helix.IsConflict(err)`
or `errors.Is(err, helix.ErrConflict)` checks for HTTP 409:

```go
func ExecWithConflictRetry(ctx context.Context, client *helix.Client, build func() helix.Request, out any) error {
	for attempt := 0; attempt < 3; attempt++ {
		err := client.Exec(ctx, build(), out)
		if err == nil || !helix.IsConflict(err) || attempt == 2 {
			return err
		}
		time.Sleep(time.Duration(attempt+1) * 50 * time.Millisecond)
	}
	return nil
}
```

## Notes

- Go v1 is dynamic-first and posts to `/v1/query` through `client.Exec`.
- Stored-query registration and bundle generation are not part of the primary Go workflow.
- Use `MarshalRequest(req)` only for tests, parity fixtures, or debugging.
- `int64` values serialize as JSON numbers; response decoding uses `json.Decoder.UseNumber()`.
- Dynamic datetime parameters serialize as RFC3339 UTC strings with millisecond precision.
- Dynamic JSON cannot represent bytes parameters; bytes remain valid stored property values.
- Non-200 responses return `*HelixError` with `Kind: ErrorRemote`, `Details`, and `StatusCode`.
