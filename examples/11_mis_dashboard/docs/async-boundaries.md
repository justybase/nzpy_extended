# Async boundaries and blocking work

This example uses FastAPI and `async def` extensively, but an async function is
not automatically non-blocking. It can release the event loop only when it
reaches an `await` for an operation that actually suspends. Ordinary
synchronous code continues on the event-loop thread.

## The danger

This is an event-loop blocking anti-pattern:

```python
@router.get("/report")
async def report() -> dict[str, object]:
    rows = sqlite3_query()          # synchronous disk I/O
    workbook = make_xlsx(rows)      # synchronous CPU and file I/O
    return {"rows": rows, "file": workbook}
```

While either call runs, unrelated requests in the same worker wait. The
problem is especially easy to miss with a small demo dataset: the endpoint
works in development, but latency spikes when a real export or full refresh
arrives.

## Safe boundary rules

| Operation | Rule in an async route/service |
|---|---|
| Async Netezza driver operation | Await the driver coroutine and do not wrap it in another thread. |
| SQLite or other synchronous storage | Run it with `await asyncio.to_thread(...)`, or use a bounded dedicated executor. |
| XLSX/XLSB generation | Offload the writer; do not call the synchronous writer directly from `async def`. |
| Small file read/write | Prefer a synchronous FastAPI `def` route or offload it; size does not justify a hidden blocking call. |
| Large Python aggregation | Push it into SQL or a bounded worker; `async def` alone does not make CPU work concurrent. |
| Delay in a coroutine | Use `await asyncio.sleep(...)`, never `time.sleep(...)`. |
| Background task | Retain the task reference, cancel it and await cancellation during shutdown. |

The current implementation applies this boundary in
`app/repositories/sqlite_snapshot.py` and
`app/services/export_service.py` with `asyncio.to_thread`. The seed script is
a synchronous CLI and is not part of the server event loop.

## Choosing `asyncio.to_thread`

`asyncio.to_thread()` is useful for short, blocking operations that cannot be
made asynchronous. It uses the event loop's worker executor. A production
service with many simultaneous exports or refreshes should use a bounded
executor, queue, or separate worker service so that the number of concurrent
disk/CPU jobs is controlled. Preserve cancellation and cleanup behaviour: a
cancelled coroutine cannot necessarily stop a synchronous function that has
already started in a worker.

For CPU-heavy reporting, thread offload may not improve throughput because of
the Python interpreter's execution model. Prefer database-side aggregation,
precomputed marts, process workers or a job queue, then return a job/status
contract if the work is long-running.

## Review checklist for new code

- Search every new `async def` for direct `open`, `Path` I/O, SQLite calls,
  spreadsheet writers, `time.sleep` and long loops.
- Confirm each blocking operation is either offloaded or intentionally moved
  to a synchronous FastAPI route.
- Add a concurrency/latency test that runs a lightweight request while the
  slow operation is in progress.
- Ensure background tasks are cancelled and awaited by the lifespan owner.
- Test cancellation and temporary-file cleanup for exports.
