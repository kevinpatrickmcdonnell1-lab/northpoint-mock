"""Fresh DB: load source facts and evaluate all ten through the engine (audited)."""
from app.service import Service

if __name__ == "__main__":
    svc = Service()
    svc.seed()
    states = svc.states()
    print(f"seeded {len(states)} cases: "
          f"{sum(s.scheduling_sendable for s in states)} sendable, "
          f"{sum(s.pa_prep_ready for s in states)} PA-prep ready, "
          f"{sum(not s.scheduling_sendable and not s.pa_prep_ready for s in states)} held")
