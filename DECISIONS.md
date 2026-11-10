# DECISIONS

Choices made where SPEC.md was silent or ambiguous. One line each.

## Data
- Undo stores a JSON snapshot of the planning tables (subjects, units, assessments, availability, overrides, blocks, reviews) in `action_log.inverse_json`; only the latest 30 snapshots are kept.
- Old-schema databases are migrated generically: missing tables are created and missing columns added (compared against `schema.sql`), then `schema_version` is stamped.
- Seed runs only on the very first launch (setting `seeded=1`); deleting every subject later starts onboarding instead of re-seeding.
- Seed review stage for a unit done N days ago = first stage whose cumulative due offset (1, 4, 11, 27, 62 days) is ≥ N.
- "Exam" maps to assessment kind `final` (IELTS exam in the seed, "exam" in chat).

## Scheduler
- The daily subject cap is measured in focus minutes (block durations, reviews of that subject included) against `share × T_d`; a cap never forbids a single block (`max(share × T_d, quantum)`), so very short days stay usable.
- The 20% review cap counts review focus minutes (15 per block); each review block still costs 15 + 5 break of day time.
- A review block pairs up to 2 due units of the same subject; the covered units are encoded in the task text (`units 3 & 4`).
- Scheduled reviews are projected forward as if rated "good" so later stages show up inside the horizon.
- Starvation guard (`STARVATION_GAP_DAYS = 3`): a subject with remaining work that has not appeared for 3 days gets one block before the proportional allocation. Needed for acceptance test 3; pure W-proportional carry alone gives Internet Programming a block only every ~6 days.
- Blocks are emitted largest-remaining-budget first; the "extra block" fill repeats until nothing fits and does not touch the carry; carry is clamped to ±one block.
- The last block of a day has no trailing break, so the fill step may waive one break (the smallest break of the day, which keeps the total ≤ capacity whichever block ends up last).
- HEAVY run rule beats the warm-up rule: if non-HEAVY blocks are too few, review blocks are placed between HEAVY runs instead of first/last; if even that is impossible a HEAVY block is removed from the day (only when a non-HEAVY block exists).
- PRACTICE: when the nearest assessment's range is fully done, practice is all that is scheduled if no unit remains; if units remain, practice takes one block per day inside the last `EXAM_CLOSE_DAYS` days and the rest continues with the next unit (otherwise progress would stall until the exam). PRACTICE blocks carry the last unit number reached so unit numbers stay non-decreasing.
- Skipped blocks keep consuming their time slot on that day, so skipping moves work to later days instead of re-filling the same slot.
- Done/partial blocks subtract their actual minutes (+ break) from the day; locked pending blocks subtract their planned duration and are simulated as completed work.
- New blocks are laid out from the configured session start and step around the time slots of kept blocks; planned times are not tied to the wall clock.
- Feasibility simulates up to 60 days ahead (`FEASIBILITY_MAX_DAYS`) so assessments beyond the 14-day horizon are checked without false alarms; `shortfall_min` = minutes of the assessed range still undone on the assessment date in that simulation, `available_min = needed_min − shortfall_min`.
- Risk option 1 rounds the extra minutes/day up to 10; option 2 orders units by Σ(weight_pct / range size) over upcoming assessments covering them; option 3 reports the upper bound `(0.85 − current share) × T_d` summed over the days before the date, and is executed through an extra tool `set_cap_boost` (stored in setting `cap_boosts`).
- "The regenerated week still covers backlog" (test 9) is checked as: every day of the week is planned, every subject that is behind gets CATCHUP blocks during the week, and every backlog unit is targeted inside the regenerated horizon.

## Tools
- Extra tools beyond the SPEC list: `set_cap_boost` (risk option 3), `dismiss_missing_info` (student has no answer to an onboarding question), and UI-only tools hidden from the LLM (`list_subjects`, `list_reviews`, `get_availability`, `get_settings`, `export_data`, `update_settings`, `reset_demo_data`, `import_data`) so the UI never touches SQL.
- `subject` arguments accept a name, acronym (ML), configured alias or id; `upsert_subject` matches the exact name only (so similar names can be created).
- `set_progress`/`upsert_subject` create missing unit rows when a subject has none yet (expected/done larger than the unit count); once units exist, exceeding them is an error.
- `done_units = N` means exactly units 1..N are done; newly done units get a stage-0 review due tomorrow, un-done units lose theirs.
- `log_study(minutes)` also stores a `done` block for today so the time counts against today's capacity, the KPIs and the streak; `log_study(units)` only marks units done.
- Completing a REVIEW block rates its covered reviews (`rating` argument, default `good`). Completing a future block moves it to today.
- `move_block` locks the moved block (otherwise the regeneration would delete it). `set_day_override` accepts `clear=true`.
- `regenerate()` reconciles instead of delete+insert: identical pending blocks keep their row id, so ids held by the UI/timer/LLM stay valid across unrelated changes.
- Date arguments accept `today` / `tomorrow` besides ISO dates; integer arguments accept numeric strings.
- Undo of `update_settings` restores the previous setting values; `undo_last` itself is logged but not undoable.
- Streak = consecutive days (ending today, or yesterday if nothing is logged yet today) with a done/partial block or a unit finished.
