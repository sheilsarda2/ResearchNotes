# Reviewer admission wait, 2026-09-14 08:22 UTC

This is a read-only snapshot of the existing second final-task review queue. No scheduler, control, process, task, or model setting was changed. The locked shared-state snapshot and raw history excerpts are bound by `analysis.json` (SHA-256 `ade5984825faa91728f8e2a108ae22a128dcd654536ef1ba981dcde44fb2f9e7`). Live append-only histories are copied as exact selected lines with hashes; their full bytes-at-read hashes are also recorded.

The reviewer was registered as PID 83205/start ticks 5750249 with zero claims. Five participants shared 14 slots: main 6, Zenoh 3, Diskcache 1, Burn 4, reviewer 0. The unmanaged fair ceiling was `max(1, 14 // 5) = 2`, and the reviewer's local cap was 1. Its one pending review was therefore below both ceilings. The four managed sweep campaigns bypass the unmanaged fair ceiling. The scheduler counts registrations, without explicit participant-demand accounting, FIFO ordering, aging, or a reserved opening for this reviewer.

Six other sweep admissions occurred between the first reviewer resource record at 08:03:43 and this snapshot. Several free-slot windows overlapped reviewer memory pressure or cooldown. Three openings were refilled approximately 8 ms, 76 ms, and 50 ms after a worker logged releasing a claim, while the reviewer retries every two seconds. At 08:14:45 the reviewer reached `shared staggered startup` with space available; another sweep start filled that space at 08:14:47.

| New admission | UTC | Approximate interval since latest preceding release status |
|---|---|---:|
| Rosbag2 `QDvNHN6` | 08:04:39.632 | 8 ms |
| Burn `RBjsods` | 08:12:32.429 | 189 s; reviewer cooldown in surrounding sample |
| Burn `kYchPug` | 08:14:42.805 | 21 s; reviewer cooldown then stagger |
| C++ Zenoh `pmRF3yV` | 08:14:47.806 | 26 s; follows Burn's five-second stagger |
| Foxglove `3LvTxsB` | 08:19:27.618 | 76 ms |
| Rerun `pUANYZX` | 08:20:17.696 | 50 ms |

The queue is functioning and its heartbeat is fresh. This is repeated competition for admission, with a starvation risk, rather than a period with no openings or evidence of a stuck registration. It does not establish indefinite starvation. Resource writes are throttled to roughly five seconds and shared observations sample roughly once per second, so they do not record every retry or every sub-second vacancy. Release timestamps are post-release status writes, not exact lock timings. No scheduling action is prescribed or applied by this audit.
