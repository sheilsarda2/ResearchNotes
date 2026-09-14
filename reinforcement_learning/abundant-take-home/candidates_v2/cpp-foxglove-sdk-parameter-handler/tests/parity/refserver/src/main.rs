//! Parity reference server.
//!
//! The Rust SDK's own `WebSocketServer` running the same scripted parameter store as the C and
//! C++ parity drivers. The verifier drives all three with one scripted ws-protocol client and
//! compares the normalized transcripts; this binary defines the expected wire behavior.
//!
//! Protocol with the harness: `--scenario <handler|precedence|legacy>`; prints `PORT=<n>` on
//! stdout once the server is listening; stops when stdin reaches EOF.

use std::collections::BTreeMap;
use std::io::{BufRead, Write};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use foxglove::websocket::{
    AnyClient, Capability, Client, GetParametersResponder, Parameter, ParameterHandler,
    ServerListener, SetParametersResponder,
};
use foxglove::{WebSocketServer, WebSocketServerHandle};

type Store = BTreeMap<String, Parameter>;

fn initial_store() -> Store {
    [
        Parameter::float64("foo", 1.5),
        Parameter::string("bar", "BAR"),
        Parameter::bool("flag", true),
        Parameter::integer("count", 7),
        Parameter::float64_array("arr", [1.0, 2.0, 3.0]),
        Parameter::integer_array("ints", [1, 2]),
        Parameter::byte_array("blob", &[1u8, 2, 3]),
        Parameter::string("ro_locked", "locked"),
        Parameter::empty("unset_param"),
    ]
    .into_iter()
    .map(|p| (p.name.clone(), p))
    .collect()
}

struct Shared {
    store: Mutex<Store>,
    server: Mutex<Option<WebSocketServerHandle>>,
}

impl Shared {
    /// Empty `names` means every parameter, in byte-wise name order.
    fn get_values(&self, names: &[String]) -> Vec<Parameter> {
        let store = self.store.lock().unwrap();
        if names.is_empty() {
            store.values().cloned().collect()
        } else {
            names.iter().filter_map(|n| store.get(n).cloned()).collect()
        }
    }

    /// Applies a set request. Returns `(result, applied)`: `result` is echoed to the requester
    /// (read-only `ro_*` names echo their unchanged stored value), `applied` is what changed.
    fn apply_set(&self, params: Vec<Parameter>) -> (Vec<Parameter>, Vec<Parameter>) {
        let mut store = self.store.lock().unwrap();
        let mut result = Vec::new();
        let mut applied = Vec::new();
        for p in params {
            if p.name.starts_with("ro_") {
                if let Some(existing) = store.get(&p.name) {
                    result.push(existing.clone());
                }
                continue;
            }
            store.insert(p.name.clone(), p.clone());
            result.push(p.clone());
            applied.push(p);
        }
        (result, applied)
    }

    fn publish(&self, params: Vec<Parameter>) {
        if params.is_empty() {
            return;
        }
        if let Some(handle) = self.server.lock().unwrap().as_ref() {
            handle.publish_parameter_values(params);
        }
    }
}

/// The scripted handler. Special names: `drop`/`throw` drop the responder without responding,
/// `slow` completes the request from another thread after a delay.
struct ScriptedHandler(Arc<Shared>);

impl ParameterHandler for ScriptedHandler {
    fn get(
        &self,
        _client: AnyClient,
        names: Vec<String>,
        _request_id: Option<String>,
        responder: GetParametersResponder,
    ) {
        if names.iter().any(|n| n == "drop" || n == "throw") {
            drop(responder);
            return;
        }
        if names.iter().any(|n| n == "slow") {
            let shared = self.0.clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_millis(100));
                responder.respond(shared.get_values(&["foo".to_string()]));
            });
            return;
        }
        responder.respond(self.0.get_values(&names));
    }

    fn set(
        &self,
        _client: AnyClient,
        parameters: Vec<Parameter>,
        _request_id: Option<String>,
        responder: SetParametersResponder,
    ) {
        if parameters.iter().any(|p| p.name == "drop") {
            drop(responder);
            return;
        }
        let (result, applied) = self.0.apply_set(parameters);
        responder.respond(result);
        self.0.publish(applied);
    }
}

/// Legacy listener. With `sentinel`, it returns a marker that must never reach the wire because
/// a registered handler takes precedence. Without it, it implements the store through the legacy
/// path (which echoes and publishes the returned values itself).
struct LegacyListener {
    shared: Arc<Shared>,
    sentinel: bool,
}

impl ServerListener for LegacyListener {
    fn on_get_parameters(
        &self,
        _client: Client,
        names: Vec<String>,
        _request_id: Option<&str>,
    ) -> Vec<Parameter> {
        if self.sentinel {
            return vec![Parameter::bool("legacy", true)];
        }
        self.shared.get_values(&names)
    }

    fn on_set_parameters(
        &self,
        _client: Client,
        parameters: Vec<Parameter>,
        _request_id: Option<&str>,
    ) -> Vec<Parameter> {
        if self.sentinel {
            return vec![Parameter::bool("legacy", true)];
        }
        let (result, _applied) = self.shared.apply_set(parameters);
        result
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let scenario = args
        .iter()
        .position(|a| a == "--scenario")
        .and_then(|i| args.get(i + 1))
        .cloned()
        .unwrap_or_else(|| "handler".to_string());

    let shared = Arc::new(Shared {
        store: Mutex::new(initial_store()),
        server: Mutex::new(None),
    });

    let mut builder = WebSocketServer::new()
        .name("parity-server")
        .bind("127.0.0.1", 0);
    match scenario.as_str() {
        "handler" => {
            builder = builder.parameter_handler(Arc::new(ScriptedHandler(shared.clone())));
        }
        "precedence" => {
            builder = builder
                .capabilities([Capability::Parameters])
                .listener(Arc::new(LegacyListener {
                    shared: shared.clone(),
                    sentinel: true,
                }))
                .parameter_handler(Arc::new(ScriptedHandler(shared.clone())));
        }
        "legacy" => {
            builder = builder
                .capabilities([Capability::Parameters])
                .listener(Arc::new(LegacyListener {
                    shared: shared.clone(),
                    sentinel: false,
                }));
        }
        other => {
            eprintln!("unknown scenario: {other}");
            std::process::exit(2);
        }
    }

    let handle = builder.start_blocking().expect("failed to start server");
    let port = handle.port();
    *shared.server.lock().unwrap() = Some(handle);
    println!("PORT={port}");
    std::io::stdout().flush().unwrap();

    let stdin = std::io::stdin();
    let mut line = String::new();
    while stdin.lock().read_line(&mut line).map(|n| n > 0).unwrap_or(false) {
        line.clear();
    }

    if let Some(handle) = shared.server.lock().unwrap().take() {
        handle.stop().wait_blocking();
    }
}
