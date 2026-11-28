"""Fresh DB: load the packets' extracted facts. The engine is NOT run here: the demo starts with
the packets arrived and "Run all 10 through engine" pressed live (that run is audited).
The counts printed below are a terminal-only sanity check; they write nothing."""
from app.service import Service

if __name__ == "__main__":
    svc = Service()
    svc.seed(evaluate=False)
    states = svc.states()   # pure evaluation, nothing recorded
    print(f"loaded {len(states)} packets; engine not run yet. Preview: "
          f"{sum(s.scheduling_sendable for s in states)} sendable, "
          f"{sum(s.pa_prep_ready for s in states)} PA-prep ready, "
          f"{sum(not s.scheduling_sendable and not s.pa_prep_ready for s in states)} held")
