---
name: perf-profile
description: Diagnose and fix performance problems — slow endpoints, CPU hot paths, memory leaks, N+1 queries, bundle size, slow tests — by measuring first with profilers and benchmarks, then optimising the proven bottleneck. Use when something is slow, uses too much memory, or when asked to optimise or benchmark.
---

# Performance profiling

Measure → find the bottleneck → fix it → measure again. Never optimise on intuition alone.

## Procedure
1. **Define the metric and target**: latency (p50/p95/p99), throughput, memory peak, startup time, bundle size, query count. Record the environment (hardware, data size, build mode — always profile release/production builds).
2. **Build a repeatable benchmark**: a script or test that exercises the slow path with realistic data; run it ≥ 5 times and note mean and variance. Commit it if the project has a bench folder.
3. **Profile** with the right tool:
   - Python: `python -m cProfile -o out.prof script.py` + `snakeviz`/`pstats`; `py-spy record -o flame.svg -- python app.py`; memory: `tracemalloc`, `memray`
   - Node: `node --cpu-prof`, `clinic flame`, Chrome DevTools; memory: heap snapshots
   - Go: `go test -bench . -cpuprofile cpu.out` + `go tool pprof`; Rust: `cargo flamegraph`; JVM: async-profiler, JFR
   - DB: `EXPLAIN (ANALYZE, BUFFERS)`; count queries per request (look for N+1)
   - Web: Lighthouse, Performance panel, bundle analyser (`vite-bundle-visualizer`, `webpack-bundle-analyzer`)
4. **Identify the bottleneck**: the top frames/queries by self and cumulative time. State it with numbers.
5. **Fix the biggest win first**: better algorithm/data structure, batching, caching with invalidation, indexes, avoiding repeated work, streaming instead of loading, lazy loading, concurrency where I/O bound.
6. **Re-measure** with the same benchmark. Keep the change only if the improvement is real and outside noise. Verify correctness with the test suite.

## Rules
- One optimisation at a time; measure each.
- Don't trade correctness or readability for micro-gains; document non-obvious optimisations.
- Watch for regressions elsewhere (memory vs speed trade-offs).

## Exit criteria
Report: baseline vs after numbers (same benchmark), the bottleneck and its evidence (profile excerpt), the change, and tests passing.
