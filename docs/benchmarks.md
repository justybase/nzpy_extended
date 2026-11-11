# Benchmarks

Measured on 2026-10-31 with nzpy_extended 0.8.0 at commit `bd9c406`, against the same Netezza appliance from two machines:

- **Mac mini** (Apple M4), over the local network;
- **WSL2** (Linux x86_64 on an AMD Ryzen 7 7840HS).

Each script was run once per machine, in sequence, with nothing else running against the appliance. Run-to-run variation is typically 5–15 %, so treat differences below that as noise.

## Environment

| | Mac mini | WSL2 |
|---|---|---|
| CPU | Apple M4, 10 cores | AMD Ryzen 7 7840HS, 16 logical CPUs |
| Memory | 16 GB | 22 GB |
| OS | macOS 27.2 (arm64) | WSL2, Linux 6.18 (x86_64) |
| Python | 3.14.10 (Homebrew) | 3.14.4 |
| Official `nzpy` | 1.17.8 | 1.13.2 |
| `pyodbc` | not installed (`N/A` below) | installed |
| Native decoder | loaded (`rust_ext`, Rust) | loaded (`rust_ext`, Rust) |

## Result rows per second, data types (`performance_test.py`)

100,000 rows per query, one run per cell. `nzpy_extended` columns are the Rust decoder (`native`) and the pure-Python decoder (`nonative`), each for the async and the sync API.

### Mac mini (Apple M4)

| Type | official nzpy | nzpy_extended async | nzpy_extended sync | async, no native | sync, no native |
|---|---|---|---|---|---|
| integer | 43,560 | 705,906 | 655,874 | 212,679 | 212,754 |
| numeric | 22,386 | 159,060 | 181,109 | 91,300 | 92,329 |
| string | 40,557 | 175,695 | 189,102 | 115,586 | 112,027 |
| datetime | 41,645 | 749,538 | 801,777 | 176,497 | 184,456 |
| boolean | 54,437 | 871,447 | 876,912 | 262,630 | 302,236 |
| all types | 11,026 | 90,532 | 89,487 | 47,158 | 46,888 |

### WSL2 (AMD Ryzen 7 7840HS)

| Type | official nzpy | pyodbc | nzpy_extended async | nzpy_extended sync | async, no native | sync, no native |
|---|---|---|---|---|---|---|
| integer | 32,254 | 116,492 | 697,746 | 696,548 | 186,471 | 188,148 |
| numeric | 16,276 | 73,734 | 181,018 | 185,007 | 84,545 | 83,250 |
| string | 31,757 | 91,242 | 189,731 | 187,440 | 104,604 | 105,477 |
| datetime | 30,523 | 140,766 | 871,212 | 642,073 | 148,136 | 152,653 |
| boolean | 40,313 | 226,849 | 743,459 | 915,734 | 250,859 | 244,720 |
| all types | 7,922 | 26,787 | 80,758 | 90,727 | 40,927 | 42,332 |

The string and numeric columns gain less from the native decoder because the server spends most of the time producing the rows, not decoding them.

## Native versus pure Python decoder (`benchmark_native_vs_python.py`)

153,600 mixed-type rows, five interleaved rounds in one process, median of the rounds:

| Machine | Native (Rust) | Pure Python | Pure Python / native |
|---|---|---|---|
| Mac mini (M4) | 0.905 s | 4.112 s | 4.5× |
| WSL2 (Ryzen 7840HS) | 1.418 s | 5.673 s | 4.0× |

## Bulk loading: `load_data` versus `executemany` (`benchmark_load_data.py`)

`executemany` ran 1,000 rows (one `INSERT` per row, the per-row cost is what matters). `load_data` ran 100,000 rows, three rounds each, with the table created by the call, so each time includes `CREATE TABLE`. The table has five columns (`INTEGER`, `VARCHAR(40)`, `NUMERIC(12,2)`, `DATE`, `BOOLEAN`), and every row is checked by a `COUNT(*)` after the load.

| Method | Mac mini (M4) | WSL2 (Ryzen 7840HS) |
|---|---|---|
| `executemany`, per row | 45.3 ms | 44.9 ms |
| `load_data`, list of rows, median of 3 | 1.894 s (52,790 rows/s) | 1.981 s (50,487 rows/s) |
| `load_data`, generator, median of 3 | 1.681 s (59,480 rows/s) | 1.648 s (60,685 rows/s) |
| Per-row speed-up of `load_data` (list) over `executemany` | about 2,400× | about 2,300× |

In these runs the generator was a little faster than the list, and its three rounds were more even. On the Mac the first list round was the slowest of the three (2.6 s against 1.9 s). We did not investigate the cause. Both paths include rendering the rows, which happens inside `load_data`.

## Value deduplication

Each result set keeps one bounded cache per column for immutable decoded
values: text and `CHAR`/`NCHAR` values, `DATE`, `TIME`, `TIMETZ` and
`TIMESTAMP`. `NUMERIC` and timestamps keep their existing value-keyed caches,
which now also share the decoded object. The key is the exact wire bytes, so a
repeated value returns the same Python object instead of decoding and
allocating again. A column that still shows under 2% repetition after 8192
lookups disables its cache for the rest of that result set; the key length and
per-column entry count are bounded.

On a synthetic 100000-row mixed row (INT, TIMESTAMP, NUMERIC(11,6), two
`VARCHAR`s, DATE) the pure-Python decoder took 55.2 ms with the cache and
55.3 ms with the cache bypassed: decode time is neutral, while repeated rows
share strings, `Decimal`s and date/time objects instead of allocating new ones.

The native decoder is fast enough that the cache overhead matters, so it was
measured as an interleaved A/B in one process: 200000 rows of a nine-column
row whose values all repeat. All caches took 35.3-36.6 ms, the pre-existing
NUMERIC/TIMESTAMP caches alone 36.7-39.8 ms, and no caches 67.5-72.1 ms. The
added per-column cache is 4-9% faster than the old caches alone and all caches
are about 48% faster than no caching; the probe counters stop after the 8192
lookup decision, so the decision itself costs nothing per cell.
`tests/test_value_cache_live.py` checks object identity on the live
`PERF.EVENTS_400K` and `ADMIN.FACTPRODUCTINVENTORY` tables in both decoder
modes.

## Reproducing

Set the connection variables (`NZ_DEV_HOST`, `NZ_DEV_PORT`, `NZ_DEV_DATABASE`, `NZ_DEV_USER`, `NZ_DEV_PASSWORD`). Use a disposable database: every script creates uniquely named `ZZ_` tables and drops them afterwards.

```shell
python tools/examples/performance_test.py -o performance_test.txt
python tools/examples/benchmark_native_vs_python.py --doublings 9 --rounds 5 --output native_vs_python.json
python tools/examples/benchmark_load_data.py --rows 100000 --executemany-rows 1000 --rounds 3 --output load_data.json
```

Add `NZPY_EXTENDED_NO_NATIVE=1` to force the pure-Python decoder for the whole run.

## Notes

- `performance_test.py` ran once per machine. The WSL2 table in the README is the median of three runs from an earlier session, so single cells differ from it by the usual run-to-run variation.
- The Mac mini has no `pyodbc` and no ODBC driver, so its ODBC column is `N/A`.
- Both machines used the same appliance over the LAN. We did not profile the differences between them; they are consistent with differences in client CPU and network path, but that is not measured here.
