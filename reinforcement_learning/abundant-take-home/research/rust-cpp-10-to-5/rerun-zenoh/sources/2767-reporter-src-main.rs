use std::time::{Duration, Instant};

use zenoh::{Config, Session, query::Querier};

const KEY: &str = "repro/service/rpc";

fn client_config() -> Config {
    let mut config = Config::default();
    config.insert_json5("mode", "\"client\"").unwrap();
    config
        .insert_json5(
            "connect/endpoints",
            r#"["tcp/127.0.0.1:7447"]"#,
        )
        .unwrap();
    config
        .insert_json5("scouting/multicast/enabled", "false")
        .unwrap();
    config
}

async fn open() -> Session {
    zenoh::open(client_config()).await.unwrap()
}

async fn responder() {
    let session = open().await;
    let queryable = session
        .declare_queryable(KEY)
        .callback(|query| {
            tokio::spawn(async move {
                query.reply(KEY, "pong").await.unwrap();
            });
        })
        .await
        .unwrap();

    println!("responder: connected for four seconds");
    tokio::time::sleep(Duration::from_secs(4)).await;

    drop(queryable);
    session.close().await.unwrap();
    println!("responder: disconnected for two seconds");

    tokio::time::sleep(Duration::from_secs(2)).await;

    let session = open().await;
    let _queryable = session
        .declare_queryable(KEY)
        .callback(|query| {
            tokio::spawn(async move {
                query.reply(KEY, "pong").await.unwrap();
            });
        })
        .await
        .unwrap();

    println!("responder: reconnected");
    std::future::pending::<()>().await;
}

async fn get_once(label: &str, querier: &Querier<'_>) {
    let started = Instant::now();
    let replies = querier
        .get()
        .payload("ping")
        .await
        .expect("get should start");

    match replies.recv_async().await {
        Ok(reply) => match reply.result() {
            Ok(sample) => println!(
                "{label}: reply={} after {:?}",
                sample.payload().try_to_string().unwrap(),
                started.elapsed()
            ),
            Err(error) => println!(
                "{label}: ReplyError={error:?} after {:?}",
                started.elapsed()
            ),
        },
        Err(error) => println!(
            "{label}: ZERO REPLIES ({error:?}) after {:?}",
            started.elapsed()
        ),
    }
}

async fn requester() {
    tokio::time::sleep(Duration::from_secs(2)).await;

    let session = open().await;
    let querier = session
        .declare_querier(KEY)
        .timeout(Duration::from_secs(10))
        .await
        .unwrap();
    get_once("before disconnect", &querier).await;
    drop(querier);

    // The responder reconnects at t=6; wait until t=9 so its declaration has
    // reached the router before declaring the next querier.
    tokio::time::sleep(Duration::from_secs(7)).await;

    let querier = session
        .declare_querier(KEY)
        .timeout(Duration::from_secs(10))
        .await
        .unwrap();
    get_once("immediately after reconnect", &querier).await;

    // Keep this querier alive while its CurrentFuture interest refreshes.
    tokio::time::sleep(Duration::from_secs(1)).await;
    get_once("one second later", &querier).await;
}

#[tokio::main]
async fn main() {
    match std::env::args().nth(1).as_deref() {
        Some("responder") => responder().await,
        Some("requester") => requester().await,
        _ => eprintln!("usage: cargo run -- responder|requester"),
    }
}
