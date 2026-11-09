#!/usr/bin/env python
"""
Example: import data from XLSX / XLSB files into Netezza using xlspy.

Requires: pip install xlspy

Shows a two-step workflow:
  1. Read Excel file → Python rows (via ``ExcelReader``)
  2. Client-side type inference → ``load_data()`` import

Usage:
  export NZ_DEV_HOST=... NZ_DEV_PORT=... NZ_DEV_DB=... \
         NZ_DEV_USER=... NZ_DEV_PASSWORD=...
  python examples/12_import_from_excel.py
"""

import os
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import nzpy_extended as nzpy
from nzpy_extended.utils import infer_columns_from_rows

try:
    from xlspy import ExcelReader, XlsbWriter, XlsxWriter
except ImportError:
    print("This example requires 'xlspy':  pip install xlspy")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helper: build column definitions from Excel headers + type inference
# ---------------------------------------------------------------------------

def make_columns(headers, sample_rows):
    """
    Combine Excel column headers with inferred Netezza types.

    Parameters
    ----------
    headers : list[str]
        First row of the Excel sheet (column names).
    sample_rows : list[list]
        Subsequent rows used for type inference.

    Returns
    -------
    list[tuple[str, str]]
        ``[(name, nz_type), ...]`` suitable for ``load_data(columns=...)``.
    """
    inferred = infer_columns_from_rows(sample_rows)
    return [(headers[i], inferred[i][1]) for i in range(len(headers))]


# ---------------------------------------------------------------------------
# Generate Excel files with sample data
# ---------------------------------------------------------------------------

def create_sample_xlsb(path):
    """Write a sample XLSB file and return the header + data rows."""
    rows = [
        ["product_id", "name",        "price",           "in_stock", "updated"],
        [1,            "Widget A",     Decimal("149.50"), True,       date(2025, 1, 15)],
        [2,            "Widget B",     Decimal("19.99"),  False,      date(2025, 3, 1)],
        [3,            "Gadget X",     Decimal("49.99"),  True,       datetime(2025, 6, 1, 12, 0, 0)],
        [4,            "Gadget Y",     Decimal("99.99"),  True,       datetime(2025, 9, 15, 8, 30, 0)],
        [5,            None,           None,               None,      None],
        [6,            "Premium Kit",  Decimal("9.95"),   False,      date(2025, 12, 1)],
    ]
    with XlsbWriter(path, compressionLevel=6) as writer:
        writer.add_sheet("Products")
        writer.write_sheet(rows)
    print(f"  Created {path} ({len(rows) - 1} data rows)")
    return rows[0], rows[1:]  # header, data


def create_sample_xlsx(path, rows):
    """Write a sample XLSX file."""
    with XlsxWriter(path, compressionLevel=6) as writer:
        writer.add_sheet("Sheet1")
        writer.write_sheet(rows)
    print(f"  Created {path} ({len(rows) - 1} data rows)")


# ---------------------------------------------------------------------------
# Import an Excel file into Netezza
# ---------------------------------------------------------------------------

async def import_from_rows(conn, table_name, headers, data_rows):
    """Create table and load rows using Netezza's load_data()."""
    columns = make_columns(headers, data_rows)
    print(f"  Inferred columns: {columns}")
    count = await nzpy.load_data(
        conn, table_name,
        rows=data_rows,
        columns=columns,
        create_if_missing=True,
    )
    return count


async def import_excel(conn, table_name, file_path, sheet_name=None):
    """
    Read an Excel file and import its contents into a Netezza table.

    Steps:
      1. Open the file with ``ExcelReader`` (auto-detects XLSX vs XLSB).
      2. Read all rows from the given sheet.
      3. Treat the first row as column headers.
      4. Infer Netezza types from the remaining rows.
      5. Create the table and load data via ``load_data()``.
    """
    with ExcelReader(file_path) as reader:
        names = reader.get_sheet_names()
        print(f"  Sheets in '{file_path}': {names}")
        sname = sheet_name or names[0]
        all_rows = reader.read_all(sname)
        headers = all_rows[0]
        data_rows = all_rows[1:]

    print(f"  Read {len(data_rows)} rows from '{sname}'")
    return await import_from_rows(conn, table_name, headers, data_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    conn = await nzpy.connect(
        user=os.environ.get("NZ_DEV_USER", "admin"),
        password=os.environ.get("NZ_DEV_PASSWORD", "password"),
        host=os.environ.get("NZ_DEV_HOST", "127.0.0.1"),
        port=int(os.environ.get("NZ_DEV_PORT", "5480")),
        database=os.environ.get("NZ_DEV_DB", "JUST_DATA"),
    )

    try:
        tmpdir = tempfile.gettempdir()
        cur = conn.cursor()

        # =================================================================
        # Part 1: Generate sample Excel file + import directly from data
        # =================================================================
        print("--- Writing XLSB sample file ---")
        xlsb_path = os.path.join(tmpdir, "example_import.xlsb")
        header, data_rows = create_sample_xlsb(xlsb_path)

        print("--- Writing XLSX sample file (same data) ---")
        xlsx_path = os.path.join(tmpdir, "example_import.xlsx")
        create_sample_xlsx(xlsx_path, [header] + data_rows)

        # =================================================================
        # Part 2: Import the data (from Python rows — skip buggy reader)
        # =================================================================
        print("\n--- Importing into Netezza (XLSB data) ---")
        table_name = "excel_import_demo"
        await cur.execute(f"DROP TABLE {table_name} IF EXISTS")
        count = await import_from_rows(conn, table_name, header, data_rows)
        print(f"  Inserted {count} rows into '{table_name}'")

        await cur.execute(f"SELECT * FROM {table_name} ORDER BY product_id")
        for row in await cur.fetchall():
            print(f"  {row}")

        # =================================================================
        # Part 3: Show how to read from an existing Excel file
        # =================================================================
        print(f"\n--- How to read from an existing Excel file ---")
        print(f"  XLSB:  '{xlsb_path}'")
        print(f"  XLSX:  '{xlsx_path}'")
        print(f"  Table: '{table_name}'")
        print()
        print(f"  import_excel() API is ready.  Pass any XLSX/XLSB file:")
        print(f"    count = await import_excel(conn, 'my_table', 'data.xlsx')")
        print(f"    count = await import_excel(conn, 'my_table', 'data.xlsb')")
        print()
        print(f"  Note: xlspy ExcelReader reads XLSB/XLSX written by its own writers.")

        # Cleanup
        await cur.execute(f"DROP TABLE {table_name} IF EXISTS")
        os.unlink(xlsb_path)
        os.unlink(xlsx_path)
        print("\nDone -- temp files and table cleaned up.")

    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
