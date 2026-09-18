# Optimizer benchmark

Direct solver calls; result and interpretation caches bypassed. Alternating comparison order on public cases with 1% changes. Before: original cold binary formulation. After: equivalent lossless LP formulation, with cycle cancellation and verified history storage. Native warm binary formulation is also measured separately. Timings include validation, model construction, applicable history search, CBC invocation and history storage, excluding independent benchmark verification. Training solves excluded. No time/node limits or nonzero optimality gaps.

- Cases: 10; repetitions: 10; paired solves: 100
- Before average: 53.831 ms
- After average: 41.472 ms
- Native warm binary average: 59.580 ms
- Speedup: 22.96%
- Every schedule independently verified; every cold/warm objective matched.

These small CBC models are sensitive to process startup and machine load. A warm start is a hint, not a guarantee of speedup.
