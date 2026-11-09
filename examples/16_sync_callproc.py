import sys
sys.path.insert(0, '.')
import nzpy_extended.sync as nzpy


def main():
    conn = nzpy.connect(
        user="admin",
        password="password",
        host="127.0.0.1",
        port=5480,
        database="JUST_DATA",
    )
    try:
        cur = conn.cursor()

        # --- Create a stored procedure ---
        cur.execute("""
            CREATE OR REPLACE PROCEDURE demo_add(INTEGER, INTEGER)
            RETURNS INTEGER
            EXECUTE AS OWNER
            LANGUAGE NZPLSQL AS
            BEGIN_PROC

            BEGIN
                RETURN $1 + $2;
            END;

            END_PROC;
        """)

        # --- Call procedure with parameters ---
        result = cur.callproc("demo_add", [10, 20])
        print(f"callproc returned: {result}")
        rows = cur.fetchall()
        print(f"Procedure result: {rows}")

        # --- Call procedure without parameters ---
        cur.execute("""
            CREATE OR REPLACE PROCEDURE demo_hello()
            RETURNS INTEGER
            EXECUTE AS OWNER
            LANGUAGE NZPLSQL AS
            BEGIN_PROC

            BEGIN
                RETURN 0;
            END;

            END_PROC;
        """)
        result = cur.callproc("demo_hello")
        print(f"callproc (no params) returned: {result}")

        # --- Procedure with RAISE NOTICE (server messages) ---
        cur.execute("""
            CREATE OR REPLACE PROCEDURE demo_notice(INTEGER)
            RETURNS INTEGER
            EXECUTE AS OWNER
            LANGUAGE NZPLSQL AS
            BEGIN_PROC

            BEGIN
                RAISE NOTICE 'Processing value %', $1;
                RETURN $1;
            END;

            END_PROC;
        """)
        cur.callproc("demo_notice", [99])
        print("Server notices (cur.messages):")
        for msg in cur.messages:
            print(f"  {msg}")

        # --- Cleanup ---
        for proc in ["demo_add", "demo_hello", "demo_notice"]:
            try:
                cur.execute(f"DROP PROCEDURE {proc}(INTEGER)")
            except Exception:
                pass
        try:
            cur.execute("DROP PROCEDURE demo_hello()")
        except Exception:
            pass

    finally:
        conn.close()


if __name__ == "__main__":
    main()
