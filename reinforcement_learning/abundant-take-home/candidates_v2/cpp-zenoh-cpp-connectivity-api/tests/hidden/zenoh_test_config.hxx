// Verifier-owned helper. The verifier container has no network beyond loopback, so
// upstream tests that rely on multicast scouting between peer sessions are re-pointed
// at a local zenohd router: every session becomes a client of ZENOH_TEST_ROUTER
// (default tcp/127.0.0.1:27447) with scouting disabled. Test bodies are otherwise
// unchanged (prepare_tests.sh substitutes Config::create_default() -> test_config()).
#pragma once

#include <cstdlib>
#include <string>

#include "zenoh.hxx"

inline const char* test_router_locator() {
    const char* loc = std::getenv("ZENOH_TEST_ROUTER");
    return (loc != nullptr && *loc != '\0') ? loc : "tcp/127.0.0.1:27447";
}

inline zenoh::Config test_config() {
    auto config = zenoh::Config::create_default();
#if defined(ZENOHCXX_ZENOHC)
    config.insert_json5("mode", "\"client\"");
    config.insert_json5("connect/endpoints", std::string("[\"") + test_router_locator() + "\"]");
    config.insert_json5("scouting/multicast/enabled", "false");
    config.insert_json5("scouting/gossip/enabled", "false");
#else
    config.insert(Z_CONFIG_MODE_KEY, "client");
    config.insert(Z_CONFIG_CONNECT_KEY, test_router_locator());
    config.insert(Z_CONFIG_MULTICAST_SCOUTING_KEY, "false");
#endif
    return config;
}
