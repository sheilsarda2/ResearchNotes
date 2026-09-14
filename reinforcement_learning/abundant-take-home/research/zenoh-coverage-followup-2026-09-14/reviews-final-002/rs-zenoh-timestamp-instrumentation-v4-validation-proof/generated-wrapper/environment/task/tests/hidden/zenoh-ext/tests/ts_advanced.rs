//
// Hidden verifier test: zenoh-ext AdvancedPublisher publications must accept and
// carry timestamp instrumentation exactly like plain publications.
//
#![cfg(feature = "unstable")]

use std::time::Duration;

use zenoh::{
    sample::SampleKind,
    timestamp_stack::{InterceptionPoint, TimestampInstrumentationBuilder},
};
use zenoh_config::ModeDependentValue;
use zenoh_ext::{AdvancedPublisherBuilderExt, CacheConfig};

const TIMEOUT: Duration = Duration::from_secs(60);
const SLEEP: Duration = Duration::from_secs(1);

macro_rules! wait {
    ($e:expr) => {
        tokio::time::timeout(TIMEOUT, $e)
            .await
            .expect("operation timed out")
    };
}

fn local_config() -> zenoh::Config {
    let mut config = zenoh::Config::default();
    config.scouting.multicast.set_enabled(Some(false)).unwrap();
    // An AdvancedPublisher with a cache sequences its samples by timestamp, which requires the
    // session HLC (`timestamping` enabled), exactly as upstream zenoh-ext/tests/advanced.rs does.
    config
        .timestamping
        .set_enabled(Some(ModeDependentValue::Unique(true)))
        .unwrap();
    config
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn advanced_publisher_put_carries_timestamp_stack() {
    let ke = "test/ts_instr/advanced/put";
    let session = wait!(zenoh::open(local_config())).unwrap();
    let publisher = wait!(session
        .declare_publisher(ke)
        .cache(CacheConfig::default().max_samples(3)))
    .unwrap();
    let subscriber = wait!(session.declare_subscriber(ke)).unwrap();
    tokio::time::sleep(SLEEP).await;

    let instr = TimestampInstrumentationBuilder::new()
        .set_send(true)
        .set_receive(true)
        .build()
        .unwrap();
    wait!(publisher.put("payload").timestamp_instrumentation(instr)).unwrap();

    let sample = wait!(subscriber.recv_async()).unwrap();
    assert_eq!(sample.kind(), SampleKind::Put);
    let stack = sample
        .timestamp_stack()
        .expect("advanced publication must carry the timestamp stack");
    let cfg = stack.instrumentation();
    assert!(cfg.is_instrumented(InterceptionPoint::Send));
    assert!(cfg.is_instrumented(InterceptionPoint::Receive));
    assert!(!cfg.is_instrumented(InterceptionPoint::Route));
    let points: Vec<InterceptionPoint> = stack.records().iter().map(|r| r.point()).collect();
    assert_eq!(
        points,
        vec![InterceptionPoint::Send, InterceptionPoint::Receive],
        "same-session delivery records SEND then exactly one RECEIVE"
    );
    assert!(stack.records().iter().all(|r| !r.is_custom()));

    wait!(session.close()).unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn advanced_publisher_put_without_instrumentation_has_no_stack() {
    let ke = "test/ts_instr/advanced/no_instr";
    let session = wait!(zenoh::open(local_config())).unwrap();
    let publisher = wait!(session
        .declare_publisher(ke)
        .cache(CacheConfig::default().max_samples(3)))
    .unwrap();
    let subscriber = wait!(session.declare_subscriber(ke)).unwrap();
    tokio::time::sleep(SLEEP).await;

    wait!(publisher.put("payload")).unwrap();
    let sample = wait!(subscriber.recv_async()).unwrap();
    assert!(
        sample.timestamp_stack().is_none(),
        "no instrumentation requested: no stack must be attached"
    );

    wait!(session.close()).unwrap();
}
