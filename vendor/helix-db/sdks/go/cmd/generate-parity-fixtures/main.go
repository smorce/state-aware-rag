package main

import (
	"fmt"
	"os"
	"path/filepath"

	helix "github.com/helixdb/helix-db/sdks/go"
)

type fixture struct {
	bucket  string
	name    string
	request helix.Request
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	out := "../tests/parity/generated/go"
	if len(os.Args) > 1 {
		out = os.Args[1]
	}

	if err := resetDir(filepath.Join(out, "runtime")); err != nil {
		return err
	}
	if err := resetDir(filepath.Join(out, "json-only")); err != nil {
		return err
	}

	fixtures := append(runtimeFixtures(), nodePermutationFixtures()...)
	fixtures = append(fixtures, jsonOnlyFixtures()...)

	seen := map[string]struct{}{}
	runtimeCount := 0
	jsonOnlyCount := 0
	for _, fixture := range fixtures {
		if fixture.bucket == "runtime" {
			runtimeCount++
		} else if fixture.bucket == "json-only" {
			jsonOnlyCount++
		} else {
			return fmt.Errorf("unknown fixture bucket %q", fixture.bucket)
		}

		rel := filepath.Join(fixture.bucket, fixture.name+".json")
		if _, ok := seen[rel]; ok {
			return fmt.Errorf("duplicate fixture path %s", rel)
		}
		seen[rel] = struct{}{}

		body, err := helix.MarshalRequest(fixture.request)
		if err != nil {
			return fmt.Errorf("marshal %s: %w", rel, err)
		}
		if err := os.WriteFile(filepath.Join(out, rel), body, 0o644); err != nil {
			return err
		}
	}

	if runtimeCount != 224 {
		return fmt.Errorf("generated %d runtime fixtures, expected 224", runtimeCount)
	}
	if jsonOnlyCount != 8 {
		return fmt.Errorf("generated %d json-only fixtures, expected 8", jsonOnlyCount)
	}

	return nil
}

func resetDir(path string) error {
	if err := os.RemoveAll(path); err != nil {
		return err
	}
	return os.MkdirAll(path, 0o755)
}

func runtime(name string, request helix.Request) fixture {
	return fixture{bucket: "runtime", name: name, request: request}
}

func jsonOnly(name string, request helix.Request) fixture {
	return fixture{bucket: "json-only", name: name, request: request}
}

func read() *helix.ReadQueryBuilder { return helix.ReadQuery("") }

func write() *helix.WriteQueryBuilder { return helix.WriteQuery("") }

func userProps(externalID, name string, age int64, score float64, status, city, bio string, embedding []float32) helix.Props {
	return helix.Props{
		helix.Prop("externalId", externalID),
		helix.Prop("name", name),
		helix.Prop("age", age),
		helix.Prop("score", helix.F64(score)),
		helix.Prop("status", status),
		helix.Prop("tenantId", "tenant-a"),
		helix.Prop("city", city),
		helix.Prop("bio", bio),
		helix.Prop("createdAt", helix.DateTimeFromMillis(1_776_000_000_000)),
		helix.Prop("embedding", helix.F32Array(embedding...)),
	}
}

func nestedMetadataProperty(externalID string, score int64) helix.PropertyValue {
	return helix.ObjectFromEntries(
		helix.Entry("externalID", externalID),
		helix.Entry("score", score),
		helix.Entry("tags", helix.Array(helix.String("alpha"), helix.I64(7))),
	)
}

func nestedMetadataParam(externalID string, score int64) map[string]any {
	return map[string]any{"externalID": externalID, "score": score, "tags": []any{"alpha", int64(7)}}
}

func exprPtr(expr helix.Expr) *helix.Expr { return &expr }

func inputPtr(input helix.PropertyInput) *helix.PropertyInput { return &input }

func runtimeFixtures() []fixture {
	return []fixture{
		runtime(
			"001-write-seed-core",
			write().
				VarAs("alice", helix.G().AddN("ParityUser", userProps("user-alice", "Alice", 31, 90.5, "active", "London", "Alice writes graph database tests", []float32{1.0, 0.0, 0.0}))).
				VarAs("bob", helix.G().AddN("ParityUser", userProps("user-bob", "Bob", 27, 72.25, "active", "Paris", "Bob likes traversal testing", []float32{0.9, 0.1, 0.0}))).
				VarAs("carol", helix.G().AddN("ParityUser", userProps("user-carol", "Carol", 42, 64.0, "inactive", "Berlin", "Carol archives old records", []float32{0.0, 1.0, 0.0}))).
				VarAs("alice_follows_bob", helix.G().N(helix.NodeVar("alice")).AddE("FOLLOWS", helix.NodeVar("bob"), helix.Props{
					helix.Prop("weight", helix.F64(1.0)),
					helix.Prop("since", "2024-01-01"),
					helix.Prop("note", "Alice follows Bob"),
					helix.Prop("embedding", helix.F32Array(1.0, 0.0)),
				})).
				VarAs("bob_follows_carol", helix.G().N(helix.NodeVar("bob")).AddE("FOLLOWS", helix.NodeVar("carol"), helix.Props{
					helix.Prop("weight", helix.F64(0.5)),
					helix.Prop("since", "2024-02-01"),
					helix.Prop("note", "Bob follows Carol"),
					helix.Prop("embedding", helix.F32Array(0.0, 1.0)),
				})).
				Returning("alice", "bob", "carol", "alice_follows_bob", "bob_follows_carol"),
		),
		runtime(
			"002-read-count-all-users",
			read().VarAs("user_count", helix.G().NWithLabel("ParityUser").Count()).Returning("user_count"),
		),
		runtime(
			"003-read-source-predicate-and-count",
			read().VarAs("active_adults", helix.G().NWithLabelWhere("ParityUser", helix.SourceAnd(helix.SourceEq("status", "active"), helix.SourceGte("age", int64(30)))).Count()).Returning("active_adults"),
		),
		runtime(
			"004-read-value-map-projection",
			read().VarAs("alice", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "user-alice")).Project(
				helix.ProjectPropAs("externalId", "id"),
				helix.ProjectPropAs("name", "name"),
				helix.ProjectExpr("score_plus_one", helix.ExprProp("score").Add(helix.ExprVal(helix.F64(1.0)))),
				helix.ProjectExpr("status_label", helix.ExprCase([]helix.CaseBranch{{When: helix.PredEq("status", "active"), Then: helix.ExprVal("enabled")}}, exprPtr(helix.ExprVal("disabled")))),
			)).Returning("alice"),
		),
		runtime(
			"005-read-order-range-values",
			read().VarAs("ordered", helix.G().NWithLabel("ParityUser").OrderByMultiple(
				helix.Ordering{Property: "status", Order: helix.OrderAsc},
				helix.Ordering{Property: "age", Order: helix.OrderDesc},
			).Range(0, 2).ValueMap("externalId", "age", "status")).Returning("ordered"),
		),
		runtime(
			"006-read-edge-count",
			read().VarAs("edge_count", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "user-alice")).OutE("FOLLOWS").Count()).Returning("edge_count"),
		),
		runtime(
			"007-read-edge-properties",
			read().VarAs("edges", helix.G().EWithLabel("FOLLOWS").EdgeHas("weight", helix.F64(1.0)).EdgeProperties()).Returning("edges"),
		),
		runtime(
			"008-read-edge-endpoints",
			read().
				VarAs("from_nodes", helix.G().EWithLabel("FOLLOWS").EdgeHasLabel("FOLLOWS").InN().ValueMap("externalId", "name")).
				VarAs("to_nodes", helix.G().EWithLabel("FOLLOWS").OutN().ValueMap("externalId", "name")).
				Returning("from_nodes", "to_nodes"),
		),
		runtime(
			"009-read-conditional-var-not-empty",
			read().
				VarAs("alice", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "user-alice"))).
				VarAsIf("friends", helix.VarNotEmpty("alice"), helix.G().N(helix.NodeVar("alice")).Out("FOLLOWS").ValueMap("externalId", "name")).
				Returning("alice", "friends"),
		),
		runtime(
			"010-read-conditional-var-empty",
			read().
				VarAs("missing", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "missing-user"))).
				VarAsIf("fallback", helix.VarEmpty("missing"), helix.G().NWithLabel("ParityUser").Limit(1).ValueMap("externalId")).
				Returning("missing", "fallback"),
		),
		runtime(
			"011-read-conditional-var-min-size-prev",
			read().
				VarAs("users", helix.G().NWithLabel("ParityUser").Limit(3)).
				VarAsIf("min_two", helix.VarMinSize("users", 2), helix.G().N(helix.NodeVar("users")).Count()).
				VarAsIf("prev_ok", helix.PrevNotEmpty(), helix.G().N(helix.NodeVar("users")).Exists()).
				Returning("min_two", "prev_ok"),
		),
		fixtureReadForeachParam(),
		fixtureWriteForeachParamCreate(),
		runtime(
			"014-read-after-foreach-param",
			read().VarAs("event_count", helix.G().NWithLabel("ParityEvent").Count()).Returning("event_count"),
		),
		runtime(
			"015-write-set-remove-properties",
			write().VarAs("updated", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "user-bob")).SetProperty("status", "inactive").SetProperty("updatedAt", helix.DateTimeFromMillis(1_777_000_000_000)).RemoveProperty("city").Count()).Returning("updated"),
		),
		runtime(
			"016-read-updated-properties",
			read().VarAs("bob", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "user-bob")).ValueMap("externalId", "status", "updatedAt", "city")).Returning("bob"),
		),
		runtime(
			"017-read-repeat-union",
			read().VarAs("walked", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "user-alice")).Repeat(helix.Repeat(helix.Sub().Out("FOLLOWS")).WithTimes(2).EmitAll().WithMaxDepth(4)).Union(helix.Sub().Out("FOLLOWS"), helix.Sub().In("FOLLOWS")).Dedup().ValueMap("externalId", "name")).Returning("walked"),
		),
		runtime(
			"018-read-choose-coalesce-optional",
			read().VarAs("branched", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", "user-alice")).Choose(helix.PredEq("status", "active"), helix.Sub().Out("FOLLOWS"), helix.Sub().In("FOLLOWS")).Coalesce(helix.Sub().Out("FOLLOWS"), helix.Sub().In("FOLLOWS")).Optional(helix.Sub().Out("FOLLOWS")).Dedup().ValueMap("externalId", "name")).Returning("branched"),
		),
		runtime(
			"019-read-aggregations",
			read().
				VarAs("by_status", helix.G().NWithLabel("ParityUser").GroupCount("status")).
				VarAs("mean_score", helix.G().NWithLabel("ParityUser").AggregateBy(helix.AggregateMean, "score")).
				VarAs("max_age", helix.G().NWithLabel("ParityUser").AggregateBy(helix.AggregateMax, "age")).
				Returning("by_status", "mean_score", "max_age"),
		),
		runtime(
			"020-write-index-create",
			write().
				VarAs("node_eq", helix.G().CreateIndexIfNotExists(helix.NodeEqualityIndex("ParityUser", "externalId"))).
				VarAs("node_range", helix.G().CreateIndexIfNotExists(helix.NodeRangeIndex("ParityUser", "age"))).
				VarAs("edge_eq", helix.G().CreateIndexIfNotExists(helix.EdgeEqualityIndex("FOLLOWS", "since"))).
				VarAs("edge_range", helix.G().CreateIndexIfNotExists(helix.EdgeRangeIndex("FOLLOWS", "weight"))).
				Returning("node_eq", "node_range", "edge_eq", "edge_range"),
		),
		fixtureReadParameterTypes(),
		runtime(
			"022-write-property-value-variants",
			write().VarAs("variant_node", helix.G().AddN("ParityVariant", helix.Props{
				helix.Prop("nullValue", helix.Null()),
				helix.Prop("boolValue", true),
				helix.Prop("i64Value", int64(9_223_372_036_854_775_000)),
				helix.Prop("dateTimeValue", helix.DateTimeFromMillis(-1)),
				helix.Prop("f64Value", helix.F64(3.25)),
				helix.Prop("f32Value", helix.F32(1.5)),
				helix.Prop("stringValue", "variant"),
				helix.Prop("bytesValue", helix.Bytes([]byte{1, 2, 3})),
				helix.Prop("i64Array", helix.I64Array(1, 2, 3)),
				helix.Prop("f64Array", helix.F64Array(1.0, 2.0)),
				helix.Prop("f32Array", helix.F32Array(1.0, 2.0)),
				helix.Prop("stringArray", helix.StringArray("a", "b")),
			})).Returning("variant_node"),
		),
		runtime(
			"023-read-property-value-variants",
			read().VarAs("variant", helix.G().NWithLabel("ParityVariant").ValueMapAll()).Returning("variant"),
		),
		runtime(
			"024-write-text-vector-indexes",
			write().
				VarAs("node_text", helix.G().CreateTextIndexNodes("ParityUser", "bio")).
				VarAs("node_vector", helix.G().CreateVectorIndexNodes("ParityUser", "embedding")).
				VarAs("edge_text", helix.G().CreateTextIndexEdges("FOLLOWS", "note")).
				VarAs("edge_vector", helix.G().CreateVectorIndexEdges("FOLLOWS", "embedding")).
				Returning("node_text", "node_vector", "edge_text", "edge_vector"),
		),
		runtime(
			"025-read-text-search-nodes",
			read().VarAs("text_hits", helix.G().TextSearchNodes("ParityUser", "bio", "graph", 5).ValueMap("externalId", "bio", "$distance")).Returning("text_hits"),
		),
		runtime(
			"026-read-vector-search-nodes",
			read().VarAs("vector_hits", helix.G().VectorSearchNodes("ParityUser", "embedding", []float32{1.0, 0.0, 0.0}, 3).Project(
				helix.ProjectPropAs("externalId", "externalId"),
				helix.ProjectPropAs("$distance", "distance"),
			)).Returning("vector_hits"),
		),
		runtime(
			"027-read-text-search-edges",
			read().VarAs("edge_text_hits", helix.G().TextSearchEdges("FOLLOWS", "note", "follows", 5).EdgeProperties()).Returning("edge_text_hits"),
		),
		runtime(
			"028-read-vector-search-edges",
			read().VarAs("edge_vector_hits", helix.G().VectorSearchEdges("FOLLOWS", "embedding", []float32{1.0, 0.0}, 5).EdgeProperties()).Returning("edge_vector_hits"),
		),
		runtime(
			"029-write-drop-temp-node",
			write().VarAs("temp", helix.G().AddN("ParityTemp", helix.Props{helix.Prop("name", "temp")})).VarAs("dropped", helix.G().N(helix.NodeVar("temp")).Drop().Count()).Returning("dropped"),
		),
		runtime(
			"030-read-final-counts",
			read().VarAs("users", helix.G().NWithLabel("ParityUser").Count()).VarAs("events", helix.G().NWithLabel("ParityEvent").Count()).VarAs("variants", helix.G().NWithLabel("ParityVariant").Count()).Returning("users", "events", "variants"),
		),
		fixtureReadSourcePredicateEqParam(),
		fixtureReadSourcePredicateBetweenParam(),
	}
}

func fixtureReadForeachParam() fixture {
	q := read()
	q.ParamArray("lookups", []any{map[string]any{"externalId": "user-alice"}, map[string]any{"externalId": "user-carol"}}, helix.ParamTypeObject())
	return runtime(
		"012-read-foreach-param",
		q.ForEachParam("lookups", helix.Read().VarAs("matched", helix.G().NWithLabel("ParityUser").Where(helix.PredEq("externalId", helix.ExprParam("externalId"))).ValueMap("externalId", "name"))).Returning("matched"),
	)
}

func fixtureWriteForeachParamCreate() fixture {
	q := write()
	q.ParamArray("rows", []any{
		map[string]any{"eventId": "event-1", "kind": "click", "score": int64(10)},
		map[string]any{"eventId": "event-2", "kind": "view", "score": int64(5)},
	}, helix.ParamTypeObject())
	return runtime(
		"013-write-foreach-param-create",
		q.ForEachParam("rows", helix.Write().VarAs("created", helix.G().AddN("ParityEvent", helix.Props{
			helix.Prop("eventId", helix.ExprParam("eventId")),
			helix.Prop("kind", helix.ExprParam("kind")),
			helix.Prop("score", helix.ExprParam("score")),
		}))).Returning("created"),
	)
}

func fixtureReadParameterTypes() fixture {
	q := read()
	statuses := q.ParamArray("statuses", []string{"active", "inactive"}, helix.ParamTypeString())
	createdAfter := q.ParamDateTime("created_after", "2026-01-01T00:00:00.000Z")
	limit := q.ParamI64("limit", int64(5))
	return runtime(
		"021-read-parameter-types",
		q.VarAs("matches", helix.G().NWithLabel("ParityUser").Where(helix.PredIsIn("status", statuses)).Where(helix.PredGte("createdAt", createdAfter)).Limit(limit).ValueMap("externalId", "status")).Returning("matches"),
	)
}

func fixtureReadSourcePredicateEqParam() fixture {
	q := read()
	name := q.ParamString("name", "Alice")
	return runtime(
		"031-read-source-predicate-eq-param",
		q.VarAs("user", helix.G().NWhere(helix.SourceAnd(helix.SourceEq("$label", "ParityUser"), helix.SourceEq("name", name))).ValueMap("externalId", "name")).Returning("user"),
	)
}

func fixtureReadSourcePredicateBetweenParam() fixture {
	q := read()
	minAge := q.ParamI64("min_age", int64(30))
	return runtime(
		"032-read-source-predicate-between-param",
		q.VarAs("adults", helix.G().NWhere(helix.SourceAnd(helix.SourceEq("$label", "ParityUser"), helix.SourceBetween("age", minAge, int64(65)))).ValueMap("externalId", "age")).Returning("adults"),
	)
}

func nodePermutationFixtures() []fixture {
	sources := []string{"label", "where", "all"}
	filters := []string{"none", "has", "logic", "expr"}
	bounds := []string{"none", "limit", "skip", "range"}
	terminals := []string{"count", "exists", "value_map", "project"}

	fixtures := make([]fixture, 0, len(sources)*len(filters)*len(bounds)*len(terminals))
	index := 100
	for _, source := range sources {
		for _, filter := range filters {
			for _, bound := range bounds {
				for _, terminal := range terminals {
					name := fmt.Sprintf("%03d-combo-node-%s-%s-%s-%s", index, source, filter, bound, terminal)
					fixtures = append(fixtures, runtime(name, nodeComboBatch(source, filter, bound, terminal)))
					index++
				}
			}
		}
	}
	return fixtures
}

func nodeComboBatch(source, filter, bound, terminal string) helix.Request {
	traversal := applyNodeBound(applyNodeFilter(nodeSource(source), filter), bound).OrderBy("externalId", helix.OrderAsc)
	switch terminal {
	case "count":
		traversal = traversal.Count()
	case "exists":
		traversal = traversal.Exists()
	case "value_map":
		traversal = traversal.ValueMap("externalId", "name", "age", "status")
	case "project":
		traversal = traversal.Project(
			helix.ProjectPropAs("externalId", "externalId"),
			helix.ProjectPropAs("status", "status"),
			helix.ProjectExpr("age_plus_two", helix.ExprProp("age").Add(helix.ExprVal(int64(2)))),
		)
	default:
		panic("unknown terminal " + terminal)
	}
	return read().VarAs("result", traversal).Returning("result")
}

func nodeSource(source string) *helix.Traversal {
	switch source {
	case "label":
		return helix.G().NWithLabel("ParityUser")
	case "where":
		return helix.G().NWhere(helix.SourceEq("$label", "ParityUser"))
	case "all":
		return helix.G().N(helix.AllNodes()).HasLabel("ParityUser")
	default:
		panic("unknown source " + source)
	}
}

func applyNodeFilter(traversal *helix.Traversal, filter string) *helix.Traversal {
	switch filter {
	case "none":
		return traversal
	case "has":
		return traversal.Has("status", "active")
	case "logic":
		return traversal.Where(helix.PredAnd(
			helix.PredHasKey("externalId"),
			helix.PredOr(helix.PredStartsWith("name", "A"), helix.PredEndsWith("name", "b")),
			helix.PredNot(helix.PredIsNull("age")),
		))
	case "expr":
		return traversal.Where(helix.PredCompare(helix.ExprProp("score").Add(helix.ExprVal(helix.F64(1.0))), helix.CompareGt, helix.ExprVal(helix.F64(65.0))))
	default:
		panic("unknown filter " + filter)
	}
}

func applyNodeBound(traversal *helix.Traversal, bound string) *helix.Traversal {
	switch bound {
	case "none":
		return traversal
	case "limit":
		return traversal.Limit(2)
	case "skip":
		return traversal.Skip(1)
	case "range":
		return traversal.Range(0, 2)
	default:
		panic("unknown bound " + bound)
	}
}

func jsonOnlyFixtures() []fixture {
	return []fixture{
		fixtureRawReadSteps(),
		fixtureRawWriteSteps(),
		fixtureDynamicValueShapes(),
		fixtureEmptySourceVectorTextRuntimeInputs(),
		jsonOnly(
			"904-empty-query-and-node-edge-ref-shapes",
			read().
				VarAs("all_nodes", helix.G().N(helix.AllNodes()).Count()).
				VarAs("node_ids", helix.G().N(helix.NodeIDs(1, 2)).ID()).
				VarAs("node_var", helix.G().N(helix.NodeVar("all_nodes")).Label()).
				VarAs("edge_ids", helix.G().E(helix.EdgeIDs(1, 2)).ID()).
				VarAs("edge_var", helix.G().E(helix.EdgeVar("edge_ids")).Label()).
				Returning("all_nodes", "node_ids", "node_var", "edge_ids", "edge_var"),
		),
		jsonOnly(
			"905-empty-traversal-source-mutators",
			write().
				VarAs("inject", helix.G().Inject("some_var").Count()).
				VarAs("drop_edge_by_id", helix.G().DropEdgeByID(helix.EdgeID(123_456)).Count()).
				Returning("inject", "drop_edge_by_id"),
		),
		fixtureNestedDynamicPropertyWriteShapes(),
		fixtureNestedDynamicPropertyReadShapes(),
	}
}

func fixtureRawReadSteps() fixture {
	q := read()
	q.ParamArray("node_ids", []int64{1, 2}, helix.ParamTypeI64())
	q.ParamArray("edge_ids", []int64{1}, helix.ParamTypeI64())
	q.ParamString("needle", "graph")
	q.ParamI64("limit", int64(10))
	q.ParamI64("skip", int64(0))
	q.ParamI64("end", int64(10))
	return jsonOnly(
		"900-exhaustive-raw-read-steps",
		q.
			VarAs("raw_nodes", helix.G().N(helix.NodeParam("node_ids")).Has("name", "Alice").Where(helix.PredContainsExpr("bio", helix.ExprParam("needle"))).Limit(helix.ExprParam("limit")).Skip(helix.ExprParam("skip")).Range(helix.BoundLiteral(0), helix.BoundExpr(helix.ExprParam("end"))).As("a").Store("stored").Select("stored").Dedup().Within("stored").Without("missing").Fold().Unfold().Path().SimplePath().WithSack(int64(0)).SackSet("score").SackAdd("score").SackGet().Project(
				helix.ProjectPropAs("externalId", "externalId"),
				helix.ProjectExpr("neg_age", helix.ExprProp("age").Neg()),
			)).
			VarAs("raw_edges", helix.G().E(helix.EdgeParam("edge_ids")).EWhere(helix.SourceOr(helix.SourceHasKey("since"), helix.SourceStartsWith("note", "Alice"))).OutN().InN().OtherN().EdgeHas("weight", helix.F64(1.0)).EdgeHasLabel("FOLLOWS").OrderBy("weight", helix.OrderDesc).EdgeProperties()).
			Returning("raw_nodes", "raw_edges"),
	)
}

func fixtureRawWriteSteps() fixture {
	rawIndexSteps := helix.G().CreateIndexIfNotExists(helix.NodeUniqueEqualityIndex("ParityUser", "externalId")).DropIndex(helix.NodeRangeIndex("ParityUser", "age")).Steps()
	rawIndexSteps = append(rawIndexSteps,
		helix.CreateVectorIndexNodesStep("ParityUser", "embedding", "tenantId"),
		helix.CreateVectorIndexEdgesStep("FOLLOWS", "embedding", "tenantId"),
		helix.CreateTextIndexNodesStep("ParityUser", "bio", "tenantId"),
		helix.CreateTextIndexEdgesStep("FOLLOWS", "note", "tenantId"),
	)

	return jsonOnly(
		"901-exhaustive-raw-write-steps",
		write().
			VarAs("raw_indexes", helix.TraversalFromSteps(rawIndexSteps)).
			VarAs("raw_mutations", helix.G().AddN("RawNode", helix.Props{helix.Prop("name", "raw")}).AddE("RAW_EDGE", helix.NodeVar("raw_mutations"), helix.Props{helix.Prop("weight", int64(1))}).SetProperty("name", helix.ExprParam("name")).RemoveProperty("old").DropEdge(helix.NodeID(999_999)).DropEdgeLabeled(helix.NodeID(999_999), "RAW_EDGE").DropEdgeByID(helix.EdgeID(999_999)).Drop()).
			Returning("raw_indexes", "raw_mutations"),
	)
}

func fixtureDynamicValueShapes() fixture {
	q := read()
	q.ParamValue("null", nil)
	q.ParamBool("bool", true)
	q.ParamI64("i64", int64(9_223_372_036_854_775_807))
	q.ParamF64("f64", 1.25)
	q.ParamF32("f32", float32(1.5))
	q.ParamString("string", "value")
	q.ParamArray("array", []any{int64(1), "two"}, helix.ParamTypeValue())
	q.ParamObject("object", map[string]any{"nested": true})
	return jsonOnly(
		"902-dynamic-value-and-param-type-shapes",
		q.VarAs("empty", helix.G().NWithLabel("Missing").Count()).Returning("empty"),
	)
}

func fixtureEmptySourceVectorTextRuntimeInputs() fixture {
	q := read()
	queryVector := q.ParamArray("query_vector", []float64{1.0, 0.0, 0.0}, helix.ParamTypeF64())
	queryText := q.ParamString("query_text", "graph")
	limit := q.ParamI64("limit", int64(5))
	tenant := q.ParamString("tenant", "tenant-a")
	return jsonOnly(
		"903-empty-source-vector-text-runtime-inputs",
		q.
			VarAs("vector_nodes", helix.G().VectorSearchNodesWith("ParityUser", "embedding", queryVector.Input(), limit.Bound(), inputPtr(tenant.Input()))).
			VarAs("text_nodes", helix.G().TextSearchNodesWith("ParityUser", "bio", queryText.Input(), limit.Bound(), inputPtr(tenant.Input()))).
			Returning("vector_nodes", "text_nodes"),
	)
}

func fixtureNestedDynamicPropertyWriteShapes() fixture {
	q := write()
	metadata := q.ParamObject("metadata", nestedMetadataParam("param_id", 22))
	return jsonOnly(
		"906-nested-dynamic-property-write-shapes",
		q.
			VarAs("created", helix.G().AddN("ParityNested", helix.Props{
				helix.Prop("name", "nested"),
				helix.Prop("metadata", nestedMetadataProperty("some_id", 20)),
			})).
			VarAs("updated", helix.G().N(helix.NodeVar("created")).SetProperty("metadata", metadata).ValueMap("metadata.externalID")).
			VarAs("target", helix.G().AddN("ParityNestedTarget", helix.Props{helix.Prop("name", "target")})).
			VarAs("edge", helix.G().N(helix.NodeVar("created")).AddE("NESTED_LINK", helix.NodeVar("target"), helix.Props{helix.Prop("metadata", nestedMetadataProperty("edge_id", 5))}).Count()).
			Returning("created", "updated", "edge"),
	)
}

func fixtureNestedDynamicPropertyReadShapes() fixture {
	q := read()
	externalID := q.ParamString("external_id", "param_id")
	return jsonOnly(
		"907-nested-dynamic-property-read-shapes",
		q.
			VarAs("nested_users", helix.G().NWhere(helix.SourceAnd(helix.SourceEq("$label", "ParityNested"), helix.SourceEq("metadata.externalID", externalID))).Where(helix.PredCompare(helix.ExprProp("metadata.score"), helix.CompareGt, helix.ExprVal(int64(10)))).OrderByMultiple(
				helix.Ordering{Property: "metadata.score", Order: helix.OrderDesc},
				helix.Ordering{Property: "name", Order: helix.OrderAsc},
			).Project(
				helix.ProjectPropAs("metadata.externalID", "external_id"),
				helix.ProjectExpr("score_copy", helix.ExprProp("metadata.score")),
			)).
			VarAs("nested_values", helix.G().NWithLabel("ParityNested").Values("metadata.externalID")).
			VarAs("nested_map", helix.G().NWithLabel("ParityNested").ValueMap("metadata.externalID", "metadata.score")).
			VarAs("nested_edges", helix.G().EWhere(helix.SourceAnd(helix.SourceEq("$label", "NESTED_LINK"), helix.SourceEq("metadata.externalID", "edge_id"))).EdgeHas("metadata.externalID", "edge_id").EdgeProperties()).
			Returning("nested_users", "nested_values", "nested_map", "nested_edges"),
	)
}
