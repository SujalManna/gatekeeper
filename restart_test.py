"""Kill test: run this, press Ctrl+C during step 2, then run it again.
Step 1 should show [skip], and the 'clear vendor' action should never run twice.
"""
import time
import store

JOB = "restart-demo"
store.create_job(JOB, {"vendor": "demo"})

store.run_step(JOB, "step1_identify", lambda: (time.sleep(2), {"ok": 1})[1])
store.run_step(JOB, "step2_slow_check", lambda: (time.sleep(8), {"ok": 2})[1])

store.request_approval(JOB, "mark_vendor_cleared")
print("approval status:", store.approval_status(JOB, "mark_vendor_cleared"))
print("Done. Run again: every step should say [skip].")
