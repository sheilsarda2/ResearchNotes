//
// Verifier-authored test (zenoh-c backend only).
//
// Checks the documented contract of interop::as_moved_c_ptr(std::optional<...>&) for
// handler-bearing entities: an empty optional yields a pair of null pointers, a populated
// optional yields the moved pointers of the entity and of its handler. The pristine tree
// has this condition inverted for Subscriber, Queryable, MatchingListener,
// SampleMissListener, QueryingSubscriber and AdvancedSubscriber (empty -> throws
// std::bad_optional_access, populated -> nulls); the two new listener types must get the
// correct behaviour from the start.
//
// Subscriber and Queryable are obtained through their channels::FifoChannel variants. zenoh-c
// at the vendored revision has no fifo/ring channel primitive for MatchingStatus, LinkEvent or
// TransportEvent, so MatchingListener<Handler>, LinkEventsListener<Handler> and
// TransportEventsListener<Handler> are built from their callback (<void>) form with the public
// Listener<Handler>(Listener<void>&&, Handler) constructor and an existing Owned<> handler
// (a channels::FifoHandler<Sample> taken back from an undeclared channel subscriber).
//
#include <cstdio>
#include <optional>
#include <utility>

#include "zenoh.hxx"
#include "zenoh_test_config.hxx"

using namespace zenoh;

#undef NDEBUG
#include <assert.h>

using TestHandler = channels::FifoHandler<Sample>;

template <class T>
void check_empty(const char* what) {
    std::optional<T> o;
    auto p = interop::as_moved_c_ptr(o);
    assert(p.first == nullptr);
    assert(p.second == nullptr);
    printf("empty optional<%s>: PASS\n", what);
}

template <class T>
void check_populated(std::optional<T>& o, const char* what) {
    assert(o.has_value());
    auto p = interop::as_moved_c_ptr(o);
    assert(p.first != nullptr);
    assert(p.second != nullptr);
    printf("populated optional<%s>: PASS\n", what);
}

// A standalone Owned<> handler object: declare a channel subscriber and take its handler back.
static TestHandler make_handler(const Session& session, const KeyExpr& ke) {
    auto sub = session.declare_subscriber(ke, channels::FifoChannel(4));
    return std::move(sub).undeclare();
}

int main() {
    init_log_from_env_or("error");

    check_empty<Subscriber<channels::FifoHandler<Sample>>>("Subscriber");
    check_empty<Queryable<channels::FifoHandler<Query>>>("Queryable");
    check_empty<MatchingListener<TestHandler>>("MatchingListener");
#if defined(Z_FEATURE_UNSTABLE_API)
    check_empty<LinkEventsListener<TestHandler>>("LinkEventsListener");
    check_empty<TransportEventsListener<TestHandler>>("TransportEventsListener");
#endif

    auto session = Session::open(test_config());
    KeyExpr ke("zenoh/verifier/interop_optional");

    std::optional<Subscriber<channels::FifoHandler<Sample>>> sub =
        session.declare_subscriber(ke, channels::FifoChannel(4));
    check_populated(sub, "Subscriber");

    std::optional<Queryable<channels::FifoHandler<Query>>> q = session.declare_queryable(ke, channels::FifoChannel(4));
    check_populated(q, "Queryable");

    auto pub = session.declare_publisher(ke);
    std::optional<MatchingListener<TestHandler>> ml = MatchingListener<TestHandler>(
        pub.declare_matching_listener([](const MatchingStatus&) {}, closures::none), make_handler(session, ke));
    check_populated(ml, "MatchingListener");

#if defined(Z_FEATURE_UNSTABLE_API)
    std::optional<LinkEventsListener<TestHandler>> ll = LinkEventsListener<TestHandler>(
        session.declare_link_events_listener([](LinkEvent&) {}, closures::none), make_handler(session, ke));
    check_populated(ll, "LinkEventsListener");

    std::optional<TransportEventsListener<TestHandler>> tl = TransportEventsListener<TestHandler>(
        session.declare_transport_events_listener([](TransportEvent&) {}, closures::none), make_handler(session, ke));
    check_populated(tl, "TransportEventsListener");
#endif

    printf("All interop_optional checks passed!\n");
    return 0;
}
