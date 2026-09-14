Fix query targeting when a Zenoh session owns multiple queryables on the same key expression with different `complete` flags.

A concrete deployment uses an authoritative complete queryable and an incomplete fallback in the same router/storage-manager session. A remote `QueryTarget::AllComplete` query can receive no replies if the complete queryable is declared first and the incomplete one second, even though `QueryTarget::All` reaches both. Declaration order must not change which live complete queryables are eligible.

Required behavior:

- For coexisting same-expression queryables, `All` continues to reach all eligible queryables and `AllComplete` reaches the eligible complete queryables, excluding incomplete ones. This includes either mixed declaration order and multiple complete or incomplete siblings.
- Declaring or undeclaring a sibling must preserve the remaining siblings' correct targeting. Removing the last complete queryable leaves no complete match; adding a complete queryable back restores it. Removing every queryable removes the match.
- Preserve key-expression matching, including wildcard expressions, and keep unrelated expressions independent. Preserve local-session queries and queries arriving from a remote client through the router.
- Keep existing public APIs, ordinary reply handling and consolidation behavior working. Do not introduce storage backends, change the network protocol, or turn declaration into a network-discovery readiness barrier.

The repository is `/workspace/repo`, pinned to the supplied Zenoh commit. Implement the fix in `zenoh/src/`. Only that directory is transferred to the fresh verifier. Cargo manifests, dependencies, build scripts outside that directory, upstream tests, and the verifier are protected; changes to them will not be graded. You may create local experiments, but the submitted fix must work with the supplied manifests and dependencies.

Rust 1.97.1 and vendored dependencies are installed. A small check harness builds the real workspace crate with TCP and unstable inspection APIs, avoiding unrelated optional transports. Run the existing same-session queryable regression with:

```bash
cargo test --manifest-path /opt/check/Cargo.toml --locked --offline \
  --test upstream_queryable test_queryable_same_session -- --exact --test-threads=1
```

The original upstream tests remain in the repository. The final verifier runs offline and exercises public Rust APIs over container-local TCP loopback with multicast disabled, plus the selected upstream regression. Correctness is based on reply sets and lifecycle transitions, not latency targets or a prescribed internal data structure.
