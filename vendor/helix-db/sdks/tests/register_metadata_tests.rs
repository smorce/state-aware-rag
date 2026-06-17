#![allow(unused_variables)]

use std::collections::BTreeMap;

use helix_dsl::prelude::*;
use helix_dsl::{
    build_query_bundle, deserialize_query_bundle, serialize_query_bundle, BatchQuery,
    DynamicQueryError, DynamicQueryRequestType, DynamicQueryValue, QueryParamType, QueryParameter,
};

#[register]
pub fn register_metadata_read(tenant_id: String, limit: i64) -> ReadBatch {
    let _ = &tenant_id;

    read_batch()
        .var_as(
            "users",
            g().n_with_label("User")
                .where_(Predicate::eq_param("tenantId", "tenant_id"))
                .limit(limit)
                .value_map(Some(vec!["$id", "name", "tenantId"])),
        )
        .returning(["users"])
}

#[register]
fn register_metadata_read_array(statuses: Vec<String>) -> ReadBatch {
    read_batch().var_as(
        "users",
        g().n_with_label("User")
            .where_(Predicate::is_in_param("status", "statuses")),
    )
}

#[register]
pub fn register_metadata_write(
    data: Vec<helix_dsl::ParamObject>,
    embeddings: Vec<Vec<f64>>,
) -> WriteBatch {
    let _ = (&data, &embeddings);

    let body = write_batch().var_as(
        "created",
        g().add_n(
            "User",
            vec![
                ("externalId", PropertyInput::param("externalId")),
                ("embedding", PropertyInput::param("embedding")),
            ],
        ),
    );

    write_batch()
        .for_each_param("data", body)
        .returning(["created"])
}

#[register]
pub fn register_metadata_bytes(bytes: Vec<u8>) -> ReadBatch {
    let _ = &bytes;
    read_batch()
}

#[register]
pub fn register_metadata_datetime(created_after: DateTime) -> ReadBatch {
    let _ = &created_after;
    read_batch().var_as(
        "recent_users",
        g().n_with_label("User")
            .where_(Predicate::gte_param("created_at", "created_after"))
            .value_map(Some(vec!["$id", "created_at"])),
    )
}

#[test]
fn registered_queries_record_parameter_shapes() {
    let bundle = build_query_bundle().expect("build query bundle");

    assert!(bundle.read_routes.contains_key("register_metadata_read"));
    assert!(bundle.write_routes.contains_key("register_metadata_write"));
    assert!(!bundle
        .read_routes
        .contains_key("register_metadata_read_decomposed"));
    assert!(!bundle
        .write_routes
        .contains_key("register_metadata_write_decomposed"));

    assert_eq!(
        bundle.read_parameters.get("register_metadata_read"),
        Some(&vec![
            QueryParameter {
                name: "tenant_id".to_string(),
                ty: QueryParamType::String,
            },
            QueryParameter {
                name: "limit".to_string(),
                ty: QueryParamType::I64,
            },
        ])
    );

    assert_eq!(
        bundle.read_parameters.get("register_metadata_read_array"),
        Some(&vec![QueryParameter {
            name: "statuses".to_string(),
            ty: QueryParamType::Array(Box::new(QueryParamType::String)),
        }])
    );

    assert_eq!(
        bundle.read_parameters.get("register_metadata_datetime"),
        Some(&vec![QueryParameter {
            name: "created_after".to_string(),
            ty: QueryParamType::DateTime,
        }])
    );

    assert_eq!(
        bundle.write_parameters.get("register_metadata_write"),
        Some(&vec![
            QueryParameter {
                name: "data".to_string(),
                ty: QueryParamType::Array(Box::new(QueryParamType::Object)),
            },
            QueryParameter {
                name: "embeddings".to_string(),
                ty: QueryParamType::Array(Box::new(QueryParamType::Array(Box::new(
                    QueryParamType::F64,
                )))),
            },
        ])
    );
}

#[test]
fn registered_parameter_shapes_roundtrip_in_bundle() {
    let bundle = build_query_bundle().expect("build query bundle");
    let bytes = serialize_query_bundle(&bundle).expect("serialize query bundle");
    let decoded = deserialize_query_bundle(&bytes).expect("deserialize query bundle");

    assert_eq!(
        decoded.write_parameters.get("register_metadata_write"),
        bundle.write_parameters.get("register_metadata_write")
    );
    assert_eq!(
        decoded.read_parameters.get("register_metadata_read"),
        bundle.read_parameters.get("register_metadata_read")
    );
}

#[test]
fn public_read_queries_generate_callable_dynamic_requests() {
    let request =
        register_metadata_read("acme".to_string(), 25).expect("build dynamic read request");

    assert_eq!(request.request_type, DynamicQueryRequestType::Read);
    match &request.query {
        BatchQuery::Read(batch) => assert_eq!(batch.returns, vec!["users"]),
        other => panic!("expected read batch, got {other:?}"),
    }

    let parameters = request
        .parameters
        .as_ref()
        .expect("parameters should exist");
    assert_eq!(
        parameters.get("tenant_id"),
        Some(&DynamicQueryValue::String("acme".to_string()))
    );
    assert_eq!(parameters.get("limit"), Some(&DynamicQueryValue::I64(25)));

    let parameter_types = request
        .parameter_types
        .as_ref()
        .expect("parameter types should exist");
    assert_eq!(
        parameter_types.get("tenant_id"),
        Some(&QueryParamType::String)
    );
    assert_eq!(parameter_types.get("limit"), Some(&QueryParamType::I64));

    let json = request.to_json_string().expect("serialize request json");
    assert!(json.contains(r#""request_type":"read""#));
    assert!(json.contains(r#""parameters":{"limit":25,"tenant_id":"acme"}"#));
    assert!(json.contains(r#""parameter_types":{"limit":"I64","tenant_id":"String"}"#));
}

#[test]
fn public_write_queries_generate_callable_dynamic_requests() {
    let mut row = BTreeMap::new();
    row.insert("externalId".to_string(), PropertyValue::from("u-1"));
    row.insert(
        "embedding".to_string(),
        PropertyValue::from(vec![0.1f64, 0.2f64]),
    );

    let request = register_metadata_write(vec![row], vec![vec![0.3, 0.4]])
        .expect("build dynamic write request");

    assert_eq!(request.request_type, DynamicQueryRequestType::Write);
    match &request.query {
        BatchQuery::Write(batch) => assert_eq!(batch.returns, vec!["created"]),
        other => panic!("expected write batch, got {other:?}"),
    }

    let parameters = request
        .parameters
        .as_ref()
        .expect("parameters should exist");
    assert!(matches!(
        parameters.get("data"),
        Some(DynamicQueryValue::Array(rows))
            if matches!(rows.first(), Some(DynamicQueryValue::Object(fields))
                if fields.get("externalId") == Some(&DynamicQueryValue::String("u-1".to_string()))
                && fields.get("embedding")
                    == Some(&DynamicQueryValue::Array(vec![
                        DynamicQueryValue::F64(0.1),
                        DynamicQueryValue::F64(0.2),
                    ])))
    ));
    assert_eq!(
        parameters.get("embeddings"),
        Some(&DynamicQueryValue::Array(vec![DynamicQueryValue::Array(
            vec![DynamicQueryValue::F64(0.3), DynamicQueryValue::F64(0.4),]
        )]))
    );

    let parameter_types = request
        .parameter_types
        .as_ref()
        .expect("parameter types should exist");
    assert_eq!(
        parameter_types.get("data"),
        Some(&QueryParamType::Array(Box::new(QueryParamType::Object)))
    );
    assert_eq!(
        parameter_types.get("embeddings"),
        Some(&QueryParamType::Array(Box::new(QueryParamType::Array(
            Box::new(QueryParamType::F64,)
        ))))
    );

    let json = request.to_json_string().expect("serialize request json");
    assert!(json.contains(r#""request_type":"write""#));
    assert!(json.contains(r#""embeddings":[[0.3,0.4]]"#));
}

#[test]
fn public_datetime_queries_generate_rfc3339_utc_parameters() {
    let request = register_metadata_datetime(
        DateTime::parse_rfc3339("2026-04-05T12:34:56.789+02:00").expect("parse datetime"),
    )
    .expect("build dynamic read request");

    let parameters = request
        .parameters
        .as_ref()
        .expect("parameters should exist");
    assert_eq!(
        parameters.get("created_after"),
        Some(&DynamicQueryValue::String(
            "2026-04-05T10:34:56.789Z".to_string()
        ))
    );
    assert_eq!(
        request
            .parameter_types
            .as_ref()
            .expect("parameter types should exist")
            .get("created_after"),
        Some(&QueryParamType::DateTime)
    );

    let json = request.to_json_string().expect("serialize request json");
    assert!(json.contains(r#""parameter_types":{"created_after":"DateTime"}"#));
}

#[test]
fn bytes_parameters_fail_for_dynamic_request_helpers() {
    let err = register_metadata_bytes(vec![1, 2, 3]).expect_err("bytes should fail");

    assert!(matches!(
        err,
        DynamicQueryError::UnsupportedBytesParameter(path) if path == "bytes"
    ));
}
