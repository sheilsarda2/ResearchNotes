//! Exploratory follow-up for contracts 3 and 7: an admin reply inherits its query stack.
//! Gold/reference failure must be reported, not worked around in this public-API test.

#![cfg(feature = "unstable")]

use std::time::Duration;

use zenoh::{
    config::WhatAmI,
    timestamp_stack::{
        InstrumentationTimestamp, InterceptionPoint, TimestampInstrumentationBuilder,
    },
};

const ROUTER_TIMESTAMP: &[u8] = b"received-by-admin-router";
const CLIENT_TIMESTAMP: &[u8] = b"received-by-querying-client";

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn adminspace_reply_preserves_received_query_stack() {
    tokio::time::timeout(Duration::from_secs(60), async {
        let mut router_config = zenoh::Config::default();
        router_config.set_mode(Some(WhatAmI::Router)).unwrap();
        router_config
            .listen
            .endpoints
            .set(vec!["tcp/127.0.0.1:0".parse().unwrap()])
            .unwrap();
        router_config
            .scouting
            .multicast
            .set_enabled(Some(false))
            .unwrap();
        router_config.adminspace.set_enabled(true).unwrap();
        router_config.adminspace.permissions.set_read(true).unwrap();
        router_config.adminspace.permissions.set_write(false).unwrap();
        let router = zenoh::open(router_config)
            .with_timestamp_callback(|_| ROUTER_TIMESTAMP.to_vec())
            .await
            .unwrap();

        let endpoint = router
            .info()
            .locators()
            .await
            .into_iter()
            .find(|locator| locator.to_string().starts_with("tcp/"))
            .expect("router must expose its loopback TCP listener")
            .to_endpoint();
        let mut client_config = zenoh::Config::default();
        client_config.set_mode(Some(WhatAmI::Client)).unwrap();
        client_config.listen.endpoints.set(vec![]).unwrap();
        client_config.connect.endpoints.set(vec![zenoh::config::EndPoints::Single(endpoint)]).unwrap();
        client_config
            .scouting
            .multicast
            .set_enabled(Some(false))
            .unwrap();
        client_config.adminspace.set_enabled(false).unwrap();
        let client = zenoh::open(client_config)
            .with_timestamp_callback(|_| CLIENT_TIMESTAMP.to_vec())
            .await
            .unwrap();
        let admin_key = format!("@/{}/router", router.zid());

        // Establish the existing admin read behavior without a timing sleep.
        let plain_replies = client.get(admin_key.clone()).await.unwrap();
        let plain_reply = plain_replies.recv_async().await.unwrap();
        let plain_sample = plain_reply
            .result()
            .expect("uninstrumented admin read must return a sample");
        assert_eq!(plain_sample.key_expr().as_str(), admin_key.as_str());
        assert!(plain_sample.timestamp_stack().is_none());

        // No Send or Route record may stand in for either Receive record.
        let receive_only = TimestampInstrumentationBuilder::new()
            .set_send(false)
            .set_route(false)
            .set_receive(true)
            .build()
            .unwrap();
        let replies = client
            .get(admin_key.clone())
            .timestamp_instrumentation(receive_only)
            .await
            .unwrap();
        let reply = replies.recv_async().await.unwrap();
        let sample = reply
            .result()
            .expect("instrumented admin read must return a sample");
        assert_eq!(sample.key_expr().as_str(), admin_key.as_str());
        let payload: serde_json::Value =
            serde_json::from_slice(&sample.payload().to_bytes()).unwrap();
        assert_eq!(
            payload["zid"].as_str(),
            Some(router.zid().to_string().as_str())
        );

        let stack = sample.timestamp_stack().expect(
            "admin reply must inherit the instrumented query stack as received by the admin handler",
        );
        assert_eq!(stack.instrumentation(), receive_only);
        assert_eq!(
            stack.records().len(),
            2,
            "reply must preserve admin Receive and append the querying client's Receive exactly once"
        );
        for (record, expected_timestamp) in stack
            .records()
            .iter()
            .zip([ROUTER_TIMESTAMP, CLIENT_TIMESTAMP])
        {
            assert_eq!(record.point(), InterceptionPoint::Receive);
            assert!(record.is_custom());
            assert_eq!(
                record.timestamp(),
                &InstrumentationTimestamp::Custom(expected_timestamp.to_vec())
            );
        }

        client.close().await.unwrap();
        router.close().await.unwrap();
    })
    .await
    .expect("admin returned-stack test exceeded its 60-second bound");
}
