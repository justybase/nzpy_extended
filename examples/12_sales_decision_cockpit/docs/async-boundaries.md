# Async boundaries and blocking work

This example uses FastAPI and `async def`, but `async def` is not a promise that
the body is non-blocking. The event loop is released only at real suspension
points such as an awaited asynchronous driver call. Synchronous work continues
on the event-loop thread.

## The danger

Do not place synchronous export, file, database or long-running calculation
directly in an async endpoint:

```python
@router.get("/export")
async def export() -> dict[str, str]:
    path = write_xlsx_sync()       # blocks all requests in this worker
    return {"path": path}
```

It may appear fine with demo data and a single browser tab. Under concurrent
use, one large export or refresh can make unrelated requests wait for the
whole operation.

## Rules for extending this example

| Work | Correct async boundary |
|---|---|
| Async Netezza access | Await the driver operation directly. |
| XLSX/XLSB generation | Use the async wrapper and keep the synchronous writer behind `asyncio.to_thread`. |
| Synchronous file or local storage access | Offload it with `asyncio.to_thread`, use a bounded executor, or make the FastAPI route synchronous. |
| Large Python report calculation | Push aggregation into the mart/SQL or use a bounded process/worker; `async def` does not make CPU work non-blocking. |
| Delays | Use `await asyncio.sleep(...)`; never call `time.sleep(...)` in an async path. |
| Background refresh tasks | Keep a task reference, cancel it and await it during application shutdown. |

The current export path is deliberately split: `export_report()` and
`export_drivers()` are async, while `write_workbook()` is synchronous and is
called through `write_workbook_async()`. Keep this separation when adding new
formats or writers. The synchronous `seed.py` CLI does not run in the server's
event loop.

## Worker-pool cautions

`asyncio.to_thread()` is appropriate for short blocking work that cannot be
made asynchronous, but its executor still has finite resources. A production
deployment with many simultaneous exports needs a bounded executor, queue or
separate worker service. Cancellation of the awaiting coroutine does not
necessarily stop synchronous work that has already started in a worker, so
temporary files and other resources must still be cleaned up.

For large datasets, prefer SQL aggregation or precomputed weekly marts. A
thread does not make CPU-heavy Python aggregation scale indefinitely, and an
unbounded number of worker jobs can exhaust memory or disk.

## Review checklist

- Inspect every new `async def` for direct synchronous I/O and long loops.
- Verify every blocking call is offloaded or intentionally in a synchronous
  route.
- Add a concurrency test that proves a lightweight request remains responsive
  while the slow path runs.
- Cancel and await any task created by the application lifespan.
- Test export cancellation and temporary-file cleanup.
