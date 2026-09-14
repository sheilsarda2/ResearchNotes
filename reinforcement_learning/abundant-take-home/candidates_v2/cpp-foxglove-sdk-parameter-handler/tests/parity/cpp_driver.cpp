// Parity driver for the C++ SDK.
//
// Runs the same scripted parameter store as the Rust reference server (refserver/src/main.rs)
// through the submission's foxglove::ParameterHandler API. The verifier drives both with one
// scripted ws-protocol client and compares transcripts.
//
// Modes:
//   --scenario handler|precedence|legacy   start a server, print PORT=<n>, stop on stdin EOF
//   --check validation                     create() must reject a half-set handler (ValueError)
//
// Compiled by the verifier against the freshly built libfoxglove_cpp_shared / libfoxglove.

#include <foxglove/context.hpp>
#include <foxglove/error.hpp>
#include <foxglove/parameter.hpp>
#include <foxglove/parameter_handler.hpp>
#include <foxglove/websocket.hpp>

#ifdef PARITY_CHECK_GATEWAY
#include <foxglove/remote_access.hpp>
#include <type_traits>
// The gateway options must expose the same handler type as the server options.
static_assert(
  std::is_same_v<decltype(foxglove::RemoteAccessGatewayOptions::parameter_handler), foxglove::ParameterHandler>,
  "RemoteAccessGatewayOptions::parameter_handler must be a foxglove::ParameterHandler"
);
#endif

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {

std::map<std::string, foxglove::Parameter> initialStore() {
  std::map<std::string, foxglove::Parameter> store;
  auto put = [&](foxglove::Parameter&& p) {
    std::string name(p.name());
    store.emplace(std::move(name), std::move(p));
  };
  put(foxglove::Parameter("foo", 1.5));
  put(foxglove::Parameter("bar", "BAR"));
  put(foxglove::Parameter("flag", true));
  put(foxglove::Parameter("count", static_cast<int64_t>(7)));
  put(foxglove::Parameter("arr", std::vector<double>{1.0, 2.0, 3.0}));
  put(foxglove::Parameter("ints", std::vector<int64_t>{1, 2}));
  const uint8_t blob[] = {1, 2, 3};
  put(foxglove::Parameter("blob", blob, sizeof(blob)));
  put(foxglove::Parameter("ro_locked", "locked"));
  put(foxglove::Parameter("unset_param"));
  return store;
}

struct Shared {
  std::mutex mu;
  std::map<std::string, foxglove::Parameter> store = initialStore();
  foxglove::WebSocketServer* server = nullptr;

  std::vector<foxglove::Parameter> getValues(const std::vector<std::string>& names) {
    std::lock_guard<std::mutex> lock(mu);
    std::vector<foxglove::Parameter> out;
    if (names.empty()) {
      for (const auto& kv : store) {
        out.push_back(kv.second.clone());
      }
      return out;
    }
    for (const auto& n : names) {
      auto it = store.find(n);
      if (it != store.end()) {
        out.push_back(it->second.clone());
      }
    }
    return out;
  }

  // Returns {result echoed to requester, applied for broadcast}.
  std::pair<std::vector<foxglove::Parameter>, std::vector<foxglove::Parameter>> applySet(
    const std::vector<foxglove::ParameterView>& params
  ) {
    std::lock_guard<std::mutex> lock(mu);
    std::vector<foxglove::Parameter> result;
    std::vector<foxglove::Parameter> applied;
    for (const auto& pv : params) {
      std::string name(pv.name());
      if (name.rfind("ro_", 0) == 0) {
        auto it = store.find(name);
        if (it != store.end()) {
          result.push_back(it->second.clone());
        }
        continue;
      }
      store.insert_or_assign(name, pv.clone());
      result.push_back(pv.clone());
      applied.push_back(pv.clone());
    }
    return {std::move(result), std::move(applied)};
  }

  void publish(std::vector<foxglove::Parameter>&& applied) {
    if (applied.empty() || server == nullptr) {
      return;
    }
    server->publishParameterValues(std::move(applied));
  }
};

std::vector<std::string> owned(const std::vector<std::string_view>& views) {
  std::vector<std::string> out;
  out.reserve(views.size());
  for (auto v : views) {
    out.emplace_back(v);
  }
  return out;
}

foxglove::ParameterHandler makeHandler(std::shared_ptr<Shared> shared) {
  foxglove::ParameterHandler handler;
  handler.onGet = [shared](
                    uint32_t /*client_id*/,
                    std::optional<std::string_view> /*request_id*/,
                    const std::vector<std::string_view>& param_names,
                    foxglove::GetParametersResponder&& responder
                  ) {
    auto names = owned(param_names);
    auto has = [&](const char* s) {
      return std::find(names.begin(), names.end(), s) != names.end();
    };
    if (has("drop")) {
      // Drop without responding: the SDK must send the generic error status.
      foxglove::GetParametersResponder dropped(std::move(responder));
      (void)dropped;
      return;
    }
    if (has("throw")) {
      // The SDK must contain this; the un-consumed responder is dropped by the wrapper.
      throw std::runtime_error("scripted handler failure");
    }
    if (has("slow")) {
      // Complete the request from another thread.
      std::thread([shared, r = std::move(responder)]() mutable {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        std::move(r).respond(shared->getValues({"foo"}));
      }).detach();
      return;
    }
    std::move(responder).respond(shared->getValues(names));
  };
  handler.onSet = [shared](
                    uint32_t /*client_id*/,
                    std::optional<std::string_view> /*request_id*/,
                    const std::vector<foxglove::ParameterView>& params,
                    foxglove::SetParametersResponder&& responder
                  ) {
    for (const auto& p : params) {
      if (p.name() == "drop") {
        foxglove::SetParametersResponder dropped(std::move(responder));
        (void)dropped;
        return;
      }
    }
    auto applied_pair = shared->applySet(params);
    std::move(responder).respond(std::move(applied_pair.first));
    // The responder only echoes to the requester; broadcast applied changes ourselves.
    shared->publish(std::move(applied_pair.second));
  };
  return handler;
}

// Legacy callbacks. With `sentinel`, they return a marker that must never reach the wire because
// a registered ParameterHandler takes precedence.
void setLegacyCallbacks(
  foxglove::WebSocketServerCallbacks& callbacks, std::shared_ptr<Shared> shared, bool sentinel
) {
#if defined(__GNUC__) || defined(__clang__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#endif
  callbacks.onGetParameters = [shared, sentinel](
                                uint32_t /*client_id*/,
                                std::optional<std::string_view> /*request_id*/,
                                const std::vector<std::string_view>& param_names
                              ) -> std::vector<foxglove::Parameter> {
    if (sentinel) {
      std::vector<foxglove::Parameter> out;
      out.emplace_back("legacy", true);
      return out;
    }
    return shared->getValues(owned(param_names));
  };
  callbacks.onSetParameters = [shared, sentinel](
                                uint32_t /*client_id*/,
                                std::optional<std::string_view> /*request_id*/,
                                const std::vector<foxglove::ParameterView>& params
                              ) -> std::vector<foxglove::Parameter> {
    if (sentinel) {
      std::vector<foxglove::Parameter> out;
      out.emplace_back("legacy", true);
      return out;
    }
    auto applied_pair = shared->applySet(params);
    return std::move(applied_pair.first);
  };
#if defined(__GNUC__) || defined(__clang__)
#pragma GCC diagnostic pop
#endif
}

int runServer(const std::string& scenario) {
  auto shared = std::make_shared<Shared>();

  foxglove::WebSocketServerOptions options;
  options.context = foxglove::Context::create();
  options.name = "parity-server";
  options.host = "127.0.0.1";
  options.port = 0;
  if (scenario == "handler") {
    options.parameter_handler = makeHandler(shared);
  } else if (scenario == "precedence") {
    options.capabilities = foxglove::WebSocketServerCapabilities::Parameters;
    setLegacyCallbacks(options.callbacks, shared, true);
    options.parameter_handler = makeHandler(shared);
  } else if (scenario == "legacy") {
    options.capabilities = foxglove::WebSocketServerCapabilities::Parameters;
    setLegacyCallbacks(options.callbacks, shared, false);
  } else {
    std::cerr << "unknown scenario: " << scenario << '\n';
    return 2;
  }

  auto result = foxglove::WebSocketServer::create(std::move(options));
  if (!result.has_value()) {
    std::cerr << "WebSocketServer::create failed: " << foxglove::strerror(result.error()) << '\n';
    return 1;
  }
  auto server = std::move(result.value());
  shared->server = &server;
  std::cout << "PORT=" << server.port() << std::endl;

  std::string line;
  while (std::getline(std::cin, line)) {
  }
  server.stop();
  shared->server = nullptr;
  return 0;
}

int runValidation() {
  auto stub_get = [](
                    uint32_t /*client_id*/,
                    std::optional<std::string_view> /*request_id*/,
                    const std::vector<std::string_view>& /*names*/,
                    foxglove::GetParametersResponder&& responder
                  ) {
    std::move(responder).respond({});
  };
  auto stub_set = [](
                    uint32_t /*client_id*/,
                    std::optional<std::string_view> /*request_id*/,
                    const std::vector<foxglove::ParameterView>& /*params*/,
                    foxglove::SetParametersResponder&& responder
                  ) {
    std::move(responder).respond({});
  };
  int failures = 0;
  auto expectValueError = [&](foxglove::WebSocketServerOptions&& options, const char* label) {
    auto result = foxglove::WebSocketServer::create(std::move(options));
    if (result.has_value()) {
      std::cerr << label << ": expected ValueError, got a server\n";
      ++failures;
      result.value().stop();
    } else if (result.error() != foxglove::FoxgloveError::ValueError) {
      std::cerr << label << ": expected ValueError, got " << foxglove::strerror(result.error()) << '\n';
      ++failures;
    }
  };
  auto expectOk = [&](foxglove::WebSocketServerOptions&& options, const char* label) {
    auto result = foxglove::WebSocketServer::create(std::move(options));
    if (!result.has_value()) {
      std::cerr << label << ": expected success, got " << foxglove::strerror(result.error()) << '\n';
      ++failures;
      return;
    }
    if (result.value().stop() != foxglove::FoxgloveError::Ok) {
      std::cerr << label << ": stop failed\n";
      ++failures;
    }
  };
  {
    foxglove::WebSocketServerOptions o;
    o.context = foxglove::Context::create();
    o.name = "validation";
    o.port = 0;
    o.parameter_handler.onGet = stub_get;
    expectValueError(std::move(o), "only onGet");
  }
  {
    foxglove::WebSocketServerOptions o;
    o.context = foxglove::Context::create();
    o.name = "validation";
    o.port = 0;
    o.parameter_handler.onSet = stub_set;
    expectValueError(std::move(o), "only onSet");
  }
  {
    foxglove::WebSocketServerOptions o;
    o.context = foxglove::Context::create();
    o.name = "validation";
    o.port = 0;
    expectOk(std::move(o), "no handler");
  }
  {
    foxglove::WebSocketServerOptions o;
    o.context = foxglove::Context::create();
    o.name = "validation";
    o.port = 0;
    o.parameter_handler.onGet = stub_get;
    o.parameter_handler.onSet = stub_set;
    expectOk(std::move(o), "both");
  }
  std::cout << (failures == 0 ? "VALIDATION_OK" : "VALIDATION_FAIL") << std::endl;
  return failures == 0 ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
  std::string scenario = "handler";
  std::string check;
  for (int i = 1; i + 1 < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--scenario") {
      scenario = argv[++i];
    } else if (arg == "--check") {
      check = argv[++i];
    }
  }
  if (check == "validation") {
    return runValidation();
  }
  return runServer(scenario);
}
