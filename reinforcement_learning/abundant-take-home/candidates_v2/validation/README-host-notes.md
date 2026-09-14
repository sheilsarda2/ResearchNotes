# Validation host notes
- 2026-09-13 until ~17:00 UTC: Docker Desktop VM ~8.3 GB (18 CPUs), aarch64. All oracle/no-op results before that time were produced under this limit; the `CARGO_BUILD_JOBS=4` bounds and OOM notes in task STATUS files refer to it.
- From ~17:00 UTC: Docker Desktop VM reports ~32 GB. Later runs (hygiene re-validations, cpp-rerun-cpp reruns) ran with the larger VM.
