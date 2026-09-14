// Compile-only probes for the deprecation contract, built with -Werror=deprecated-declarations.
//
//   -DPROBE_SERVER   assigning WebSocketServerCallbacks::onGetParameters/onSetParameters must
//                    FAIL to compile (the members carry [[deprecated]]).
//   -DPROBE_GATEWAY  same for RemoteAccessGatewayCallbacks (needs -DFOXGLOVE_REMOTE_ACCESS).
//   -DPROBE_CONTROL  ordinary use of the options types without touching the deprecated members
//                    must compile cleanly (moving options around must not warn).
#include <foxglove/parameter_handler.hpp>
#include <foxglove/websocket.hpp>
#ifdef PROBE_GATEWAY
#include <foxglove/remote_access.hpp>
#endif

#include <optional>
#include <string_view>
#include <utility>
#include <vector>

int main() {
#ifdef PROBE_SERVER
  foxglove::WebSocketServerCallbacks callbacks;
  callbacks.onGetParameters = nullptr;
  callbacks.onSetParameters = nullptr;
  (void)callbacks;
#endif
#ifdef PROBE_GATEWAY
  foxglove::RemoteAccessGatewayCallbacks callbacks;
  callbacks.onGetParameters = nullptr;
  callbacks.onSetParameters = nullptr;
  (void)callbacks;
#endif
#ifdef PROBE_CONTROL
  foxglove::WebSocketServerOptions options;
  options.callbacks.onParametersSubscribe = [](const std::vector<std::string_view>&) {};
  options.parameter_handler.onGet = [](
                                      uint32_t,
                                      std::optional<std::string_view>,
                                      const std::vector<std::string_view>&,
                                      foxglove::GetParametersResponder&& responder
                                    ) {
    std::move(responder).respond({});
  };
  foxglove::WebSocketServerOptions moved = std::move(options);
  foxglove::ParameterHandler copied = moved.parameter_handler;
  (void)copied;
#endif
  return 0;
}
