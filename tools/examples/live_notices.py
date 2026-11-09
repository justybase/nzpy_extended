import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import nzpy

def on_notice_received(notice_message):
    """
    Callback function that gets triggered immediately 
    when the Netezza backend sends a NOTICE packet over TCP.
    """
    print(f"\n[LIVE NOTICE] The database reported: {notice_message}")

def main():
    # Establish connection to the Netezza database
    conn = nzpy.connect(
        user="admin", password="password",
        host="127.0.0.1", port=5480, database="JUST_DATA",
        securityLevel=0, logLevel=0
    )

    print("Connected successfully. Preparing to execute...")

    with conn.cursor() as cur:
        # Attach our custom callback handler to the cursor.
        # This acts similarly to EventEmitter in Node.js.
        cur.notice_handler = on_notice_received
        
        # Executing a command that naturally yields notices.
        # Although execute() is a blocking call, the driver's internal loop
        # will intercept NOTICE packets on the fly and invoke our callback instantly,
        # long before the execution block finishes.
        print("Executing a procedure or heavy query...")
        
        try:
            # Replace 'CALL SOME_PROC()' with a real procedure or query 
            # in your database that generates raises/notices over time.
            cur.execute("CALL SOME_PROC()")
            
            # If the procedure returns any rows, we can fetch them here.
            # cur.fetchall()
            
        except Exception as e:
            # Catching exceptions in case SOME_PROC does not exist in your DB.
            print(f"Execution finished (Status/Error: {e})")
            
        print("Done. All real-time notices should have been printed above.")

if __name__ == "__main__":
    main()
