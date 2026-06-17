use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::Path;

use helix_db::dsl::prelude::*;
use helix_db::{Empty, OnNodes, QueryParamType, ReadOnly, Step, Traversal, WriteEnabled};

struct Fixture {
    bucket: &'static str,
    name: String,
    request: DynamicQueryRequest,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let out = env::args()
        .nth(1)
        .unwrap_or_else(|| "tests/parity/generated/rust".to_string());
    let out = Path::new(&out);
    reset_dir(&out.join("runtime"))?;
    reset_dir(&out.join("json-only"))?;

    let mut fixtures = runtime_fixtures();
    fixtures.extend(node_permutation_fixtures());
    fixtures.extend(json_only_fixtures());

    for fixture in fixtures {
        let path = out
            .join(fixture.bucket)
            .join(format!("{}.json", fixture.name));
        fs::write(path, fixture.request.to_json_string()?)?;
    }

    Ok(())
}

fn reset_dir(path: &Path) -> std::io::Result<()> {
    if path.exists() {
        fs::remove_dir_all(path)?;
    }
    fs::create_dir_all(path)
}

fn runtime(name: impl Into<String>, request: DynamicQueryRequest) -> Fixture {
    Fixture {
        bucket: "runtime",
        name: name.into(),
        request,
    }
}

fn json_only(name: impl Into<String>, request: DynamicQueryRequest) -> Fixture {
    Fixture {
        bucket: "json-only",
        name: name.into(),
        request,
    }
}

fn read_request(batch: ReadBatch) -> DynamicQueryRequest {
    DynamicQueryRequest::read(batch)
}

fn write_request(batch: WriteBatch) -> DynamicQueryRequest {
    DynamicQueryRequest::write(batch)
}

fn object(entries: Vec<(&str, DynamicQueryValue)>) -> DynamicQueryValue {
    DynamicQueryValue::Object(
        entries
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect::<BTreeMap<_, _>>(),
    )
}

fn array(values: Vec<DynamicQueryValue>) -> DynamicQueryValue {
    DynamicQueryValue::Array(values)
}

fn string(value: &str) -> DynamicQueryValue {
    DynamicQueryValue::String(value.to_string())
}

fn i64_value(value: i64) -> DynamicQueryValue {
    DynamicQueryValue::I64(value)
}

fn f64_value(value: f64) -> DynamicQueryValue {
    DynamicQueryValue::F64(value)
}

fn with_params(
    mut request: DynamicQueryRequest,
    values: Vec<(&str, DynamicQueryValue)>,
    types: Vec<(&str, QueryParamType)>,
) -> DynamicQueryRequest {
    for (name, value) in values {
        request.insert_parameter_value(name, value);
    }
    for (name, ty) in types {
        request.insert_parameter_type(name, ty);
    }
    request
}

fn user_props(
    external_id: &'static str,
    name: &'static str,
    age: i64,
    score: f64,
    status: &'static str,
    city: &'static str,
    bio: &'static str,
    embedding: Vec<f32>,
) -> Vec<(&'static str, PropertyInput)> {
    vec![
        ("externalId", PropertyInput::from(external_id)),
        ("name", PropertyInput::from(name)),
        ("age", PropertyInput::from(age)),
        ("score", PropertyInput::from(score)),
        ("status", PropertyInput::from(status)),
        ("tenantId", PropertyInput::from("tenant-a")),
        ("city", PropertyInput::from(city)),
        ("bio", PropertyInput::from(bio)),
        (
            "createdAt",
            PropertyInput::from(DateTime::from_millis(1_776_000_000_000)),
        ),
        (
            "embedding",
            PropertyInput::from(PropertyValue::from(embedding)),
        ),
    ]
}

fn nested_metadata_property(external_id: &str, score: i64) -> PropertyValue {
    PropertyValue::object(vec![
        ("externalID", PropertyValue::from(external_id)),
        ("score", PropertyValue::from(score)),
        (
            "tags",
            PropertyValue::array(vec![
                PropertyValue::from("alpha"),
                PropertyValue::from(7i64),
            ]),
        ),
    ])
}

fn nested_metadata_param(external_id: &str, score: i64) -> DynamicQueryValue {
    object(vec![
        ("externalID", string(external_id)),
        ("score", i64_value(score)),
        ("tags", array(vec![string("alpha"), i64_value(7)])),
    ])
}

fn runtime_fixtures() -> Vec<Fixture> {
    vec![
        runtime(
            "001-write-seed-core",
            write_request(
                write_batch()
                    .var_as(
                        "alice",
                        g().add_n(
                            "ParityUser",
                            user_props(
                                "user-alice",
                                "Alice",
                                31,
                                90.5,
                                "active",
                                "London",
                                "Alice writes graph database tests",
                                vec![1.0, 0.0, 0.0],
                            ),
                        ),
                    )
                    .var_as(
                        "bob",
                        g().add_n(
                            "ParityUser",
                            user_props(
                                "user-bob",
                                "Bob",
                                27,
                                72.25,
                                "active",
                                "Paris",
                                "Bob likes traversal testing",
                                vec![0.9, 0.1, 0.0],
                            ),
                        ),
                    )
                    .var_as(
                        "carol",
                        g().add_n(
                            "ParityUser",
                            user_props(
                                "user-carol",
                                "Carol",
                                42,
                                64.0,
                                "inactive",
                                "Berlin",
                                "Carol archives old records",
                                vec![0.0, 1.0, 0.0],
                            ),
                        ),
                    )
                    .var_as(
                        "alice_follows_bob",
                        g().n(NodeRef::var("alice")).add_e(
                            "FOLLOWS",
                            NodeRef::var("bob"),
                            vec![
                                ("weight", PropertyInput::from(1.0f64)),
                                ("since", PropertyInput::from("2024-01-01")),
                                ("note", PropertyInput::from("Alice follows Bob")),
                                (
                                    "embedding",
                                    PropertyInput::from(PropertyValue::from(vec![1.0f32, 0.0])),
                                ),
                            ],
                        ),
                    )
                    .var_as(
                        "bob_follows_carol",
                        g().n(NodeRef::var("bob")).add_e(
                            "FOLLOWS",
                            NodeRef::var("carol"),
                            vec![
                                ("weight", PropertyInput::from(0.5f64)),
                                ("since", PropertyInput::from("2024-02-01")),
                                ("note", PropertyInput::from("Bob follows Carol")),
                                (
                                    "embedding",
                                    PropertyInput::from(PropertyValue::from(vec![0.0f32, 1.0])),
                                ),
                            ],
                        ),
                    )
                    .returning([
                        "alice",
                        "bob",
                        "carol",
                        "alice_follows_bob",
                        "bob_follows_carol",
                    ]),
            ),
        ),
        runtime(
            "002-read-count-all-users",
            read_request(
                read_batch()
                    .var_as("user_count", g().n_with_label("ParityUser").count())
                    .returning(["user_count"]),
            ),
        ),
        runtime(
            "003-read-source-predicate-and-count",
            read_request(
                read_batch()
                    .var_as(
                        "active_adults",
                        g().n_with_label_where(
                            "ParityUser",
                            SourcePredicate::and(vec![
                                SourcePredicate::eq("status", "active"),
                                SourcePredicate::gte("age", 30i64),
                            ]),
                        )
                        .count(),
                    )
                    .returning(["active_adults"]),
            ),
        ),
        runtime(
            "004-read-value-map-projection",
            read_request(
                read_batch()
                    .var_as(
                        "alice",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "user-alice"))
                            .project(vec![
                                Projection::property("externalId", "id"),
                                Projection::property("name", "name"),
                                Projection::expr(
                                    "score_plus_one",
                                    Expr::prop("score").add(Expr::val(1.0f64)),
                                ),
                                Projection::expr(
                                    "status_label",
                                    Expr::case(
                                        vec![(
                                            Predicate::eq("status", "active"),
                                            Expr::val("enabled"),
                                        )],
                                        Some(Expr::val("disabled")),
                                    ),
                                ),
                            ]),
                    )
                    .returning(["alice"]),
            ),
        ),
        runtime(
            "005-read-order-range-values",
            read_request(
                read_batch()
                    .var_as(
                        "ordered",
                        g().n_with_label("ParityUser")
                            .order_by_multiple(vec![("status", Order::Asc), ("age", Order::Desc)])
                            .range(0usize, 2usize)
                            .value_map(Some(vec!["externalId", "age", "status"])),
                    )
                    .returning(["ordered"]),
            ),
        ),
        runtime(
            "006-read-edge-count",
            read_request(
                read_batch()
                    .var_as(
                        "edge_count",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "user-alice"))
                            .out_e(Some("FOLLOWS"))
                            .count(),
                    )
                    .returning(["edge_count"]),
            ),
        ),
        runtime(
            "007-read-edge-properties",
            read_request(
                read_batch()
                    .var_as(
                        "edges",
                        g().e_with_label("FOLLOWS")
                            .edge_has("weight", PropertyInput::from(1.0f64))
                            .edge_properties(),
                    )
                    .returning(["edges"]),
            ),
        ),
        runtime(
            "008-read-edge-endpoints",
            read_request(
                read_batch()
                    .var_as(
                        "from_nodes",
                        g().e_with_label("FOLLOWS")
                            .edge_has_label("FOLLOWS")
                            .in_n()
                            .value_map(Some(vec!["externalId", "name"])),
                    )
                    .var_as(
                        "to_nodes",
                        g().e_with_label("FOLLOWS")
                            .out_n()
                            .value_map(Some(vec!["externalId", "name"])),
                    )
                    .returning(["from_nodes", "to_nodes"]),
            ),
        ),
        runtime(
            "009-read-conditional-var-not-empty",
            read_request(
                read_batch()
                    .var_as(
                        "alice",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "user-alice")),
                    )
                    .var_as_if(
                        "friends",
                        BatchCondition::VarNotEmpty("alice".to_string()),
                        g().n(NodeRef::var("alice"))
                            .out(Some("FOLLOWS"))
                            .value_map(Some(vec!["externalId", "name"])),
                    )
                    .returning(["alice", "friends"]),
            ),
        ),
        runtime(
            "010-read-conditional-var-empty",
            read_request(
                read_batch()
                    .var_as(
                        "missing",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "missing-user")),
                    )
                    .var_as_if(
                        "fallback",
                        BatchCondition::VarEmpty("missing".to_string()),
                        g().n_with_label("ParityUser")
                            .limit(1usize)
                            .value_map(Some(vec!["externalId"])),
                    )
                    .returning(["missing", "fallback"]),
            ),
        ),
        runtime(
            "011-read-conditional-var-min-size-prev",
            read_request(
                read_batch()
                    .var_as("users", g().n_with_label("ParityUser").limit(3usize))
                    .var_as_if(
                        "min_two",
                        BatchCondition::VarMinSize("users".to_string(), 2),
                        g().n(NodeRef::var("users")).count(),
                    )
                    .var_as_if(
                        "prev_ok",
                        BatchCondition::PrevNotEmpty,
                        g().n(NodeRef::var("users")).exists(),
                    )
                    .returning(["min_two", "prev_ok"]),
            ),
        ),
        runtime(
            "012-read-foreach-param",
            with_params(
                read_request(
                    read_batch()
                        .for_each_param(
                            "lookups",
                            read_batch().var_as(
                                "matched",
                                g().n_with_label("ParityUser")
                                    .where_(Predicate::eq_param("externalId", "externalId"))
                                    .value_map(Some(vec!["externalId", "name"])),
                            ),
                        )
                        .returning(["matched"]),
                ),
                vec![(
                    "lookups",
                    array(vec![
                        object(vec![("externalId", string("user-alice"))]),
                        object(vec![("externalId", string("user-carol"))]),
                    ]),
                )],
                vec![(
                    "lookups",
                    QueryParamType::Array(Box::new(QueryParamType::Object)),
                )],
            ),
        ),
        runtime(
            "013-write-foreach-param-create",
            with_params(
                write_request(
                    write_batch()
                        .for_each_param(
                            "rows",
                            write_batch().var_as(
                                "created",
                                g().add_n(
                                    "ParityEvent",
                                    vec![
                                        ("eventId", PropertyInput::param("eventId")),
                                        ("kind", PropertyInput::param("kind")),
                                        ("score", PropertyInput::param("score")),
                                    ],
                                ),
                            ),
                        )
                        .returning(["created"]),
                ),
                vec![(
                    "rows",
                    array(vec![
                        object(vec![
                            ("eventId", string("event-1")),
                            ("kind", string("click")),
                            ("score", i64_value(10)),
                        ]),
                        object(vec![
                            ("eventId", string("event-2")),
                            ("kind", string("view")),
                            ("score", i64_value(5)),
                        ]),
                    ]),
                )],
                vec![(
                    "rows",
                    QueryParamType::Array(Box::new(QueryParamType::Object)),
                )],
            ),
        ),
        runtime(
            "014-read-after-foreach-param",
            read_request(
                read_batch()
                    .var_as("event_count", g().n_with_label("ParityEvent").count())
                    .returning(["event_count"]),
            ),
        ),
        runtime(
            "015-write-set-remove-properties",
            write_request(
                write_batch()
                    .var_as(
                        "updated",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "user-bob"))
                            .set_property("status", PropertyInput::from("inactive"))
                            .set_property(
                                "updatedAt",
                                PropertyInput::from(DateTime::from_millis(1_777_000_000_000)),
                            )
                            .remove_property("city")
                            .count(),
                    )
                    .returning(["updated"]),
            ),
        ),
        runtime(
            "016-read-updated-properties",
            read_request(
                read_batch()
                    .var_as(
                        "bob",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "user-bob"))
                            .value_map(Some(vec!["externalId", "status", "updatedAt", "city"])),
                    )
                    .returning(["bob"]),
            ),
        ),
        runtime(
            "017-read-repeat-union",
            read_request(
                read_batch()
                    .var_as(
                        "walked",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "user-alice"))
                            .repeat(
                                RepeatConfig::new(sub().out(Some("FOLLOWS")))
                                    .times(2)
                                    .emit_all()
                                    .max_depth(4),
                            )
                            .union(vec![sub().out(Some("FOLLOWS")), sub().in_(Some("FOLLOWS"))])
                            .dedup()
                            .value_map(Some(vec!["externalId", "name"])),
                    )
                    .returning(["walked"]),
            ),
        ),
        runtime(
            "018-read-choose-coalesce-optional",
            read_request(
                read_batch()
                    .var_as(
                        "branched",
                        g().n_with_label("ParityUser")
                            .where_(Predicate::eq("externalId", "user-alice"))
                            .choose(
                                Predicate::eq("status", "active"),
                                sub().out(Some("FOLLOWS")),
                                Some(sub().in_(Some("FOLLOWS"))),
                            )
                            .coalesce(vec![sub().out(Some("FOLLOWS")), sub().in_(Some("FOLLOWS"))])
                            .optional(sub().out(Some("FOLLOWS")))
                            .dedup()
                            .value_map(Some(vec!["externalId", "name"])),
                    )
                    .returning(["branched"]),
            ),
        ),
        runtime(
            "019-read-aggregations",
            read_request(
                read_batch()
                    .var_as(
                        "by_status",
                        g().n_with_label("ParityUser").group_count("status"),
                    )
                    .var_as(
                        "mean_score",
                        g().n_with_label("ParityUser")
                            .aggregate_by(AggregateFunction::Mean, "score"),
                    )
                    .var_as(
                        "max_age",
                        g().n_with_label("ParityUser")
                            .aggregate_by(AggregateFunction::Max, "age"),
                    )
                    .returning(["by_status", "mean_score", "max_age"]),
            ),
        ),
        runtime(
            "020-write-index-create",
            write_request(
                write_batch()
                    .var_as(
                        "node_eq",
                        g().create_index_if_not_exists(IndexSpec::node_equality(
                            "ParityUser",
                            "externalId",
                        )),
                    )
                    .var_as(
                        "node_range",
                        g().create_index_if_not_exists(IndexSpec::node_range("ParityUser", "age")),
                    )
                    .var_as(
                        "edge_eq",
                        g().create_index_if_not_exists(IndexSpec::edge_equality(
                            "FOLLOWS", "since",
                        )),
                    )
                    .var_as(
                        "edge_range",
                        g().create_index_if_not_exists(IndexSpec::edge_range("FOLLOWS", "weight")),
                    )
                    .returning(["node_eq", "node_range", "edge_eq", "edge_range"]),
            ),
        ),
        runtime(
            "021-read-parameter-types",
            with_params(
                read_request(
                    read_batch()
                        .var_as(
                            "matches",
                            g().n_with_label("ParityUser")
                                .where_(Predicate::is_in_param("status", "statuses"))
                                .where_(Predicate::gte_param("createdAt", "created_after"))
                                .limit(Expr::param("limit"))
                                .value_map(Some(vec!["externalId", "status"])),
                        )
                        .returning(["matches"]),
                ),
                vec![
                    (
                        "statuses",
                        array(vec![string("active"), string("inactive")]),
                    ),
                    ("created_after", string("2026-01-01T00:00:00.000Z")),
                    ("limit", i64_value(5)),
                ],
                vec![
                    (
                        "statuses",
                        QueryParamType::Array(Box::new(QueryParamType::String)),
                    ),
                    ("created_after", QueryParamType::DateTime),
                    ("limit", QueryParamType::I64),
                ],
            ),
        ),
        runtime(
            "022-write-property-value-variants",
            write_request(
                write_batch()
                    .var_as(
                        "variant_node",
                        g().add_n(
                            "ParityVariant",
                            vec![
                                ("nullValue", PropertyInput::from(PropertyValue::Null)),
                                ("boolValue", PropertyInput::from(true)),
                                (
                                    "i64Value",
                                    PropertyInput::from(9_223_372_036_854_775_000i64),
                                ),
                                (
                                    "dateTimeValue",
                                    PropertyInput::from(DateTime::from_millis(-1)),
                                ),
                                ("f64Value", PropertyInput::from(3.25f64)),
                                ("f32Value", PropertyInput::from(1.5f32)),
                                ("stringValue", PropertyInput::from("variant")),
                                (
                                    "bytesValue",
                                    PropertyInput::from(PropertyValue::from(vec![1u8, 2u8, 3u8])),
                                ),
                                (
                                    "i64Array",
                                    PropertyInput::from(PropertyValue::from(vec![
                                        1i64, 2i64, 3i64,
                                    ])),
                                ),
                                (
                                    "f64Array",
                                    PropertyInput::from(PropertyValue::from(vec![1.0f64, 2.0f64])),
                                ),
                                (
                                    "f32Array",
                                    PropertyInput::from(PropertyValue::from(vec![1.0f32, 2.0f32])),
                                ),
                                (
                                    "stringArray",
                                    PropertyInput::from(PropertyValue::from(vec![
                                        "a".to_string(),
                                        "b".to_string(),
                                    ])),
                                ),
                            ],
                        ),
                    )
                    .returning(["variant_node"]),
            ),
        ),
        runtime(
            "023-read-property-value-variants",
            read_request(
                read_batch()
                    .var_as(
                        "variant",
                        g().n_with_label("ParityVariant")
                            .value_map(None::<Vec<&str>>),
                    )
                    .returning(["variant"]),
            ),
        ),
        runtime(
            "024-write-text-vector-indexes",
            write_request(
                write_batch()
                    .var_as(
                        "node_text",
                        g().create_text_index_nodes("ParityUser", "bio", None::<&str>),
                    )
                    .var_as(
                        "node_vector",
                        g().create_vector_index_nodes("ParityUser", "embedding", None::<&str>),
                    )
                    .var_as(
                        "edge_text",
                        g().create_text_index_edges("FOLLOWS", "note", None::<&str>),
                    )
                    .var_as(
                        "edge_vector",
                        g().create_vector_index_edges("FOLLOWS", "embedding", None::<&str>),
                    )
                    .returning(["node_text", "node_vector", "edge_text", "edge_vector"]),
            ),
        ),
        runtime(
            "025-read-text-search-nodes",
            read_request(
                read_batch()
                    .var_as(
                        "text_hits",
                        g().text_search_nodes("ParityUser", "bio", "graph", 5, None)
                            .value_map(Some(vec!["externalId", "bio", "$distance"])),
                    )
                    .returning(["text_hits"]),
            ),
        ),
        runtime(
            "026-read-vector-search-nodes",
            read_request(
                read_batch()
                    .var_as(
                        "vector_hits",
                        g().vector_search_nodes(
                            "ParityUser",
                            "embedding",
                            vec![1.0, 0.0, 0.0],
                            3,
                            None,
                        )
                        .project(vec![
                            Projection::property("externalId", "externalId"),
                            Projection::property("$distance", "distance"),
                        ]),
                    )
                    .returning(["vector_hits"]),
            ),
        ),
        runtime(
            "027-read-text-search-edges",
            read_request(
                read_batch()
                    .var_as(
                        "edge_text_hits",
                        g().text_search_edges("FOLLOWS", "note", "follows", 5, None)
                            .edge_properties(),
                    )
                    .returning(["edge_text_hits"]),
            ),
        ),
        runtime(
            "028-read-vector-search-edges",
            read_request(
                read_batch()
                    .var_as(
                        "edge_vector_hits",
                        g().vector_search_edges("FOLLOWS", "embedding", vec![1.0, 0.0], 5, None)
                            .edge_properties(),
                    )
                    .returning(["edge_vector_hits"]),
            ),
        ),
        runtime(
            "029-write-drop-temp-node",
            write_request(
                write_batch()
                    .var_as(
                        "temp",
                        g().add_n("ParityTemp", vec![("name", PropertyInput::from("temp"))]),
                    )
                    .var_as("dropped", g().n(NodeRef::var("temp")).drop().count())
                    .returning(["dropped"]),
            ),
        ),
        runtime(
            "030-read-final-counts",
            read_request(
                read_batch()
                    .var_as("users", g().n_with_label("ParityUser").count())
                    .var_as("events", g().n_with_label("ParityEvent").count())
                    .var_as("variants", g().n_with_label("ParityVariant").count())
                    .returning(["users", "events", "variants"]),
            ),
        ),
        runtime(
            "031-read-source-predicate-eq-param",
            with_params(
                read_request(
                    read_batch()
                        .var_as(
                            "user",
                            g().n_where(SourcePredicate::and(vec![
                                SourcePredicate::eq("$label", "ParityUser"),
                                SourcePredicate::eq("name", Expr::param("name")),
                            ]))
                            .value_map(Some(vec!["externalId", "name"])),
                        )
                        .returning(["user"]),
                ),
                vec![("name", string("Alice"))],
                vec![("name", QueryParamType::String)],
            ),
        ),
        runtime(
            "032-read-source-predicate-between-param",
            with_params(
                read_request(
                    read_batch()
                        .var_as(
                            "adults",
                            g().n_where(SourcePredicate::and(vec![
                                SourcePredicate::eq("$label", "ParityUser"),
                                SourcePredicate::between("age", Expr::param("min_age"), 65i64),
                            ]))
                            .value_map(Some(vec!["externalId", "age"])),
                        )
                        .returning(["adults"]),
                ),
                vec![("min_age", i64_value(30))],
                vec![("min_age", QueryParamType::I64)],
            ),
        ),
    ]
}

fn node_permutation_fixtures() -> Vec<Fixture> {
    let sources = ["label", "where", "all"];
    let filters = ["none", "has", "logic", "expr"];
    let bounds = ["none", "limit", "skip", "range"];
    let terminals = ["count", "exists", "value_map", "project"];

    let mut fixtures = Vec::new();
    let mut index = 100;
    for source in sources {
        for filter in filters {
            for bound in bounds {
                for terminal in terminals {
                    let name =
                        format!("{index:03}-combo-node-{source}-{filter}-{bound}-{terminal}");
                    index += 1;
                    fixtures.push(runtime(
                        name,
                        read_request(node_combo_batch(source, filter, bound, terminal)),
                    ));
                }
            }
        }
    }
    fixtures
}

fn node_combo_batch(source: &str, filter: &str, bound: &str, terminal: &str) -> ReadBatch {
    let traversal = apply_node_bound(apply_node_filter(node_source(source), filter), bound)
        .order_by("externalId", Order::Asc);
    let traversal = match terminal {
        "count" => traversal.count(),
        "exists" => traversal.exists(),
        "value_map" => traversal.value_map(Some(vec!["externalId", "name", "age", "status"])),
        "project" => traversal.project(vec![
            Projection::property("externalId", "externalId"),
            Projection::property("status", "status"),
            Projection::expr("age_plus_two", Expr::prop("age").add(Expr::val(2i64))),
        ]),
        other => panic!("unknown terminal {other}"),
    };
    read_batch()
        .var_as("result", traversal)
        .returning(["result"])
}

fn node_source(source: &str) -> Traversal<OnNodes, ReadOnly> {
    match source {
        "label" => g().n_with_label("ParityUser"),
        "where" => g().n_where(SourcePredicate::eq("$label", "ParityUser")),
        "all" => g().n(NodeRef::all()).has_label("ParityUser"),
        other => panic!("unknown source {other}"),
    }
}

fn apply_node_filter(
    traversal: Traversal<OnNodes, ReadOnly>,
    filter: &str,
) -> Traversal<OnNodes, ReadOnly> {
    match filter {
        "none" => traversal,
        "has" => traversal.has("status", "active"),
        "logic" => traversal.where_(Predicate::and(vec![
            Predicate::has_key("externalId"),
            Predicate::or(vec![
                Predicate::starts_with("name", "A"),
                Predicate::ends_with("name", "b"),
            ]),
            Predicate::not(Predicate::is_null("age")),
        ])),
        "expr" => traversal.where_(Predicate::compare(
            Expr::prop("score").add(Expr::val(1.0f64)),
            CompareOp::Gt,
            Expr::val(65.0f64),
        )),
        other => panic!("unknown filter {other}"),
    }
}

fn apply_node_bound(
    traversal: Traversal<OnNodes, ReadOnly>,
    bound: &str,
) -> Traversal<OnNodes, ReadOnly> {
    match bound {
        "none" => traversal,
        "limit" => traversal.limit(2usize),
        "skip" => traversal.skip(1usize),
        "range" => traversal.range(0usize, 2usize),
        other => panic!("unknown bound {other}"),
    }
}

fn json_only_fixtures() -> Vec<Fixture> {
    vec![
        json_only(
            "900-exhaustive-raw-read-steps",
            with_params(
                read_request(
                    read_batch()
                        .var_as(
                            "raw_nodes",
                            Traversal::<OnNodes, ReadOnly>::from_steps(vec![
                                Step::N(NodeRef::Param("node_ids".to_string())),
                                Step::Has("name".to_string(), PropertyValue::from("Alice")),
                                Step::Where(Predicate::contains_param("bio", "needle")),
                                Step::LimitBy(Expr::param("limit")),
                                Step::SkipBy(Expr::param("skip")),
                                Step::RangeBy(
                                    StreamBound::literal(0),
                                    StreamBound::expr(Expr::param("end")),
                                ),
                                Step::As("a".to_string()),
                                Step::Store("stored".to_string()),
                                Step::Select("stored".to_string()),
                                Step::Dedup,
                                Step::Within("stored".to_string()),
                                Step::Without("missing".to_string()),
                                Step::Fold,
                                Step::Unfold,
                                Step::Path,
                                Step::SimplePath,
                                Step::WithSack(PropertyValue::from(0i64)),
                                Step::SackSet("score".to_string()),
                                Step::SackAdd("score".to_string()),
                                Step::SackGet,
                                Step::Project(vec![
                                    Projection::property("externalId", "externalId"),
                                    Projection::expr("neg_age", Expr::prop("age").neg()),
                                ]),
                            ]),
                        )
                        .var_as(
                            "raw_edges",
                            Traversal::<helix_db::OnEdges, ReadOnly>::from_steps(vec![
                                Step::E(EdgeRef::Param("edge_ids".to_string())),
                                Step::EWhere(SourcePredicate::or(vec![
                                    SourcePredicate::has_key("since"),
                                    SourcePredicate::starts_with("note", "Alice"),
                                ])),
                                Step::OutN,
                                Step::InN,
                                Step::OtherN,
                                Step::EdgeHas("weight".to_string(), PropertyInput::from(1.0f64)),
                                Step::EdgeHasLabel("FOLLOWS".to_string()),
                                Step::OrderBy("weight".to_string(), Order::Desc),
                                Step::EdgeProperties,
                            ]),
                        )
                        .returning(["raw_nodes", "raw_edges"]),
                ),
                vec![
                    ("node_ids", array(vec![i64_value(1), i64_value(2)])),
                    ("edge_ids", array(vec![i64_value(1)])),
                    ("needle", string("graph")),
                    ("limit", i64_value(10)),
                    ("skip", i64_value(0)),
                    ("end", i64_value(10)),
                ],
                vec![
                    (
                        "node_ids",
                        QueryParamType::Array(Box::new(QueryParamType::I64)),
                    ),
                    (
                        "edge_ids",
                        QueryParamType::Array(Box::new(QueryParamType::I64)),
                    ),
                    ("needle", QueryParamType::String),
                    ("limit", QueryParamType::I64),
                    ("skip", QueryParamType::I64),
                    ("end", QueryParamType::I64),
                ],
            ),
        ),
        json_only(
            "901-exhaustive-raw-write-steps",
            write_request(
                write_batch()
                    .var_as(
                        "raw_indexes",
                        Traversal::<helix_db::Terminal, WriteEnabled>::from_steps(vec![
                            Step::CreateIndex {
                                spec: IndexSpec::node_unique_equality("ParityUser", "externalId"),
                                if_not_exists: true,
                            },
                            Step::DropIndex {
                                spec: IndexSpec::node_range("ParityUser", "age"),
                            },
                            Step::CreateVectorIndexNodes {
                                label: "ParityUser".to_string(),
                                property: "embedding".to_string(),
                                tenant_property: Some("tenantId".to_string()),
                            },
                            Step::CreateVectorIndexEdges {
                                label: "FOLLOWS".to_string(),
                                property: "embedding".to_string(),
                                tenant_property: Some("tenantId".to_string()),
                            },
                            Step::CreateTextIndexNodes {
                                label: "ParityUser".to_string(),
                                property: "bio".to_string(),
                                tenant_property: Some("tenantId".to_string()),
                            },
                            Step::CreateTextIndexEdges {
                                label: "FOLLOWS".to_string(),
                                property: "note".to_string(),
                                tenant_property: Some("tenantId".to_string()),
                            },
                        ]),
                    )
                    .var_as(
                        "raw_mutations",
                        Traversal::<OnNodes, WriteEnabled>::from_steps(vec![
                            Step::AddN {
                                label: "RawNode".to_string(),
                                properties: vec![("name".to_string(), PropertyInput::from("raw"))],
                            },
                            Step::AddE {
                                label: "RAW_EDGE".to_string(),
                                to: NodeRef::Var("raw_mutations".to_string()),
                                properties: vec![("weight".to_string(), PropertyInput::from(1i64))],
                            },
                            Step::SetProperty(
                                "name".to_string(),
                                PropertyInput::Expr(Expr::param("name")),
                            ),
                            Step::RemoveProperty("old".to_string()),
                            Step::DropEdge(NodeRef::Ids(vec![999_999])),
                            Step::DropEdgeLabeled {
                                to: NodeRef::Ids(vec![999_999]),
                                label: "RAW_EDGE".to_string(),
                            },
                            Step::DropEdgeById(EdgeRef::Ids(vec![999_999])),
                            Step::Drop,
                        ]),
                    )
                    .returning(["raw_indexes", "raw_mutations"]),
            ),
        ),
        json_only(
            "902-dynamic-value-and-param-type-shapes",
            with_params(
                read_request(
                    read_batch()
                        .var_as("empty", g().n_with_label("Missing").count())
                        .returning(["empty"]),
                ),
                vec![
                    ("null", DynamicQueryValue::Null),
                    ("bool", DynamicQueryValue::Bool(true)),
                    ("i64", DynamicQueryValue::I64(i64::MAX)),
                    ("f64", DynamicQueryValue::F64(1.25)),
                    ("f32", DynamicQueryValue::F32(1.5)),
                    ("string", string("value")),
                    ("array", array(vec![i64_value(1), string("two")])),
                    (
                        "object",
                        object(vec![("nested", DynamicQueryValue::Bool(true))]),
                    ),
                ],
                vec![
                    ("null", QueryParamType::Value),
                    ("bool", QueryParamType::Bool),
                    ("i64", QueryParamType::I64),
                    ("f64", QueryParamType::F64),
                    ("f32", QueryParamType::F32),
                    ("string", QueryParamType::String),
                    (
                        "array",
                        QueryParamType::Array(Box::new(QueryParamType::Value)),
                    ),
                    ("object", QueryParamType::Object),
                ],
            ),
        ),
        json_only(
            "903-empty-source-vector-text-runtime-inputs",
            with_params(
                read_request(
                    read_batch()
                        .var_as(
                            "vector_nodes",
                            g().vector_search_nodes_with(
                                "ParityUser",
                                "embedding",
                                PropertyInput::param("query_vector"),
                                Expr::param("limit"),
                                Some(PropertyInput::param("tenant")),
                            ),
                        )
                        .var_as(
                            "text_nodes",
                            g().text_search_nodes_with(
                                "ParityUser",
                                "bio",
                                PropertyInput::param("query_text"),
                                Expr::param("limit"),
                                Some(PropertyInput::param("tenant")),
                            ),
                        )
                        .returning(["vector_nodes", "text_nodes"]),
                ),
                vec![
                    (
                        "query_vector",
                        array(vec![f64_value(1.0), f64_value(0.0), f64_value(0.0)]),
                    ),
                    ("query_text", string("graph")),
                    ("limit", i64_value(5)),
                    ("tenant", string("tenant-a")),
                ],
                vec![
                    (
                        "query_vector",
                        QueryParamType::Array(Box::new(QueryParamType::F64)),
                    ),
                    ("query_text", QueryParamType::String),
                    ("limit", QueryParamType::I64),
                    ("tenant", QueryParamType::String),
                ],
            ),
        ),
        json_only(
            "904-empty-query-and-node-edge-ref-shapes",
            read_request(
                read_batch()
                    .var_as(
                        "all_nodes",
                        Traversal::<OnNodes, ReadOnly>::from_steps(vec![
                            Step::N(NodeRef::All),
                            Step::Count,
                        ]),
                    )
                    .var_as(
                        "node_ids",
                        Traversal::<OnNodes, ReadOnly>::from_steps(vec![
                            Step::N(NodeRef::ids([1, 2])),
                            Step::Id,
                        ]),
                    )
                    .var_as(
                        "node_var",
                        Traversal::<OnNodes, ReadOnly>::from_steps(vec![
                            Step::N(NodeRef::Var("all_nodes".to_string())),
                            Step::Label,
                        ]),
                    )
                    .var_as(
                        "edge_ids",
                        Traversal::<helix_db::OnEdges, ReadOnly>::from_steps(vec![
                            Step::E(EdgeRef::ids([1, 2])),
                            Step::Id,
                        ]),
                    )
                    .var_as(
                        "edge_var",
                        Traversal::<helix_db::OnEdges, ReadOnly>::from_steps(vec![
                            Step::E(EdgeRef::Var("edge_ids".to_string())),
                            Step::Label,
                        ]),
                    )
                    .returning(["all_nodes", "node_ids", "node_var", "edge_ids", "edge_var"]),
            ),
        ),
        json_only(
            "905-empty-traversal-source-mutators",
            write_request(
                write_batch()
                    .var_as(
                        "inject",
                        Traversal::<Empty, ReadOnly>::new()
                            .inject("some_var")
                            .count(),
                    )
                    .var_as(
                        "drop_edge_by_id",
                        g().drop_edge_by_id(EdgeRef::id(123_456)).count(),
                    )
                    .returning(["inject", "drop_edge_by_id"]),
            ),
        ),
        json_only(
            "906-nested-dynamic-property-write-shapes",
            with_params(
                write_request(
                    write_batch()
                        .var_as(
                            "created",
                            g().add_n(
                                "ParityNested",
                                vec![
                                    ("name", PropertyInput::from("nested")),
                                    (
                                        "metadata",
                                        PropertyInput::from(nested_metadata_property(
                                            "some_id", 20,
                                        )),
                                    ),
                                ],
                            ),
                        )
                        .var_as(
                            "updated",
                            g().n(NodeRef::var("created"))
                                .set_property("metadata", PropertyInput::param("metadata"))
                                .value_map(Some(vec!["metadata.externalID"])),
                        )
                        .var_as(
                            "target",
                            g().add_n(
                                "ParityNestedTarget",
                                vec![("name", PropertyInput::from("target"))],
                            ),
                        )
                        .var_as(
                            "edge",
                            g().n(NodeRef::var("created"))
                                .add_e(
                                    "NESTED_LINK",
                                    NodeRef::var("target"),
                                    vec![(
                                        "metadata",
                                        PropertyInput::from(nested_metadata_property("edge_id", 5)),
                                    )],
                                )
                                .count(),
                        )
                        .returning(["created", "updated", "edge"]),
                ),
                vec![("metadata", nested_metadata_param("param_id", 22))],
                vec![("metadata", QueryParamType::Object)],
            ),
        ),
        json_only(
            "907-nested-dynamic-property-read-shapes",
            with_params(
                read_request(
                    read_batch()
                        .var_as(
                            "nested_users",
                            g().n_where(SourcePredicate::and(vec![
                                SourcePredicate::eq("$label", "ParityNested"),
                                SourcePredicate::eq(
                                    "metadata.externalID",
                                    Expr::param("external_id"),
                                ),
                            ]))
                            .where_(Predicate::compare(
                                Expr::prop("metadata.score"),
                                CompareOp::Gt,
                                Expr::val(10i64),
                            ))
                            .order_by_multiple(vec![
                                ("metadata.score", Order::Desc),
                                ("name", Order::Asc),
                            ])
                            .project(vec![
                                Projection::property("metadata.externalID", "external_id"),
                                Projection::expr("score_copy", Expr::prop("metadata.score")),
                            ]),
                        )
                        .var_as(
                            "nested_values",
                            g().n_with_label("ParityNested")
                                .values(vec!["metadata.externalID"]),
                        )
                        .var_as(
                            "nested_map",
                            g().n_with_label("ParityNested")
                                .value_map(Some(vec!["metadata.externalID", "metadata.score"])),
                        )
                        .var_as(
                            "nested_edges",
                            g().e_where(SourcePredicate::and(vec![
                                SourcePredicate::eq("$label", "NESTED_LINK"),
                                SourcePredicate::eq("metadata.externalID", "edge_id"),
                            ]))
                            .edge_has("metadata.externalID", PropertyInput::from("edge_id"))
                            .edge_properties(),
                        )
                        .returning([
                            "nested_users",
                            "nested_values",
                            "nested_map",
                            "nested_edges",
                        ]),
                ),
                vec![("external_id", string("param_id"))],
                vec![("external_id", QueryParamType::String)],
            ),
        ),
    ]
}
