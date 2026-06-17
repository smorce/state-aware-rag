import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
  AggregateFunction,
  BatchCondition,
  CompareOp,
  DateTime,
  DynamicQueryRequest,
  DynamicQueryValue,
  EdgeRef,
  Expr,
  IndexSpec,
  NodeRef,
  Order,
  Predicate,
  Projection,
  PropertyInput,
  PropertyValue,
  QueryParamType,
  RepeatConfig,
  SourcePredicate,
  Step,
  StreamBound,
  Traversal,
  g,
  readBatch,
  stringifyJson,
  sub,
  writeBatch,
} from "../../src/index.js";
import { typescriptGeneratedRoot } from "./paths.js";

type Fixture = {
  bucket: "runtime" | "json-only";
  name: string;
  request: DynamicQueryRequest;
};

await resetDir(join(typescriptGeneratedRoot, "runtime"));
await resetDir(join(typescriptGeneratedRoot, "json-only"));

const fixtures = [...runtimeFixtures(), ...nodePermutationFixtures(), ...jsonOnlyFixtures()];
for (const fixture of fixtures) {
  await writeFile(join(typescriptGeneratedRoot, fixture.bucket, `${fixture.name}.json`), fixture.request.toJsonString());
}

async function resetDir(path: string) {
  await rm(path, { recursive: true, force: true });
  await mkdir(path, { recursive: true });
}

function runtime(name: string, request: DynamicQueryRequest): Fixture {
  return { bucket: "runtime", name, request };
}

function jsonOnly(name: string, request: DynamicQueryRequest): Fixture {
  return { bucket: "json-only", name, request };
}

function withParams(request: DynamicQueryRequest, values: [string, unknown][], types: [string, QueryParamType][]): DynamicQueryRequest {
  for (const [name, value] of values) request.insertParameterValue(name, value);
  for (const [name, ty] of types) request.insertParameterType(name, ty);
  return request;
}

function userProps(
  externalId: string,
  name: string,
  age: number,
  score: number,
  status: string,
  city: string,
  bio: string,
  embedding: number[],
): [string, PropertyInput][] {
  return [
    ["externalId", PropertyInput.value(externalId)],
    ["name", PropertyInput.value(name)],
    ["age", PropertyInput.value(age)],
    ["score", PropertyInput.value(PropertyValue.f64(score))],
    ["status", PropertyInput.value(status)],
    ["tenantId", PropertyInput.value("tenant-a")],
    ["city", PropertyInput.value(city)],
    ["bio", PropertyInput.value(bio)],
    ["createdAt", PropertyInput.value(DateTime.fromMillis(1_776_000_000_000))],
    ["embedding", PropertyInput.value(PropertyValue.f32Array(embedding))],
  ];
}

function nestedMetadataProperty(externalID: string, score: number): PropertyValue {
  return PropertyValue.object({ externalID, score, tags: ["alpha", 7] });
}

function nestedMetadataParam(externalID: string, score: number) {
  return { externalID, score, tags: ["alpha", 7] };
}

function runtimeFixtures(): Fixture[] {
  return [
    runtime(
      "001-write-seed-core",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs(
            "alice",
            g().addN(
              "ParityUser",
              userProps("user-alice", "Alice", 31, 90.5, "active", "London", "Alice writes graph database tests", [1.0, 0.0, 0.0]),
            ),
          )
          .varAs(
            "bob",
            g().addN(
              "ParityUser",
              userProps("user-bob", "Bob", 27, 72.25, "active", "Paris", "Bob likes traversal testing", [0.9, 0.1, 0.0]),
            ),
          )
          .varAs(
            "carol",
            g().addN(
              "ParityUser",
              userProps("user-carol", "Carol", 42, 64.0, "inactive", "Berlin", "Carol archives old records", [0.0, 1.0, 0.0]),
            ),
          )
          .varAs(
            "alice_follows_bob",
            g()
              .n(NodeRef.var("alice"))
              .addE("FOLLOWS", NodeRef.var("bob"), [
                ["weight", PropertyInput.value(PropertyValue.f64(1.0))],
                ["since", PropertyInput.value("2024-01-01")],
                ["note", PropertyInput.value("Alice follows Bob")],
                ["embedding", PropertyInput.value(PropertyValue.f32Array([1.0, 0.0]))],
              ]),
          )
          .varAs(
            "bob_follows_carol",
            g()
              .n(NodeRef.var("bob"))
              .addE("FOLLOWS", NodeRef.var("carol"), [
                ["weight", PropertyInput.value(PropertyValue.f64(0.5))],
                ["since", PropertyInput.value("2024-02-01")],
                ["note", PropertyInput.value("Bob follows Carol")],
                ["embedding", PropertyInput.value(PropertyValue.f32Array([0.0, 1.0]))],
              ]),
          )
          .returning(["alice", "bob", "carol", "alice_follows_bob", "bob_follows_carol"]),
      ),
    ),
    runtime(
      "002-read-count-all-users",
      DynamicQueryRequest.read(readBatch().varAs("user_count", g().nWithLabel("ParityUser").count()).returning(["user_count"])),
    ),
    runtime(
      "003-read-source-predicate-and-count",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "active_adults",
            g()
              .nWithLabelWhere("ParityUser", SourcePredicate.and([SourcePredicate.eq("status", "active"), SourcePredicate.gte("age", 30)]))
              .count(),
          )
          .returning(["active_adults"]),
      ),
    ),
    runtime(
      "004-read-value-map-projection",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "alice",
            g()
              .nWithLabel("ParityUser")
              .where(Predicate.eq("externalId", "user-alice"))
              .project([
                Projection.property("externalId", "id"),
                Projection.property("name", "name"),
                Projection.expr("score_plus_one", Expr.prop("score").add(Expr.val(PropertyValue.f64(1.0)))),
                Projection.expr("status_label", Expr.case([[Predicate.eq("status", "active"), Expr.val("enabled")]], Expr.val("disabled"))),
              ]),
          )
          .returning(["alice"]),
      ),
    ),
    runtime(
      "005-read-order-range-values",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "ordered",
            g()
              .nWithLabel("ParityUser")
              .orderByMultiple([
                ["status", Order.Asc],
                ["age", Order.Desc],
              ])
              .range(0, 2)
              .valueMap(["externalId", "age", "status"]),
          )
          .returning(["ordered"]),
      ),
    ),
    runtime(
      "006-read-edge-count",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("edge_count", g().nWithLabel("ParityUser").where(Predicate.eq("externalId", "user-alice")).outE("FOLLOWS").count())
          .returning(["edge_count"]),
      ),
    ),
    runtime(
      "007-read-edge-properties",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "edges",
            g()
              .eWithLabel("FOLLOWS")
              .edgeHas("weight", PropertyInput.value(PropertyValue.f64(1.0)))
              .edgeProperties(),
          )
          .returning(["edges"]),
      ),
    ),
    runtime(
      "008-read-edge-endpoints",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("from_nodes", g().eWithLabel("FOLLOWS").edgeHasLabel("FOLLOWS").inN().valueMap(["externalId", "name"]))
          .varAs("to_nodes", g().eWithLabel("FOLLOWS").outN().valueMap(["externalId", "name"]))
          .returning(["from_nodes", "to_nodes"]),
      ),
    ),
    runtime(
      "009-read-conditional-var-not-empty",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("alice", g().nWithLabel("ParityUser").where(Predicate.eq("externalId", "user-alice")))
          .varAsIf(
            "friends",
            BatchCondition.varNotEmpty("alice"),
            g().n(NodeRef.var("alice")).out("FOLLOWS").valueMap(["externalId", "name"]),
          )
          .returning(["alice", "friends"]),
      ),
    ),
    runtime(
      "010-read-conditional-var-empty",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("missing", g().nWithLabel("ParityUser").where(Predicate.eq("externalId", "missing-user")))
          .varAsIf("fallback", BatchCondition.varEmpty("missing"), g().nWithLabel("ParityUser").limit(1).valueMap(["externalId"]))
          .returning(["missing", "fallback"]),
      ),
    ),
    runtime(
      "011-read-conditional-var-min-size-prev",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("users", g().nWithLabel("ParityUser").limit(3))
          .varAsIf("min_two", BatchCondition.varMinSize("users", 2), g().n(NodeRef.var("users")).count())
          .varAsIf("prev_ok", BatchCondition.prevNotEmpty(), g().n(NodeRef.var("users")).exists())
          .returning(["min_two", "prev_ok"]),
      ),
    ),
    runtime(
      "012-read-foreach-param",
      withParams(
        DynamicQueryRequest.read(
          readBatch()
            .forEachParam(
              "lookups",
              readBatch().varAs(
                "matched",
                g().nWithLabel("ParityUser").where(Predicate.eqParam("externalId", "externalId")).valueMap(["externalId", "name"]),
              ),
            )
            .returning(["matched"]),
        ),
        [["lookups", [{ externalId: "user-alice" }, { externalId: "user-carol" }]]],
        [["lookups", QueryParamType.array(QueryParamType.object())]],
      ),
    ),
    runtime(
      "013-write-foreach-param-create",
      withParams(
        DynamicQueryRequest.write(
          writeBatch()
            .forEachParam(
              "rows",
              writeBatch().varAs(
                "created",
                g().addN("ParityEvent", [
                  ["eventId", PropertyInput.param("eventId")],
                  ["kind", PropertyInput.param("kind")],
                  ["score", PropertyInput.param("score")],
                ]),
              ),
            )
            .returning(["created"]),
        ),
        [
          [
            "rows",
            [
              { eventId: "event-1", kind: "click", score: 10 },
              { eventId: "event-2", kind: "view", score: 5 },
            ],
          ],
        ],
        [["rows", QueryParamType.array(QueryParamType.object())]],
      ),
    ),
    runtime(
      "014-read-after-foreach-param",
      DynamicQueryRequest.read(readBatch().varAs("event_count", g().nWithLabel("ParityEvent").count()).returning(["event_count"])),
    ),
    runtime(
      "015-write-set-remove-properties",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs(
            "updated",
            g()
              .nWithLabel("ParityUser")
              .where(Predicate.eq("externalId", "user-bob"))
              .setProperty("status", PropertyInput.value("inactive"))
              .setProperty("updatedAt", PropertyInput.value(DateTime.fromMillis(1_777_000_000_000)))
              .removeProperty("city")
              .count(),
          )
          .returning(["updated"]),
      ),
    ),
    runtime(
      "016-read-updated-properties",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "bob",
            g()
              .nWithLabel("ParityUser")
              .where(Predicate.eq("externalId", "user-bob"))
              .valueMap(["externalId", "status", "updatedAt", "city"]),
          )
          .returning(["bob"]),
      ),
    ),
    runtime(
      "017-read-repeat-union",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "walked",
            g()
              .nWithLabel("ParityUser")
              .where(Predicate.eq("externalId", "user-alice"))
              .repeat(RepeatConfig.new(sub().out("FOLLOWS")).times(2).emitAll().maxDepth(4))
              .union([sub().out("FOLLOWS"), sub().in("FOLLOWS")])
              .dedup()
              .valueMap(["externalId", "name"]),
          )
          .returning(["walked"]),
      ),
    ),
    runtime(
      "018-read-choose-coalesce-optional",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "branched",
            g()
              .nWithLabel("ParityUser")
              .where(Predicate.eq("externalId", "user-alice"))
              .choose(Predicate.eq("status", "active"), sub().out("FOLLOWS"), sub().in("FOLLOWS"))
              .coalesce([sub().out("FOLLOWS"), sub().in("FOLLOWS")])
              .optional(sub().out("FOLLOWS"))
              .dedup()
              .valueMap(["externalId", "name"]),
          )
          .returning(["branched"]),
      ),
    ),
    runtime(
      "019-read-aggregations",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("by_status", g().nWithLabel("ParityUser").groupCount("status"))
          .varAs("mean_score", g().nWithLabel("ParityUser").aggregateBy(AggregateFunction.Mean, "score"))
          .varAs("max_age", g().nWithLabel("ParityUser").aggregateBy(AggregateFunction.Max, "age"))
          .returning(["by_status", "mean_score", "max_age"]),
      ),
    ),
    runtime(
      "020-write-index-create",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs("node_eq", g().createIndexIfNotExists(IndexSpec.nodeEquality("ParityUser", "externalId")))
          .varAs("node_range", g().createIndexIfNotExists(IndexSpec.nodeRange("ParityUser", "age")))
          .varAs("edge_eq", g().createIndexIfNotExists(IndexSpec.edgeEquality("FOLLOWS", "since")))
          .varAs("edge_range", g().createIndexIfNotExists(IndexSpec.edgeRange("FOLLOWS", "weight")))
          .returning(["node_eq", "node_range", "edge_eq", "edge_range"]),
      ),
    ),
    runtime(
      "021-read-parameter-types",
      withParams(
        DynamicQueryRequest.read(
          readBatch()
            .varAs(
              "matches",
              g()
                .nWithLabel("ParityUser")
                .where(Predicate.isInParam("status", "statuses"))
                .where(Predicate.gteParam("createdAt", "created_after"))
                .limit(Expr.param("limit"))
                .valueMap(["externalId", "status"]),
            )
            .returning(["matches"]),
        ),
        [
          ["statuses", ["active", "inactive"]],
          ["created_after", "2026-01-01T00:00:00.000Z"],
          ["limit", 5],
        ],
        [
          ["statuses", QueryParamType.array(QueryParamType.string())],
          ["created_after", QueryParamType.dateTime()],
          ["limit", QueryParamType.i64()],
        ],
      ),
    ),
    runtime(
      "022-write-property-value-variants",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs(
            "variant_node",
            g().addN("ParityVariant", [
              ["nullValue", PropertyInput.value(PropertyValue.null())],
              ["boolValue", PropertyInput.value(true)],
              ["i64Value", PropertyInput.value(PropertyValue.i64(9_223_372_036_854_775_000n))],
              ["dateTimeValue", PropertyInput.value(DateTime.fromMillis(-1))],
              ["f64Value", PropertyInput.value(3.25)],
              ["f32Value", PropertyInput.value(PropertyValue.f32(1.5))],
              ["stringValue", PropertyInput.value("variant")],
              ["bytesValue", PropertyInput.value(PropertyValue.bytes([1, 2, 3]))],
              ["i64Array", PropertyInput.value(PropertyValue.i64Array([1, 2, 3]))],
              ["f64Array", PropertyInput.value(PropertyValue.f64Array([1.0, 2.0]))],
              ["f32Array", PropertyInput.value(PropertyValue.f32Array([1.0, 2.0]))],
              ["stringArray", PropertyInput.value(PropertyValue.stringArray(["a", "b"]))],
            ]),
          )
          .returning(["variant_node"]),
      ),
    ),
    runtime(
      "023-read-property-value-variants",
      DynamicQueryRequest.read(readBatch().varAs("variant", g().nWithLabel("ParityVariant").valueMap(null)).returning(["variant"])),
    ),
    runtime(
      "024-write-text-vector-indexes",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs("node_text", g().createTextIndexNodes("ParityUser", "bio", null))
          .varAs("node_vector", g().createVectorIndexNodes("ParityUser", "embedding", null))
          .varAs("edge_text", g().createTextIndexEdges("FOLLOWS", "note", null))
          .varAs("edge_vector", g().createVectorIndexEdges("FOLLOWS", "embedding", null))
          .returning(["node_text", "node_vector", "edge_text", "edge_vector"]),
      ),
    ),
    runtime(
      "025-read-text-search-nodes",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("text_hits", g().textSearchNodes("ParityUser", "bio", "graph", 5, null).valueMap(["externalId", "bio", "$distance"]))
          .returning(["text_hits"]),
      ),
    ),
    runtime(
      "026-read-vector-search-nodes",
      DynamicQueryRequest.read(
        readBatch()
          .varAs(
            "vector_hits",
            g()
              .vectorSearchNodes("ParityUser", "embedding", [1.0, 0.0, 0.0], 3, null)
              .project([Projection.property("externalId", "externalId"), Projection.property("$distance", "distance")]),
          )
          .returning(["vector_hits"]),
      ),
    ),
    runtime(
      "027-read-text-search-edges",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("edge_text_hits", g().textSearchEdges("FOLLOWS", "note", "follows", 5, null).edgeProperties())
          .returning(["edge_text_hits"]),
      ),
    ),
    runtime(
      "028-read-vector-search-edges",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("edge_vector_hits", g().vectorSearchEdges("FOLLOWS", "embedding", [1.0, 0.0], 5, null).edgeProperties())
          .returning(["edge_vector_hits"]),
      ),
    ),
    runtime(
      "029-write-drop-temp-node",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs("temp", g().addN("ParityTemp", [["name", PropertyInput.value("temp")]]))
          .varAs("dropped", g().n(NodeRef.var("temp")).drop().count())
          .returning(["dropped"]),
      ),
    ),
    runtime(
      "030-read-final-counts",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("users", g().nWithLabel("ParityUser").count())
          .varAs("events", g().nWithLabel("ParityEvent").count())
          .varAs("variants", g().nWithLabel("ParityVariant").count())
          .returning(["users", "events", "variants"]),
      ),
    ),
    runtime(
      "031-read-source-predicate-eq-param",
      withParams(
        DynamicQueryRequest.read(
          readBatch()
            .varAs(
              "user",
              g()
                .nWhere(SourcePredicate.and([SourcePredicate.eq("$label", "ParityUser"), SourcePredicate.eq("name", Expr.param("name"))]))
                .valueMap(["externalId", "name"]),
            )
            .returning(["user"]),
        ),
        [["name", "Alice"]],
        [["name", QueryParamType.string()]],
      ),
    ),
    runtime(
      "032-read-source-predicate-between-param",
      withParams(
        DynamicQueryRequest.read(
          readBatch()
            .varAs(
              "adults",
              g()
                .nWhere(
                  SourcePredicate.and([
                    SourcePredicate.eq("$label", "ParityUser"),
                    SourcePredicate.between("age", Expr.param("min_age"), 65),
                  ]),
                )
                .valueMap(["externalId", "age"]),
            )
            .returning(["adults"]),
        ),
        [["min_age", 30]],
        [["min_age", QueryParamType.i64()]],
      ),
    ),
  ];
}

function nodePermutationFixtures(): Fixture[] {
  const sources = ["label", "where", "all"] as const;
  const filters = ["none", "has", "logic", "expr"] as const;
  const bounds = ["none", "limit", "skip", "range"] as const;
  const terminals = ["count", "exists", "value_map", "project"] as const;
  const fixtures: Fixture[] = [];
  let index = 100;
  for (const source of sources) {
    for (const filter of filters) {
      for (const bound of bounds) {
        for (const terminal of terminals) {
          fixtures.push(
            runtime(
              `${String(index).padStart(3, "0")}-combo-node-${source}-${filter}-${bound}-${terminal}`,
              DynamicQueryRequest.read(nodeComboBatch(source, filter, bound, terminal)),
            ),
          );
          index += 1;
        }
      }
    }
  }
  return fixtures;
}

function nodeComboBatch(source: string, filter: string, bound: string, terminal: string) {
  const traversal = applyNodeBound(applyNodeFilter(nodeSource(source), filter), bound).orderBy("externalId", Order.Asc);
  const terminalTraversal =
    terminal === "count"
      ? traversal.count()
      : terminal === "exists"
        ? traversal.exists()
        : terminal === "value_map"
          ? traversal.valueMap(["externalId", "name", "age", "status"])
          : terminal === "project"
            ? traversal.project([
                Projection.property("externalId", "externalId"),
                Projection.property("status", "status"),
                Projection.expr("age_plus_two", Expr.prop("age").add(Expr.val(2))),
              ])
            : (() => {
                throw new Error(`unknown terminal ${terminal}`);
              })();
  return readBatch().varAs("result", terminalTraversal).returning(["result"]);
}

function nodeSource(source: string): Traversal<"nodes", "read"> {
  if (source === "label") return g().nWithLabel("ParityUser");
  if (source === "where") return g().nWhere(SourcePredicate.eq("$label", "ParityUser"));
  if (source === "all") return g().n(NodeRef.all()).hasLabel("ParityUser");
  throw new Error(`unknown source ${source}`);
}

function applyNodeFilter(traversal: Traversal<"nodes", "read">, filter: string): Traversal<"nodes", "read"> {
  if (filter === "none") return traversal;
  if (filter === "has") return traversal.has("status", "active");
  if (filter === "logic") {
    return traversal.where(
      Predicate.and([
        Predicate.hasKey("externalId"),
        Predicate.or([Predicate.startsWith("name", "A"), Predicate.endsWith("name", "b")]),
        Predicate.not(Predicate.isNull("age")),
      ]),
    );
  }
  if (filter === "expr") {
    return traversal.where(
      Predicate.compare(Expr.prop("score").add(Expr.val(PropertyValue.f64(1.0))), CompareOp.Gt, Expr.val(PropertyValue.f64(65.0))),
    );
  }
  throw new Error(`unknown filter ${filter}`);
}

function applyNodeBound(traversal: Traversal<"nodes", "read">, bound: string): Traversal<"nodes", "read"> {
  if (bound === "none") return traversal;
  if (bound === "limit") return traversal.limit(2);
  if (bound === "skip") return traversal.skip(1);
  if (bound === "range") return traversal.range(0, 2);
  throw new Error(`unknown bound ${bound}`);
}

function jsonOnlyFixtures(): Fixture[] {
  return [
    jsonOnly(
      "900-exhaustive-raw-read-steps",
      withParams(
        DynamicQueryRequest.read(
          readBatch()
            .varAs(
              "raw_nodes",
              Traversal.fromSteps(
                [
                  Step.n(NodeRef.param("node_ids")),
                  Step.has("name", "Alice"),
                  Step.where(Predicate.containsParam("bio", "needle")),
                  Step.limit(StreamBound.expr(Expr.param("limit"))),
                  Step.skip(StreamBound.expr(Expr.param("skip"))),
                  Step.range(StreamBound.literal(0), StreamBound.expr(Expr.param("end"))),
                  Step.as("a"),
                  Step.store("stored"),
                  Step.select("stored"),
                  Step.dedup(),
                  Step.within("stored"),
                  Step.without("missing"),
                  Step.fold(),
                  Step.unfold(),
                  Step.path(),
                  Step.simplePath(),
                  Step.withSack(0),
                  Step.sackSet("score"),
                  Step.sackAdd("score"),
                  Step.sackGet(),
                  Step.project([Projection.property("externalId", "externalId"), Projection.expr("neg_age", Expr.prop("age").neg())]),
                ],
                "nodes",
                "read",
              ),
            )
            .varAs(
              "raw_edges",
              Traversal.fromSteps(
                [
                  Step.e(EdgeRef.param("edge_ids")),
                  Step.eWhere(SourcePredicate.or([SourcePredicate.hasKey("since"), SourcePredicate.startsWith("note", "Alice")])),
                  Step.outN(),
                  Step.inN(),
                  Step.otherN(),
                  Step.edgeHas("weight", PropertyInput.value(PropertyValue.f64(1.0))),
                  Step.edgeHasLabel("FOLLOWS"),
                  Step.orderBy("weight", Order.Desc),
                  Step.edgeProperties(),
                ],
                "edges",
                "read",
              ),
            )
            .returning(["raw_nodes", "raw_edges"]),
        ),
        [
          ["node_ids", [1, 2]],
          ["edge_ids", [1]],
          ["needle", "graph"],
          ["limit", 10],
          ["skip", 0],
          ["end", 10],
        ],
        [
          ["node_ids", QueryParamType.array(QueryParamType.i64())],
          ["edge_ids", QueryParamType.array(QueryParamType.i64())],
          ["needle", QueryParamType.string()],
          ["limit", QueryParamType.i64()],
          ["skip", QueryParamType.i64()],
          ["end", QueryParamType.i64()],
        ],
      ),
    ),
    jsonOnly(
      "901-exhaustive-raw-write-steps",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs(
            "raw_indexes",
            Traversal.fromSteps(
              [
                Step.createIndex(IndexSpec.nodeUniqueEquality("ParityUser", "externalId"), true),
                Step.dropIndex(IndexSpec.nodeRange("ParityUser", "age")),
                Step.createVectorIndexNodes("ParityUser", "embedding", "tenantId"),
                Step.createVectorIndexEdges("FOLLOWS", "embedding", "tenantId"),
                Step.createTextIndexNodes("ParityUser", "bio", "tenantId"),
                Step.createTextIndexEdges("FOLLOWS", "note", "tenantId"),
              ],
              "terminal",
              "write",
            ),
          )
          .varAs(
            "raw_mutations",
            Traversal.fromSteps(
              [
                Step.addN("RawNode", [["name", PropertyInput.value("raw")]]),
                Step.addE("RAW_EDGE", NodeRef.var("raw_mutations"), [["weight", PropertyInput.value(1)]]),
                Step.setProperty("name", PropertyInput.expr(Expr.param("name"))),
                Step.removeProperty("old"),
                Step.dropEdge(NodeRef.ids([999_999])),
                Step.dropEdgeLabeled(NodeRef.ids([999_999]), "RAW_EDGE"),
                Step.dropEdgeById(EdgeRef.ids([999_999])),
                Step.drop(),
              ],
              "nodes",
              "write",
            ),
          )
          .returning(["raw_indexes", "raw_mutations"]),
      ),
    ),
    jsonOnly(
      "902-dynamic-value-and-param-type-shapes",
      withParams(
        DynamicQueryRequest.read(readBatch().varAs("empty", g().nWithLabel("Missing").count()).returning(["empty"])),
        [
          ["null", DynamicQueryValue.null()],
          ["bool", DynamicQueryValue.bool(true)],
          ["i64", DynamicQueryValue.i64(9_223_372_036_854_775_807n)],
          ["f64", DynamicQueryValue.f64(1.25)],
          ["f32", DynamicQueryValue.f32(1.5)],
          ["string", DynamicQueryValue.string("value")],
          ["array", DynamicQueryValue.array([1, "two"])],
          ["object", DynamicQueryValue.object({ nested: true })],
        ],
        [
          ["null", QueryParamType.value()],
          ["bool", QueryParamType.bool()],
          ["i64", QueryParamType.i64()],
          ["f64", QueryParamType.f64()],
          ["f32", QueryParamType.f32()],
          ["string", QueryParamType.string()],
          ["array", QueryParamType.array(QueryParamType.value())],
          ["object", QueryParamType.object()],
        ],
      ),
    ),
    jsonOnly(
      "903-empty-source-vector-text-runtime-inputs",
      withParams(
        DynamicQueryRequest.read(
          readBatch()
            .varAs(
              "vector_nodes",
              g().vectorSearchNodesWith(
                "ParityUser",
                "embedding",
                PropertyInput.param("query_vector"),
                Expr.param("limit"),
                PropertyInput.param("tenant"),
              ),
            )
            .varAs(
              "text_nodes",
              g().textSearchNodesWith(
                "ParityUser",
                "bio",
                PropertyInput.param("query_text"),
                Expr.param("limit"),
                PropertyInput.param("tenant"),
              ),
            )
            .returning(["vector_nodes", "text_nodes"]),
        ),
        [
          ["query_vector", [1.0, 0.0, 0.0]],
          ["query_text", "graph"],
          ["limit", 5],
          ["tenant", "tenant-a"],
        ],
        [
          ["query_vector", QueryParamType.array(QueryParamType.f64())],
          ["query_text", QueryParamType.string()],
          ["limit", QueryParamType.i64()],
          ["tenant", QueryParamType.string()],
        ],
      ),
    ),
    jsonOnly(
      "904-empty-query-and-node-edge-ref-shapes",
      DynamicQueryRequest.read(
        readBatch()
          .varAs("all_nodes", Traversal.fromSteps([Step.n(NodeRef.all()), Step.count()], "nodes", "read"))
          .varAs("node_ids", Traversal.fromSteps([Step.n(NodeRef.ids([1, 2])), Step.id()], "nodes", "read"))
          .varAs("node_var", Traversal.fromSteps([Step.n(NodeRef.var("all_nodes")), Step.label()], "nodes", "read"))
          .varAs("edge_ids", Traversal.fromSteps([Step.e(EdgeRef.ids([1, 2])), Step.id()], "edges", "read"))
          .varAs("edge_var", Traversal.fromSteps([Step.e(EdgeRef.var("edge_ids")), Step.label()], "edges", "read"))
          .returning(["all_nodes", "node_ids", "node_var", "edge_ids", "edge_var"]),
      ),
    ),
    jsonOnly(
      "905-empty-traversal-source-mutators",
      DynamicQueryRequest.write(
        writeBatch()
          .varAs("inject", Traversal.new().inject("some_var").count())
          .varAs("drop_edge_by_id", g().dropEdgeById(EdgeRef.id(123_456)).count())
          .returning(["inject", "drop_edge_by_id"]),
      ),
    ),
    jsonOnly(
      "906-nested-dynamic-property-write-shapes",
      withParams(
        DynamicQueryRequest.write(
          writeBatch()
            .varAs(
              "created",
              g().addN("ParityNested", [
                ["name", PropertyInput.value("nested")],
                ["metadata", PropertyInput.value(nestedMetadataProperty("some_id", 20))],
              ]),
            )
            .varAs(
              "updated",
              g().n(NodeRef.var("created")).setProperty("metadata", PropertyInput.param("metadata")).valueMap(["metadata.externalID"]),
            )
            .varAs("target", g().addN("ParityNestedTarget", [["name", PropertyInput.value("target")]]))
            .varAs(
              "edge",
              g()
                .n(NodeRef.var("created"))
                .addE("NESTED_LINK", NodeRef.var("target"), [["metadata", PropertyInput.value(nestedMetadataProperty("edge_id", 5))]])
                .count(),
            )
            .returning(["created", "updated", "edge"]),
        ),
        [["metadata", nestedMetadataParam("param_id", 22)]],
        [["metadata", QueryParamType.object()]],
      ),
    ),
    jsonOnly(
      "907-nested-dynamic-property-read-shapes",
      withParams(
        DynamicQueryRequest.read(
          readBatch()
            .varAs(
              "nested_users",
              g()
                .nWhere(
                  SourcePredicate.and([
                    SourcePredicate.eq("$label", "ParityNested"),
                    SourcePredicate.eq("metadata.externalID", Expr.param("external_id")),
                  ]),
                )
                .where(Predicate.compare(Expr.prop("metadata.score"), CompareOp.Gt, Expr.val(10)))
                .orderByMultiple([
                  ["metadata.score", Order.Desc],
                  ["name", Order.Asc],
                ])
                .project([
                  Projection.property("metadata.externalID", "external_id"),
                  Projection.expr("score_copy", Expr.prop("metadata.score")),
                ]),
            )
            .varAs("nested_values", g().nWithLabel("ParityNested").values(["metadata.externalID"]))
            .varAs("nested_map", g().nWithLabel("ParityNested").valueMap(["metadata.externalID", "metadata.score"]))
            .varAs(
              "nested_edges",
              g()
                .eWhere(
                  SourcePredicate.and([SourcePredicate.eq("$label", "NESTED_LINK"), SourcePredicate.eq("metadata.externalID", "edge_id")]),
                )
                .edgeHas("metadata.externalID", PropertyInput.value("edge_id"))
                .edgeProperties(),
            )
            .returning(["nested_users", "nested_values", "nested_map", "nested_edges"]),
        ),
        [["external_id", "param_id"]],
        [["external_id", QueryParamType.string()]],
      ),
    ),
  ];
}

void stringifyJson;
