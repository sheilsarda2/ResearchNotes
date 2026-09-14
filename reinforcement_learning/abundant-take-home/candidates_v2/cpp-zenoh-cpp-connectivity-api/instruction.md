# zenoh-cpp: expose the session connectivity API (transports, links, connectivity events)

You are working in `/workspace/repo`, a checkout of **eclipse-zenoh/zenoh-cpp**, the header-only C++17
binding over the C libraries `zenoh-c` (Rust core) and `zenoh-pico` (embedded C). The two C
libraries have just gained a *connectivity API* that reports the session's transports and links and
streams connectivity events. zenoh-cpp does not wrap it yet. Your job is to add the C++ API
described below to `include/`, with parity to the Rust and Python bindings, so that both backends
build, all existing tests keep passing, and a session's connectivity report matches what
zenoh-python reports for the same network.

The public C++ API of zenoh-cpp is what its users compile against, so names, signatures, ownership
and error behaviour below are the contract. How you organise the headers is up to you, as long as
`#include "zenoh.hxx"` makes everything available.

## Environment

* Sources: `include/` (zenoh-cpp headers, **the only directory that is collected and graded**),
  `tests/`, `examples/`, `docs/`, and the vendored dependency sources `zenoh-c/` (commit
  `8fd05be3`) and `zenoh-pico/` (commit `b8f78562`) as plain directories. The repo is a git
  repository with one baseline commit, so `git diff` shows your work.
* Both dependencies are already built and installed under `/opt/zenoh` (headers, libraries, CMake
  packages), with the **unstable API and shared memory enabled** (`Z_FEATURE_UNSTABLE_API`,
  `Z_FEATURE_SHARED_MEMORY` are defined for the zenoh-c backend; zenoh-pico is built with
  `Z_FEATURE_UNSTABLE_API=1`, connectivity, batching, advanced pub/sub, matching, local
  subscriber/queryable). Do not run `scripts/install_from_git.sh`; it expects git submodules.
* The C API you must wrap is declared and documented in `zenoh-c/include/zenoh_commons.h`
  (search for `z_info_transports`, `z_info_links`, `z_link_`, `z_transport_`,
  `z_declare_transport_events_listener`, `z_declare_link_events_listener`, the
  `z_owned_closure_transport_t` / `..._link_t` / `..._transport_event_t` / `..._link_event_t`
  closure types and the `z_*_events_listener_options_t` structs) and, for zenoh-pico, in
  `zenoh-pico/include/zenoh-pico/api/primitives.h` and `api/types.h`. The Rust implementation
  these wrap (`zenoh/src/api/info.rs`, builders under `zenoh/src/api/builders/`) is in the cargo
  git checkout under `/usr/local/cargo/git/checkouts/` if you want the reference semantics.
* Build directory `/workspace/repo/build` is configured for both backends
  (`-DZENOHCXX_ZENOHC=ON -DZENOHCXX_ZENOHPICO=ON`, Debug) and the upstream test binaries are
  prebuilt. Rebuild with `cmake --build build --target tests -j4`; run with
  `cd build && ctest --output-on-failure` (or run a single binary from `build/tests/`).
  `cmake --build build --target examples` also works. There is no network access; everything
  needed is in the image (`cargo` is set to offline mode).
* `zenohd` 1.8.0 is installed at `/opt/zenoh/bin/zenohd`. Upstream's network tests use peer
  discovery between two sessions in one process; the grader instead runs every upstream network
  test as **clients of a local router** (`zenohd -l tcp/127.0.0.1:27447 --no-multicast-scouting`
  with gossip disabled, sessions in `client` mode connecting to that locator with scouting off).
  You can reproduce that setup with the same commands. Fixed ports `17447`-`17457` are used by
  the connectivity tests and `27447` by the router; keep them free.
* Reference tooling you do **not** have: the grader's zenoh-python installation. There is no
  Python zenoh binding in this image and you do not need one.

## What must be added (all in namespace `zenoh`, available only when `Z_FEATURE_UNSTABLE_API` is defined)

Every new type below is an `Owned<...>` wrapper of the corresponding zenoh-c owned type, following the
conventions of the existing headers (`Owned`, `interop::as_loaned_c_ptr`, `into_copyable_cpp_obj`,
`as_owned_cpp_ref`, `__ZENOH_RESULT_CHECK`, `detail::closures`). Mark each new class and method with
the usual `@warning This API has been marked as unstable ...` Doxygen note and document it in the
style of the neighbouring headers.

### `Transport` (`Owned<::z_owned_transport_t>`): a connection to a remote zenoh node

| Method | Returns | Meaning (Rust: `zenoh::session::Transport`) |
|---|---|---|
| `Id get_zid() const` | | ZenohId of the remote node |
| `WhatAmI get_whatami() const` | | type of the remote node (router, peer or client) |
| `bool is_qos() const` | | whether the transport supports QoS |
| `bool is_multicast() const` | | whether the transport is multicast |
| `bool is_shm() const` | only when `Z_FEATURE_SHARED_MEMORY` is defined | whether the transport supports shared memory |

`Transport` is **copyable and movable** (copy uses the C clone function); copies are independent
owned objects.

### `Link` (`Owned<::z_owned_link_t>`): one data link of a transport over a concrete protocol

| Method | Returns | Meaning (Rust: `zenoh::session::Link`) |
|---|---|---|
| `Id get_zid() const` | | ZenohId of the transport this link belongs to |
| `std::string get_src() const` | | source locator, the local endpoint, e.g. `tcp/127.0.0.1:53422` |
| `std::string get_dst() const` | | destination locator, the remote endpoint |
| `std::optional<std::string> get_group() const` | | multicast group locator; `std::nullopt` for unicast links |
| `std::optional<std::string> get_auth_identifier() const` | | authentication identifier if any, else `std::nullopt` |
| `uint16_t get_mtu() const` | | maximum transmission unit in bytes |
| `bool is_streamed() const` | | streamed protocol (TCP) vs datagram (UDP) |
| `std::vector<std::string> get_interfaces() const` | | network interface names used by the link |
| `std::optional<std::pair<uint8_t, uint8_t>> get_priorities() const` | | `(min, max)` priority range; `std::nullopt` when the transport does not support QoS |
| `std::optional<Reliability> get_reliability() const` | | reliability level; `std::nullopt` when the transport does not support QoS |

The C API reports absent `group` / `auth_identifier` as an empty (null) string and absent
priorities / reliability through a `false` return: map those to `std::nullopt`. `Link` is copyable
and movable like `Transport`.

### `TransportEvent` (`Owned<::z_owned_transport_event_t>`) and `LinkEvent` (`Owned<::z_owned_link_event_t>`)

* `SampleKind get_kind() const`: `Z_SAMPLE_KIND_PUT` when a transport was opened / a link was
  added, `Z_SAMPLE_KIND_DELETE` when it was closed / removed.
* `const Transport& get_transport() const` (on `TransportEvent`), `const Link& get_link() const`
  (on `LinkEvent`): a reference to the wrapped object, valid as long as the event is.

Events are plain `Owned<>` wrappers (movable, released through the C `drop`). zenoh-c at the
vendored revision ships no fifo/ring channel primitives for transport or link events, so
`channels::FifoChannel` / `channels::RingChannel` cannot be instantiated with `TransportEvent` /
`LinkEvent`; the channel-variant `declare_*` templates listed below must still be declared with the
shown signatures, following the `declare_subscriber(Channel, ...)` pattern, but the grader exercises
listeners only through their callback and background forms and through the
`Listener<Handler>(Listener<void>&&, Handler)` constructor.

### `TransportEventsListener<Handler>` and `LinkEventsListener<Handler>`

Model these on `Subscriber<Handler>` / `MatchingListener<Handler>`:

* `Listener<void>` is the callback-only form: `void undeclare(ZResult* err = nullptr) &&`
  undeclares the listener (no more callbacks after it returns).
* `Listener<Handler>` (Handler not `void`) is constructed from `Listener<void>&&` plus a handler,
  exposes `const Handler& handler() const`, and `Handler undeclare(ZResult* err = nullptr) &&`
  undeclares and returns the handler.
* Provide the `interop` overloads that the other handler-bearing entities have:
  `as_owned_c_ptr`, `as_moved_c_ptr`, `move_to_c_obj`, and in particular
  `as_moved_c_ptr(std::optional<Listener<Handler>>&)`, which must return a pair of **null**
  pointers for an empty optional and the moved pointers of the listener and of its handler for a
  populated one.
* Destroying a listener object undeclares it, as for subscribers.

### `Session` additions (all `const` member functions)

```cpp
std::vector<Transport> get_transports(ZResult* err = nullptr) const;
std::vector<Link>      get_links(std::optional<Transport> transport = {}, ZResult* err = nullptr) const;

struct TransportEventsListenerOptions { bool history = false;
                                        static TransportEventsListenerOptions create_default(); };
struct LinkEventsListenerOptions      { bool history = false; std::optional<Transport> transport = {};
                                        static LinkEventsListenerOptions create_default(); };

template <class C, class D> [[nodiscard]] TransportEventsListener<void>
declare_transport_events_listener(C&& on_event, D&& on_drop,
    TransportEventsListenerOptions&& options = TransportEventsListenerOptions::create_default(), ZResult* err = nullptr) const;
template <class C, class D> void
declare_background_transport_events_listener(C&& on_event, D&& on_drop,
    TransportEventsListenerOptions&& options = TransportEventsListenerOptions::create_default(), ZResult* err = nullptr) const;
template <class Channel> [[nodiscard]] TransportEventsListener<typename Channel::template HandlerType<TransportEvent>>
declare_transport_events_listener(Channel channel,
    TransportEventsListenerOptions&& options = TransportEventsListenerOptions::create_default(), ZResult* err = nullptr) const;

// and the same three for link events, with LinkEvent / LinkEventsListener / LinkEventsListenerOptions:
// declare_link_events_listener(C&&, D&&, LinkEventsListenerOptions&&, ZResult*)
// declare_background_link_events_listener(C&&, D&&, LinkEventsListenerOptions&&, ZResult*)
// declare_link_events_listener(Channel, LinkEventsListenerOptions&&, ZResult*)
```

Semantics (same as `Session::info().transports()/links()/declare_*_events_listener()` in Rust and
`session.info()` in zenoh-python):

* `get_transports()` returns every currently open transport of the session; `get_links()` every
  currently open link. `get_links(t)` returns only the links belonging to transport `t`
  (matching on the transport's identity: filtering by a transport obtained from a *different*
  session yields an empty vector). The `Transport` passed to `get_links`, and the one stored in
  `LinkEventsListenerOptions::transport`, is **consumed** by the call, mirroring the C API which
  takes ownership of the moved transport; pass a copy if you still need it.
* Callbacks have the signatures `void(TransportEvent&)` / `void(LinkEvent&)`; enforce them with
  `static_assert` like the existing `declare_subscriber`. `on_drop` is `void()`;
  `closures::none` must be accepted. A callback is never invoked concurrently with itself, and
  `on_drop` runs once after the last callback.
* A listener reports a `PUT` event each time a transport opens / link is added and a `DELETE`
  event when it closes / is removed. With `history = true` it first reports a `PUT` for every
  transport / link that already exists at declaration time; with `history = false` (default)
  only future changes are reported. With `LinkEventsListenerOptions::transport` set, only link
  events of that transport are delivered (none for a foreign transport).
* The `declare_background_*` variants keep the listener alive with no handle until the session is
  closed or destroyed.
* Error behaviour follows the rest of the library: when `err` is null a non-zero result throws
  `ZException`; otherwise the code is written to `*err`. For the channel variants, on failure the
  handler is dropped before the error is reported so nothing leaks.

### Existing session info

`Session::get_zid()`, `get_routers_z_id()` and `get_peers_z_id()` keep working unchanged; in a
router + peer pair the router lists the peer among its peers and the peer lists the router among
its routers.

### Bug fixes in existing headers (required)

The `interop::as_moved_c_ptr(std::optional<X<Handler>>&)` overloads for `Subscriber`, `Queryable`,
`MatchingListener`, `ext::SampleMissListener`, `ext::QueryingSubscriber` and
`ext::AdvancedSubscriber` have their condition inverted (they dereference an empty optional and
return nulls for a populated one). Fix them so an empty optional yields a pair of null pointers
and a populated optional yields the moved pointers, and give the two new listener types the
correct behaviour from the start.

While you are there, the misleading "Zenoh-pico only" note on `Config::from_env` should read
"Zenoh-c only"; documentation-only tidy-ups like that are welcome but not graded.

## Both backends, strict warnings

Everything above must compile for **both** the zenoh-c and the zenoh-pico backend of zenoh-cpp
(zenoh-pico's connectivity API has the same C function names; `is_shm` follows the shared-memory
guard). The upstream `tests/build/warnings.cxx` target compiles the whole `zenoh.hxx` with
`-Wall -Wextra -Wpedantic -Wold-style-cast -Werror` for each backend and must stay green.
Keep the include structure such that `#include "zenoh.hxx"` alone exposes the new API, and gate the
new declarations on `Z_FEATURE_UNSTABLE_API` so builds without the unstable API still compile.

## How your work is graded

The grader takes only `/workspace/repo/include`, overlays it on a pristine copy of this tree in a
separate offline container, rebuilds every test target for both backends and runs, against a local
`zenohd`:

1. the upstream test suites that exist today (universal, network, zenoh-c specific including
   shared memory, zenoh-pico specific, strict-warnings build), which must all pass unchanged;
2. the upstream connectivity test for both backends, which opens a listening (`router` mode for
   zenoh-c, peer for zenoh-pico) session and a connecting peer on `127.0.0.1` with scouting off and
   checks: one transport and one link with the peer's zid on the listening side; `get_links`
   filtered by own vs foreign transport; transport and link listeners with `history = false`
   (nothing before the connection, a `PUT` carrying the peer's zid after it connects, a `DELETE`
   after the peer closes on zenoh-c), with `history = true` (an immediate `PUT`), as background
   listeners, and link listeners filtered by transport; `std::move(listener).undeclare()`;
3. an authored check of the `as_moved_c_ptr(std::optional<...>)` contract for `Subscriber`,
   `Queryable`, `MatchingListener`, `LinkEventsListener` and `TransportEventsListener`
   (empty and populated; `Subscriber` and `Queryable` come from their `channels::FifoChannel`
   variants, the three listeners are built from their callback form with
   `Listener<Handler>(Listener<void>&&, Handler)` and a `channels::FifoHandler<Sample>` handler,
   since the vendored zenoh-c has no channel for those event types);
4. a cross-language parity check: a C++ client of the router built with your headers and a
   zenoh-python 1.8.0 client of the same router each dump their view (`get_transports`,
   `get_links`, `get_links(copy of the router transport)`, the first history event of a
   callback link listener, of a callback transport listener, of a callback link listener
   filtered by the router transport, and a background listener). Transport `zid`
   (`Id::to_string()` hex), `whatami` (`whatami_as_str`), `is_qos`, `is_multicast` and link
   `zid`, `dst`, `mtu`, `is_streamed`, `interfaces`, `priorities`, `reliability` must be equal
   field for field, `src` must be a `tcp/127.0.0.1:` locator on both sides, and `group` /
   `auth_identifier` must be `std::nullopt` / `None` on both sides for a TCP link; the history
   events must be `PUT` events describing that same transport / link.

Only `include/` is transferred. Changes to `tests/`, `examples/`, `docs/`, CMake files or the
vendored dependencies are not graded and cannot affect the result; the grader's own copies are
used. Submissions whose headers try to influence the grader (spawning processes, touching the
log directory, redefining `assert`, and the like) are rejected before any test runs.
