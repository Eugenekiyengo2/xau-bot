"""Long-running scheduler for a VPS or your own machine. Runs a cycle every
RUN_EVERY_MIN minutes. On a serverless host, use GitHub Actions cron instead
(see .github/workflows/schedule.yml) and run run_once.py."""
import os
import time
import traceback

import run_once

EVERY = int(os.getenv("RUN_EVERY_MIN", 15)) * 60

if __name__ == "__main__":
    print(f"XAU bot loop started — every {EVERY // 60} min. Ctrl-C to stop.")
    while True:
        try:
            run_once.main()
        except SystemExit:
            pass
        except Exception:  # noqa
            traceback.print_exc()
        time.sleep(EVERY)
