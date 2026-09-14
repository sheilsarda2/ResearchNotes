//
// Verifier-authored cross-language parity probe (zenoh-c backend).
//
// Opens a client session to the verifier's zenohd (ZENOH_TEST_ROUTER) through the
// submitted C++ API and prints, as JSON, everything the connectivity API reports about
// the router: transports, links, the filtered link query, and the first history event of
// each listener flavour (callback, filtered callback, background). tests/hidden/parity_dump.py produces the same document through
// zenoh-python against the same router; compare_parity.py diffs them field by field.
//
// Waiting is deadline-bounded polling on the handler / condition variables, never a
// fixed sleep used as synchronisation.
//
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "zenoh.hxx"
#include "zenoh_test_config.hxx"

using namespace zenoh;
using namespace std::chrono_literals;

#if !defined(Z_FEATURE_UNSTABLE_API)
int main() {
    std::cout << "{\"error\":\"Z_FEATURE_UNSTABLE_API is not defined\"}" << std::endl;
    return 2;
}
#else

static std::string js(const std::string& s) {
    std::ostringstream o;
    o << '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"':
                o << "\\\"";
                break;
            case '\\':
                o << "\\\\";
                break;
            case '\n':
                o << "\\n";
                break;
            case '\r':
                o << "\\r";
                break;
            case '\t':
                o << "\\t";
                break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    o << buf;
                } else {
                    o << c;
                }
        }
    }
    o << '"';
    return o.str();
}

static std::string opt_js(const std::optional<std::string>& s) { return s.has_value() ? js(*s) : "null"; }

static std::string kind_str(SampleKind k) {
    if (k == Z_SAMPLE_KIND_PUT) return "put";
    if (k == Z_SAMPLE_KIND_DELETE) return "delete";
    return "unknown";
}

static std::string rel_str(Reliability r) {
    if (r == Z_RELIABILITY_BEST_EFFORT) return "best_effort";
    if (r == Z_RELIABILITY_RELIABLE) return "reliable";
    return "unknown";
}

static std::string transport_json(const Transport& t) {
    std::ostringstream o;
    o << "{\"zid\":" << js(t.get_zid().to_string()) << ",\"whatami\":" << js(std::string(whatami_as_str(t.get_whatami())))
      << ",\"is_qos\":" << (t.is_qos() ? "true" : "false") << ",\"is_multicast\":" << (t.is_multicast() ? "true" : "false");
#if defined(Z_FEATURE_SHARED_MEMORY)
    o << ",\"is_shm\":" << (t.is_shm() ? "true" : "false");
#endif
    o << "}";
    return o.str();
}

static std::string link_json(const Link& l) {
    std::ostringstream o;
    o << "{\"zid\":" << js(l.get_zid().to_string()) << ",\"src\":" << js(l.get_src()) << ",\"dst\":" << js(l.get_dst())
      << ",\"group\":" << opt_js(l.get_group()) << ",\"auth_identifier\":" << opt_js(l.get_auth_identifier())
      << ",\"mtu\":" << static_cast<unsigned>(l.get_mtu()) << ",\"is_streamed\":" << (l.is_streamed() ? "true" : "false")
      << ",\"interfaces\":[";
    bool first = true;
    for (const auto& i : l.get_interfaces()) {
        if (!first) o << ",";
        first = false;
        o << js(i);
    }
    o << "]";
    auto pr = l.get_priorities();
    if (pr.has_value()) {
        o << ",\"priorities\":[" << static_cast<unsigned>(pr->first) << "," << static_cast<unsigned>(pr->second) << "]";
    } else {
        o << ",\"priorities\":null";
    }
    auto rel = l.get_reliability();
    o << ",\"reliability\":" << (rel.has_value() ? js(rel_str(*rel)) : std::string("null")) << "}";
    return o.str();
}

template <class F>
static bool wait_until(F&& pred, std::chrono::milliseconds budget) {
    auto deadline = std::chrono::steady_clock::now() + budget;
    while (std::chrono::steady_clock::now() < deadline) {
        if (pred()) return true;
        std::this_thread::sleep_for(20ms);
    }
    return pred();
}

int main() {
    init_log_from_env_or("error");
    auto session = Session::open(test_config());

    // Client-mode open returns once the router transport is established, but poll briefly
    // in case the info tables are populated asynchronously.
    std::vector<Transport> transports;
    wait_until(
        [&] {
            transports = session.get_transports();
            return !transports.empty();
        },
        10000ms);
    auto links = session.get_links();

    size_t links_filtered_count = 0;
    if (!transports.empty()) {
        Transport copy(transports[0]);  // exercises the copy constructor; get_links consumes its argument
        links_filtered_count = session.get_links(std::move(copy)).size();
    }

    // Link events, callback flavour, with history: expect a PUT for the existing link.
    // (zenoh-c at the vendored revision has no fifo/ring channel for LinkEvent, so the channel
    // variant of declare_link_events_listener cannot be instantiated; see instruction.md.)
    std::string link_event = "null";
    {
        std::mutex m;
        std::condition_variable cv;
        std::optional<std::string> got;
        Session::LinkEventsListenerOptions opts;
        opts.history = true;
        auto listener = session.declare_link_events_listener(
            [&](LinkEvent& e) {
                std::lock_guard<std::mutex> g(m);
                if (!got.has_value()) {
                    got = "{\"kind\":" + js(kind_str(e.get_kind())) + ",\"link\":" + link_json(e.get_link()) + "}";
                }
                cv.notify_all();
            },
            closures::none, std::move(opts));
        {
            std::unique_lock<std::mutex> g(m);
            cv.wait_for(g, 10s, [&] { return got.has_value(); });
            if (got.has_value()) link_event = *got;
        }
        std::move(listener).undeclare();
    }

    // Link events filtered by the router transport (options.transport), with history.
    size_t link_event_filtered_count = 0;
    if (!transports.empty()) {
        std::mutex m;
        std::condition_variable cv;
        size_t count = 0;
        Session::LinkEventsListenerOptions opts;
        opts.history = true;
        opts.transport = Transport(transports[0]);
        auto listener = session.declare_link_events_listener(
            [&](LinkEvent& e) {
                if (e.get_kind() == Z_SAMPLE_KIND_PUT) {
                    std::lock_guard<std::mutex> g(m);
                    ++count;
                    cv.notify_all();
                }
            },
            closures::none, std::move(opts));
        {
            std::unique_lock<std::mutex> g(m);
            cv.wait_for(g, 10s, [&] { return count > 0; });
            link_event_filtered_count = count;
        }
        std::move(listener).undeclare();
    }

    // Transport events, callback flavour, with history: expect a PUT for the router transport.
    std::string transport_event = "null";
    {
        std::mutex m;
        std::condition_variable cv;
        std::optional<std::string> got;
        Session::TransportEventsListenerOptions opts;
        opts.history = true;
        auto listener = session.declare_transport_events_listener(
            [&](TransportEvent& e) {
                std::lock_guard<std::mutex> g(m);
                if (!got.has_value()) {
                    got = "{\"kind\":" + js(kind_str(e.get_kind())) + ",\"transport\":" + transport_json(e.get_transport()) + "}";
                }
                cv.notify_all();
            },
            closures::none, std::move(opts));
        {
            std::unique_lock<std::mutex> g(m);
            cv.wait_for(g, 10s, [&] { return got.has_value(); });
            if (got.has_value()) transport_event = *got;
        }
        std::move(listener).undeclare();
    }

    // Background listener with history: callback must fire at least once and the listener
    // needs no handle to stay alive.
    std::atomic<int> background_events{0};
    {
        std::mutex m;
        std::condition_variable cv;
        Session::TransportEventsListenerOptions opts;
        opts.history = true;
        session.declare_background_transport_events_listener(
            [&](TransportEvent&) {
                background_events.fetch_add(1);
                std::lock_guard<std::mutex> g(m);
                cv.notify_all();
            },
            closures::none, std::move(opts));
        std::unique_lock<std::mutex> g(m);
        cv.wait_for(g, 10s, [&] { return background_events.load() > 0; });
    }

    std::ostringstream o;
    o << "{\"zid\":" << js(session.get_zid().to_string()) << ",\"transports\":[";
    for (size_t i = 0; i < transports.size(); ++i) {
        if (i) o << ",";
        o << transport_json(transports[i]);
    }
    o << "],\"links\":[";
    for (size_t i = 0; i < links.size(); ++i) {
        if (i) o << ",";
        o << link_json(links[i]);
    }
    o << "],\"links_filtered_count\":" << links_filtered_count << ",\"link_event\":" << link_event
      << ",\"link_event_filtered_count\":" << link_event_filtered_count << ",\"transport_event\":" << transport_event
      << ",\"background_events\":" << background_events.load() << "}";
    std::cout << o.str() << std::endl;

    session.close();
    return 0;
}
#endif
