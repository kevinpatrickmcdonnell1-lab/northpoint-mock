# Demo script: personas (real set, R01–R10)

The same ten packets as [demo_script.md](demo_script.md), told as a relay between people. You switch **Acting as** (top right) and each person works their own list. The story is: the system catches the exception, routes it to one owner, that owner settles it against the real authority, and every step is attributed.

**Every ID, list and message below comes from running these steps.** `tests/test_demo_script_personas.py` runs the same sequence over HTTP with the persona cookie, in this order, and checks that this file states each outcome. If you change a step, change the test too.

**Before the session:** `make test`, then `make demo PORT=8010` (port 8000 is often taken). Press **Reset demo**, then **Run all 10 through engine**. Do the steps below in order: event and handoff IDs depend on it. Rehearse once in the browser.

Everything shown is **SYNTHETIC – MOCK DATA**. Payer, clinician and identity decisions and E&B responses are simulated events. Every downstream system is a stub.

| Persona | Role | What they own |
|---|---|---|
| Ana Petrov | Operations Manager | Read-only. Bottlenecks by owner, site and reason |
| Mara Quill, RN | Clinical Triage RN | Urgent and undetermined priority |
| Theo Lund, RN | Clinical Review RN | Expedited and routine clinical review |
| Dr. Ines Varga | Cardiologist | Clinical decisions on escalated orders |
| Jo Rivera | Intake Coordinator | Identity, coverage, missing documents |
| Sam Okoro | Auth Specialist | PA packets, payer decisions, not-met orders |
| Lee Hart | Scheduler | Orders that pass every gate, and recalls |
| Rui Castell | QA Auditor | Read-only. Every fact, decision and handoff |

---

## 1. Ops: where the work sits (3 min) · *Ana Petrov*
- Act as **Ana Petrov**. She lands on **/metrics**. Baseline: **0 sendable, 2 ready for PA prep, 8 held**.
- Open **/work**: "This role is read-only". "Ops sees the bottleneck. Ops doesn't touch the case."
- Back on the queue, the owner tabs show the routing: Clinical Triage has **R08, R07**, Clinical Review has **R03, R05, R06, R10**.
- Say it: "One engine evaluated all ten. Every case has a next action and exactly one owner for each action."

## 2. Triage: urgent never waits on insurance (4 min) · *Mara Quill, RN*
- Act as **Mara Quill**. Her list is **R08, R07**: urgent first, then undetermined.
- Open **R07**. The panel shows only the signed clinical sources ("2 consistent signed sources dated 2026-09-30", urgent review requested) and says *Payer, coverage and PA are not shown here*. "The triage nurse never sees insurance, so insurance can't slow her down."
- Back on **My work**, click **Notify triage** on R07 → `HO-R07-TRIAGE-1` to the Epic In Basket stub.

## 3. Auth: PA starts today, in parallel (5 min) · *Sam Okoro*
- Act as **Sam Okoro**. His list: R07 and R02 (PA ready), R04 and R09 (not met).
- **R07 → Prepare PA packet** → `HO-R07-PA-1`. "R07 is still on a clinical hold, and PA prep goes ahead anyway. Clinical holds never block PA prep."
- **R02 → Prepare PA packet** → `HO-R02-PA-1`. The panel now asks for the payer's decision.
- Try **Approved** with an empty reference: *Not recorded: a payer decision needs the payer reference*. Then enter `SCMA portal ref PA-55120` → `EV-0001`. R02's only remaining action is **SCHED_READY**, so it moves to the Scheduler.

## 4. Scheduler: only ready orders, and hard gates (4 min) · *Lee Hart*
- Act as **Lee Hart**. The list has one item: **R02**. **Send to scheduling** → `HO-R02-SCHED-1`. Click **Send to scheduling** again on the case → *already recorded*, so no second handoff.
- Guardrail 1: in the address bar, go to `/work/R01/ID_CONFLICT` → *R01 ID_CONFLICT is not on your list (Scheduler)*.
- Guardrail 2: open **/case/R01** and press **Send to scheduling** → **BLOCKED**, failing gates **G-ID, G-COV, G-EVID, G-PA-APPROVED**. That attempt is audited. "Even when someone goes looking for a case, nothing reaches scheduling with a failing gate."

## 5. Intake: the R01 identity handoff (8 min) · *Jo Rivera*
The centrepiece. R01 has two DOBs: **1958-06-12** (referral and order p.2, ECG p.4, note p.5) vs **1958-06-21** (face sheet p.3, card p.9). Name and member ID agree everywhere, so this is a transposed day, not a wrong patient.
- Act as **Jo Rivera**. R01 sits near the bottom (routine cases come after urgent and expedited). It has four rows: identity, coverage and two missing documents. Her list also includes fact-check rows (`FACT_…`), which you can skip for now.
- On the **ID_CONFLICT** row click **Create identity task** → `HO-R01-ID-1` to the Epic intake work-queue stub. Open it in the Outbox: both values, each with its page. "Intake gets the question with the evidence attached."
- Open the item, then press **Claim**. "Now everyone can see who owns it."
- The panel shows each value next to its page image. *Nothing is preselected: check Epic and choose.*
- Pick **1958-06-12**, enter Epic reference `EPIC-REG-R01`, leave the note empty and submit → *Not recorded: … needs confirmed values, epic_reference and a note*. "You can't settle identity without saying how you know."
- Add the note: "Confirmed DOB 06/12/1958 in Epic and with patient; face sheet and card transposed" → `EV-0002`. Identity is **verified**, and the holds drop to **G-COV, G-EVID, G-PA-APPROVED**.
- Talk track for the payer: "The card also says 06-21, so the payer's member record is probably wrong. In real life Intake asks the payer to fix it, or the PA won't match. That step isn't modelled here. It's a pilot item."
- The panel moves on to her next case. Go back to **My work** and open R01's **COV_PENDING_NONE**. Log the E&B response: date 2026-10-01, Summit Crest, Summit Crest Medicare Advantage, member **SCMA-SYN-71043**, active **true**, note "E&B rerun with confirmed DOB" → `EV-0003`. Coverage is verified, and the holds drop to **G-EVID, G-PA-APPROVED**.
- Open **EVID_MISSING_ORDER** → **Send request** → `HO-R01-INFO-1`. One fax covers both gaps: the *corrected signed stress echo order naming the indication* and the prior stress echo history.
- R01 moves to **Waiting on others**. "Three of Jo's four items are done. The case is still held, honestly, because the order still doesn't name an indication. Fixing identity didn't let it skip ahead."

## 6. Clinical clearance, then a reversal (6 min) · *Theo Lund → Lee Hart → Dr. Varga → Lee Hart*
- **Theo Lund**: open R05 (expedited, PPO, no PA). Choose **Cleared for scheduling**, note "Reviewed note and ECG" → `EV-0004`.
- **Lee Hart**: R05 is now on the list. **Send to scheduling** → `HO-R05-SCHED-1`.
- **Dr. Varga**: R05 is no longer on her list, because cleared cases leave the clinical queue. Open **/case/R05**, go to **Simulate**, choose clinical decision **not_cleared**, note "Wants a resting echo first" → `EV-0005`. The flash says the scheduling handoff was *recalled automatically (HO-R05-RECALL-1)*.
- **Lee Hart**: **Recalls** shows *Release the slot*, recalls `HO-R05-SCHED-1` by `HO-R05-RECALL-1`. "When an order stops being ready, it's pulled back once, with the reason, and the Scheduler hears about it."

## 7. Auth: "not met" goes back to the clinician (2 min) · *Sam Okoro*
- Open **R04** → **Send for review** → `HO-R04-REVIEW-1`. The payload says "It is not a payer denial" and names QUAL-1 with its page-5 quote (a nonexertional complaint). "We don't send the payer a packet we already know falls short."

## 8. Audit: who did what (3 min) · *Rui Castell*
- Act as **Rui Castell** and open **/case/R01/timeline**. Read the sequence in order: packet loaded → engine evaluated → *Handoff blocked* by Lee Hart (Scheduler) → `HO-R01-ID-1` sent by Jo Rivera → claimed → `EV-0002` IDENTITY_RESOLUTION with her note → identity *conflicting → verified* → E&B → request.
- "The server attributes every entry from who's signed in, not from anything typed in the form."

## 9. Ops: what changed (2 min) · *Ana Petrov*
- **/metrics**: **8 held, 1 handed to scheduling, 1 ready for PA prep** (R07). **0 gate leaks**, **1 scheduling recall**, **1 blocked attempt** (Lee Hart on R01), **1 urgent triage notification**.
- Close: "Each person worked only their own list, every handoff carried its evidence, and nothing passed a gate it shouldn't have."

---

### If you're short on time
Do sections 1, 4 (guardrail 2 only), 5 and 8, in that order. That's the identity handoff with its proof, in about 15 minutes.

### Things not to do live
- Don't flip flags A1/A2 (see [demo_script.md](demo_script.md#a-note-on-flipping-a1a2)).
- Don't skip ahead in Intake before Section 4: the Scheduler's blocked attempt on R01 needs R01's holds still in place.
