# Zenoh queryable reconnect reproduction

This reproduces a query potentially completing immediately with zero replies
after a queryable disconnects and reconnects.

It uses an upstream Zenoh 1.10.0 router and two client processes:

- `responder`: declares a queryable, disconnects, then reconnects.
- `requester`: queries before the disconnect, immediately after the reconnect,
  and one second later with the same second querier.

## Run

Start the router:

```sh
docker run --rm --network host eclipse/zenoh:1.10.0 -l tcp/0.0.0.0:7447
```

In a second terminal:

```sh
cargo run -- responder
```

Immediately afterward, in a third terminal:

```sh
cargo run -- requester
```

All Cargo commands must run from this directory.

The suspected failure looks like:

```text
before disconnect: reply=pong ...
immediately after reconnect: ZERO REPLIES ... after a few milliseconds
one second later: reply=pong ...
```

Because this is timing-dependent, run both clients again if the first attempt
does not reproduce it.
