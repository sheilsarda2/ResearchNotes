use std::time::Duration;
use zenoh::{query::{ConsolidationMode, QueryTarget, Queryable}, sample::Locality, Config, Session, Wait};

fn config(mode: &str) -> Config {
    let mut config = Config::default();
    config.insert_json5("mode", &format!("\"{mode}\"")).unwrap();
    config.insert_json5("scouting/multicast/enabled", "false").unwrap();
    config
}

async fn router() -> Session {
    let mut config = config("router");
    config.insert_json5("listen/endpoints", "[\"tcp/127.0.0.1:0\"]").unwrap();
    zenoh::open(config).await.unwrap()
}

async fn client(router: &Session) -> Session {
    let locators = router.info().locators().await;
    assert_eq!(locators.len(), 1, "fixture must have one TCP listener");
    let mut config = config("client");
    config.insert_json5("connect/endpoints", &format!("[\"{}\"]", locators[0])).unwrap();
    zenoh::open(config).await.unwrap()
}

async fn attach(session: &Session, key: &str, id: &'static str, complete: bool) -> Queryable<()> {
    session.declare_queryable(key.to_owned()).complete(complete).callback(move |query| {
        query.reply(query.key_expr().clone(), id).wait().unwrap();
    }).await.unwrap()
}

async fn replies(session: &Session, key: &str, target: QueryTarget, destination: Locality) -> Vec<String> {
    let receiver = session.get(key.to_owned()).target(target)
        .allowed_destination(destination)
        .consolidation(ConsolidationMode::None)
        .timeout(Duration::from_secs(15)).await.unwrap();
    let mut result = Vec::new();
    while let Ok(reply) = receiver.recv_async().await {
        let sample = reply.result().expect("fixture handler must reply with data");
        result.push(sample.payload().try_to_string().unwrap().into_owned());
    }
    result.sort();
    result
}

fn expected(ids: &[&str]) -> Vec<String> {
    let mut ids: Vec<_> = ids.iter().map(|id| id.to_string()).collect();
    ids.sort();
    ids
}

// All mutations finish synchronously on the router before opening the client.
// A fresh client cannot inherit stale discovery state from a previous stage.
// No sleep or elapsed-time assertion determines correctness.
async fn verify_stage(router: &Session, key: &str, all: &[&str], complete: &[&str]) {
    assert_eq!(replies(router, key, QueryTarget::All, Locality::SessionLocal).await, expected(all), "local All at {key}");
    assert_eq!(replies(router, key, QueryTarget::AllComplete, Locality::SessionLocal).await, expected(complete), "local AllComplete at {key}");
    let remote = client(router).await;
    assert_eq!(replies(&remote, key, QueryTarget::All, Locality::Remote).await, expected(all), "remote All at {key}");
    assert_eq!(replies(&remote, key, QueryTarget::AllComplete, Locality::Remote).await, expected(complete), "remote AllComplete at {key}");
    remote.close().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn homogeneous_and_empty_controls() {
    let router = router().await;
    verify_stage(&router, "control/empty", &[], &[]).await;
    let c = attach(&router, "control/single", "one", true).await;
    verify_stage(&router, "control/single", &["one"], &["one"]).await;
    let cc = attach(&router, "control/single", "two", true).await;
    verify_stage(&router, "control/single", &["one", "two"], &["one", "two"]).await;
    let i = attach(&router, "control/incomplete", "three", false).await;
    let ii = attach(&router, "control/incomplete", "four", false).await;
    verify_stage(&router, "control/incomplete", &["three", "four"], &[]).await;
    drop((c, cc, i, ii));
    router.close().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn complete_then_incomplete() {
    let router = router().await;
    let complete = attach(&router, "mixed/key", "primary", true).await;
    let incomplete = attach(&router, "mixed/key", "backup", false).await;
    verify_stage(&router, "mixed/key", &["primary", "backup"], &["primary"]).await;
    drop((complete, incomplete));
    router.close().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn incomplete_then_complete() {
    let router = router().await;
    let incomplete = attach(&router, "reverse/key", "backup", false).await;
    let complete = attach(&router, "reverse/key", "primary", true).await;
    verify_stage(&router, "reverse/key", &["primary", "backup"], &["primary"]).await;
    drop((complete, incomplete));
    router.close().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn wildcard_same_expression_and_unrelated_key() {
    let router = router().await;
    let primary = attach(&router, "wild/**", "authoritative", true).await;
    let backup = attach(&router, "wild/**", "fallback", false).await;
    let other = attach(&router, "unrelated/**", "other", true).await;
    verify_stage(&router, "wild/deep/value", &["authoritative", "fallback"], &["authoritative"]).await;
    verify_stage(&router, "unrelated/value", &["other"], &["other"]).await;
    drop((primary, backup, other));
    router.close().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn undeclare_and_redeclare_complete_queryables() {
    let router = router().await;
    let a = attach(&router, "lifecycle/key", "complete-a", true).await;
    let b = attach(&router, "lifecycle/key", "incomplete-b", false).await;
    let c = attach(&router, "lifecycle/key", "complete-c", true).await;
    verify_stage(&router, "lifecycle/key", &["complete-a", "incomplete-b", "complete-c"], &["complete-a", "complete-c"]).await;
    a.undeclare().await.unwrap();
    verify_stage(&router, "lifecycle/key", &["incomplete-b", "complete-c"], &["complete-c"]).await;
    c.undeclare().await.unwrap();
    verify_stage(&router, "lifecycle/key", &["incomplete-b"], &[]).await;
    let d = attach(&router, "lifecycle/key", "complete-d", true).await;
    verify_stage(&router, "lifecycle/key", &["incomplete-b", "complete-d"], &["complete-d"]).await;
    b.undeclare().await.unwrap();
    verify_stage(&router, "lifecycle/key", &["complete-d"], &["complete-d"]).await;
    d.undeclare().await.unwrap();
    verify_stage(&router, "lifecycle/key", &[], &[]).await;
    router.close().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn incomplete_churn_preserves_complete_siblings() {
    let router = router().await;
    let a = attach(&router, "churn/key", "complete-a", true).await;
    let b = attach(&router, "churn/key", "complete-b", true).await;
    let c = attach(&router, "churn/key", "incomplete-c", false).await;
    let d = attach(&router, "churn/key", "incomplete-d", false).await;
    verify_stage(&router, "churn/key", &["complete-a", "complete-b", "incomplete-c", "incomplete-d"], &["complete-a", "complete-b"]).await;
    c.undeclare().await.unwrap();
    verify_stage(&router, "churn/key", &["complete-a", "complete-b", "incomplete-d"], &["complete-a", "complete-b"]).await;
    b.undeclare().await.unwrap();
    verify_stage(&router, "churn/key", &["complete-a", "incomplete-d"], &["complete-a"]).await;
    d.undeclare().await.unwrap();
    verify_stage(&router, "churn/key", &["complete-a"], &["complete-a"]).await;
    a.undeclare().await.unwrap();
    verify_stage(&router, "churn/key", &[], &[]).await;
    router.close().await.unwrap();
}
