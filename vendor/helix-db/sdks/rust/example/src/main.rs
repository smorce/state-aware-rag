use helix_db::dsl::prelude::*;

#[register]
fn query1(params: String) -> helix_db::ReadBatch {
    // helix_db query that returns a read query or write query
    read_batch()
        .var_as(
            "user",
            g().n_where(SourcePredicate::eq("username", params)),
        )
        .var_as(
            "friends",
            g().n(NodeRef::var("user"))
                .out(Some("FOLLOWS"))
                .dedup()
                .limit(100),
        )
        .returning(["user", "friends"])
}

fn main() {
    let _ = helix_db::generate().expect("should work");
    let _query = query1("alice".to_string());
}
