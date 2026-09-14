/*
 * Parity driver for the C API (scenario "handler" only).
 *
 * Registers a `foxglove_parameter_handler` on a WebSocket server through the submission's
 * generated header and runs the same scripted parameter store as the Rust reference server
 * (refserver/src/main.rs). The verifier drives it with the scripted ws-protocol client and
 * compares the transcript with the reference. Compiled by the verifier against the freshly
 * built libfoxglove.
 */
#include <foxglove-c/foxglove-c.h>

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifdef PARITY_CHECK_GATEWAY
/* Compile-only: the gateway options must accept a handler through the same struct. */
static void gateway_compile_check(void) {
  struct foxglove_gateway_options g;
  struct foxglove_parameter_handler h;
  memset(&g, 0, sizeof g);
  memset(&h, 0, sizeof h);
  g.parameter_handler = &h;
  (void)g;
}
#endif

#define MAX_PARAMS 64
#define NAME_CAP 128

typedef struct {
  char name[NAME_CAP];
  struct foxglove_parameter* param;
} entry_t;

static entry_t g_store[MAX_PARAMS];
static size_t g_store_len = 0;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static struct foxglove_websocket_server* g_server = NULL;

static struct foxglove_string fstr(const char* s) {
  struct foxglove_string r;
  r.data = s;
  r.len = strlen(s);
  return r;
}

static int fstr_eq(const struct foxglove_string* s, const char* lit) {
  size_t n = strlen(lit);
  return s->len == n && memcmp(s->data, lit, n) == 0;
}

static void fstr_copy(const struct foxglove_string* s, char* buf, size_t cap) {
  size_t n = s->len < cap - 1 ? s->len : cap - 1;
  memcpy(buf, s->data, n);
  buf[n] = 0;
}

/* Keeps the store sorted by name (byte-wise) so "all parameters" matches std::map / BTreeMap
 * iteration order. Takes ownership of `param`; replaces an existing entry of the same name. */
static void store_put(const char* name, struct foxglove_parameter* param) {
  size_t i;
  for (i = 0; i < g_store_len; ++i) {
    int c = strcmp(g_store[i].name, name);
    if (c == 0) {
      foxglove_parameter_free(g_store[i].param);
      g_store[i].param = param;
      return;
    }
    if (c > 0) {
      break;
    }
  }
  if (g_store_len >= MAX_PARAMS) {
    foxglove_parameter_free(param);
    return;
  }
  memmove(&g_store[i + 1], &g_store[i], (g_store_len - i) * sizeof(entry_t));
  snprintf(g_store[i].name, sizeof g_store[i].name, "%s", name);
  g_store[i].param = param;
  ++g_store_len;
}

static const struct foxglove_parameter* store_get(const char* name) {
  for (size_t i = 0; i < g_store_len; ++i) {
    if (strcmp(g_store[i].name, name) == 0) {
      return g_store[i].param;
    }
  }
  return NULL;
}

#define MUST(expr)                                                              \
  do {                                                                          \
    foxglove_error err_ = (expr);                                               \
    if (err_ != FOXGLOVE_ERROR_OK) {                                            \
      fprintf(stderr, "%s failed with error %d\n", #expr, (int)err_);           \
      exit(1);                                                                  \
    }                                                                           \
  } while (0)

static void init_store(void) {
  struct foxglove_parameter* p = NULL;
  const double arr[] = {1.0, 2.0, 3.0};
  const int64_t ints[] = {1, 2};
  const uint8_t blob[] = {1, 2, 3};
  struct foxglove_bytes bytes;

  MUST(foxglove_parameter_create_float64(&p, fstr("foo"), 1.5));
  store_put("foo", p);
  MUST(foxglove_parameter_create_string(&p, fstr("bar"), fstr("BAR")));
  store_put("bar", p);
  MUST(foxglove_parameter_create_boolean(&p, fstr("flag"), true));
  store_put("flag", p);
  MUST(foxglove_parameter_create_integer(&p, fstr("count"), 7));
  store_put("count", p);
  MUST(foxglove_parameter_create_float64_array(&p, fstr("arr"), arr, 3));
  store_put("arr", p);
  MUST(foxglove_parameter_create_integer_array(&p, fstr("ints"), ints, 2));
  store_put("ints", p);
  bytes.data = blob;
  bytes.len = sizeof blob;
  MUST(foxglove_parameter_create_byte_array(&p, fstr("blob"), bytes));
  store_put("blob", p);
  MUST(foxglove_parameter_create_string(&p, fstr("ro_locked"), fstr("locked")));
  store_put("ro_locked", p);
  MUST(foxglove_parameter_create_empty(&p, fstr("unset_param")));
  store_put("unset_param", p);
}

struct slow_ctx {
  struct foxglove_get_parameters_responder* responder;
};

/* Completes a get request from another thread after a delay. */
static void* slow_thread(void* arg) {
  struct slow_ctx* ctx = arg;
  struct foxglove_parameter_array* arr;
  const struct foxglove_parameter* foo;
  usleep(100 * 1000);
  arr = foxglove_parameter_array_create(1);
  pthread_mutex_lock(&g_mu);
  foo = store_get("foo");
  if (foo != NULL) {
    foxglove_parameter_array_push(arr, foxglove_parameter_clone(foo));
  }
  pthread_mutex_unlock(&g_mu);
  foxglove_get_parameters_responder_respond(ctx->responder, arr);
  free(ctx);
  return NULL;
}

static void on_get(
  const void* context, uint32_t client_id, const struct foxglove_string* request_id,
  const struct foxglove_string* names, size_t names_len,
  struct foxglove_get_parameters_responder* responder
) {
  int slow = 0;
  struct foxglove_parameter_array* arr;
  (void)context;
  (void)client_id;
  (void)request_id;

  for (size_t i = 0; i < names_len; ++i) {
    if (fstr_eq(&names[i], "drop") || fstr_eq(&names[i], "throw")) {
      /* Drop without responding: the SDK must send the generic error status. */
      foxglove_get_parameters_responder_drop(responder);
      return;
    }
    if (fstr_eq(&names[i], "slow")) {
      slow = 1;
    }
  }
  if (slow) {
    struct slow_ctx* ctx = malloc(sizeof *ctx);
    pthread_t t;
    ctx->responder = responder;
    if (pthread_create(&t, NULL, slow_thread, ctx) == 0) {
      pthread_detach(t);
      return;
    }
    free(ctx);
  }

  pthread_mutex_lock(&g_mu);
  arr = foxglove_parameter_array_create(names_len ? names_len : g_store_len);
  if (names_len == 0) {
    for (size_t i = 0; i < g_store_len; ++i) {
      foxglove_parameter_array_push(arr, foxglove_parameter_clone(g_store[i].param));
    }
  } else {
    char buf[NAME_CAP];
    for (size_t i = 0; i < names_len; ++i) {
      const struct foxglove_parameter* p;
      fstr_copy(&names[i], buf, sizeof buf);
      p = store_get(buf);
      if (p != NULL) {
        foxglove_parameter_array_push(arr, foxglove_parameter_clone(p));
      }
    }
  }
  pthread_mutex_unlock(&g_mu);
  foxglove_get_parameters_responder_respond(responder, arr);
}

static void on_set(
  const void* context, uint32_t client_id, const struct foxglove_string* request_id,
  const struct foxglove_parameter_array* params, struct foxglove_set_parameters_responder* responder
) {
  struct foxglove_parameter_array* result;
  struct foxglove_parameter_array* applied;
  size_t applied_len = 0;
  char buf[NAME_CAP];
  (void)context;
  (void)client_id;
  (void)request_id;

  for (size_t i = 0; i < params->len; ++i) {
    if (fstr_eq(&params->parameters[i].name, "drop")) {
      foxglove_set_parameters_responder_drop(responder);
      return;
    }
  }

  result = foxglove_parameter_array_create(params->len);
  applied = foxglove_parameter_array_create(params->len);
  pthread_mutex_lock(&g_mu);
  for (size_t i = 0; i < params->len; ++i) {
    const struct foxglove_parameter* p = &params->parameters[i];
    fstr_copy(&p->name, buf, sizeof buf);
    if (strncmp(buf, "ro_", 3) == 0) {
      const struct foxglove_parameter* existing = store_get(buf);
      if (existing != NULL) {
        foxglove_parameter_array_push(result, foxglove_parameter_clone(existing));
      }
      continue;
    }
    store_put(buf, foxglove_parameter_clone(p));
    foxglove_parameter_array_push(result, foxglove_parameter_clone(p));
    foxglove_parameter_array_push(applied, foxglove_parameter_clone(p));
    ++applied_len;
  }
  pthread_mutex_unlock(&g_mu);

  foxglove_set_parameters_responder_respond(responder, result);
  /* The responder only echoes to the requester; broadcast applied changes ourselves. */
  if (applied_len > 0 && g_server != NULL) {
    foxglove_server_publish_parameter_values(g_server, applied);
  } else {
    foxglove_parameter_array_free(applied);
  }
}

int main(int argc, char** argv) {
  const char* scenario = "handler";
  const struct foxglove_context* ctx;
  struct foxglove_parameter_handler handler;
  struct foxglove_server_options opts;
  struct foxglove_websocket_server* server = NULL;
  foxglove_error err;
  char line[256];

  for (int i = 1; i + 1 < argc; ++i) {
    if (strcmp(argv[i], "--scenario") == 0) {
      scenario = argv[++i];
    }
  }
  if (strcmp(scenario, "handler") != 0) {
    fprintf(stderr, "c_driver supports only the handler scenario\n");
    return 2;
  }
#ifdef PARITY_CHECK_GATEWAY
  if (argc < 0) {
    gateway_compile_check();
  }
#endif

  init_store();
  ctx = foxglove_context_new();

  memset(&handler, 0, sizeof handler);
  handler.context = NULL;
  handler.get = on_get;
  handler.set = on_set;

  memset(&opts, 0, sizeof opts);
  opts.context = ctx;
  opts.name = fstr("parity-server");
  opts.host = fstr("127.0.0.1");
  opts.port = 0;
  /* No explicit Parameters capability: registering a handler must advertise it. */
  opts.parameter_handler = &handler;

  err = foxglove_server_start(&opts, &server);
  if (err != FOXGLOVE_ERROR_OK) {
    fprintf(stderr, "foxglove_server_start failed with error %d\n", (int)err);
    return 1;
  }
  g_server = server;
  printf("PORT=%u\n", (unsigned)foxglove_server_get_port(server));
  fflush(stdout);

  while (fgets(line, sizeof line, stdin) != NULL) {
  }

  foxglove_server_stop(server);
  g_server = NULL;
  foxglove_context_free(ctx);
  return 0;
}
