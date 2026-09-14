//! Contract 7: the admin-space delivery endpoint participates in Receive instrumentation.
//! This independent follow-up test uses only the documented public timestamp API.

#![cfg(feature = "unstable")]

use std::{
    sync::{Arc, Mutex},
    time::Duration,
};

use zenoh::{
    config::WhatAmI,
    timestamp_stack::{TimestampContext, TimestampInstrumentationBuilder},
};

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn adminspace_query_invokes_receive_timestamp_callback() {
    tokio::time::timeout(Duration::from_secs(60), async {
        let observed = Arc::new(Mutex::new(Vec::<TimestampContext>::new()));
        let callback_observed = observed.clone();

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
            .with_timestamp_callback(move |context| {
                callback_observed.lock().unwrap().push(context);
                // A nonempty timestamp opts into recording at the enabled point.
                b"admin-receive-probe".to_vec()
            })
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
        client_config.connect.endpoints.set(vec![endpoint]).unwrap();
        client_config
            .scouting
            .multicast
            .set_enabled(Some(false))
            .unwrap();
        client_config.adminspace.set_enabled(false).unwrap();
        let client = zenoh::open(client_config).await.unwrap();
        let admin_key = format!("@/{}/router", router.zid());

        // Public admin-space behavior from pristine zenoh/tests/adminspace.rs.
        // This also establishes readiness without fixed-port or sleep assumptions.
        let plain_replies = client.get(admin_key.clone()).await.unwrap();
        let plain_reply = plain_replies.recv_async().await.unwrap();
        let plain_sample = plain_reply
            .result()
            .expect("admin read must return a sample");
        assert_eq!(plain_sample.key_expr().as_str(), admin_key.as_str());
        assert!(plain_sample.timestamp_stack().is_none());
        assert!(
            observed.lock().unwrap().is_empty(),
            "uninstrumented admin read must not request timestamps"
        );

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

        {
            let contexts = observed.lock().unwrap();
            assert_eq!(
                contexts.len(),
                1,
                "Receive-only admin delivery must invoke the router timestamp callback exactly once"
            );
            assert_eq!(contexts[0].zid, router.zid());
            assert_eq!(contexts[0].whatami, WhatAmI::Router);
        }

        client.close().await.unwrap();
        router.close().await.unwrap();
    })
    .await
    .expect("admin Receive test exceeded its 60-second bound");
}
