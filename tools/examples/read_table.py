import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import nzpy

conn = nzpy.connect(
    user="admin", password="password",
    host="127.0.0.1", port=5480, database="JUST_DATA",
    securityLevel=0, logLevel=0
)

with conn.cursor() as cur:
    cur.execute("SELECT * FROM JUST_DATA..FACTPRODUCTINVENTORY LIMIT 100000")
    cols = [desc[0] for desc in cur.description]
    header = " | ".join(f"{c:>12}" for c in cols)
    sep = "-" * len(header)
    print(header)
    print(sep)
    while rows := cur.fetchmany(100):
        for row in rows:
            print(" | ".join(f"{str(v):>12}" for v in row))
