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

## Offline coach
- "Weekdays" means Sun–Thu and "weekend" Fri–Sat (as in the seed data); days named explicitly in the same sentence are excluded from the group.
- "next thursday" / "on thursday" = the first Thursday strictly after today.
- A generic word ("exam", "test", "امتحان") updates the subject's only upcoming midterm/final if there is exactly one, otherwise adds a `final`; a specific kind ("midterm", "quiz") updates an upcoming assessment of that kind or adds one.
- "N behind in X" sets `expected_units_by_now = done + N` (capped at the unit count).
- "I couldn't study today, rebalance" → `set_day_override(today, 0)` then `regenerate_plan`.
- Onboarding also asks for weekly availability when every weekday has 0 minutes (order: subjects → availability → units → assessment → expected). The offline interview keeps one pending question in memory and reads short answers ("12", "in 10 days", "none", "7 expected, 3 done").

## LLM coach
- Anthropic calls use the Messages API through the official SDK with `output_config.effort = "low"` (chat-sized work that must fit the 30 s timeout), no sampling parameters, thinking left at the model default, `max_tokens = 8000`, `max_retries = 1`. If a (user-configured, older) model rejects the effort setting the call is retried once without it.
- Assistant turns inside a tool loop are sent back to Anthropic unchanged (thinking blocks must not be edited); all tool results of one turn go into a single user message.
- The stable system prompt is a cached prefix (`cache_control`); the volatile state snapshot and summary go in a second system block after it.
- A model refusal (`stop_reason = "refusal"`) is treated like a provider error: one-line notice, then the offline engine answers. Server-side refusal fallbacks are not enabled because model names are free-form settings and the beta parameter is not valid for every model.
- OpenAI-compatible endpoints use Chat Completions with function tools; no `max_tokens` is sent (parameter names differ between vendors). Without a key but with a base URL (Ollama) a placeholder key is used.
- The daily call cap counts every model call (chat rounds, proactive messages, summaries). When it is reached chat falls back to the offline engine (notice once per day) and proactive messages use templates.
- An authentication error switches the coach to offline for the rest of the session (notice once); network/rate errors only affect the current message.
- If a provider error happens after tools already ran in the same turn, the turn is not replayed offline (that would apply the change twice); the executed actions are reported instead.
- The LLM cannot pass `confirmed=true` itself and cannot call UI-only tools; confirmation only comes from the student's click on the card.
- Proactive rules: `plan_changed` speaks only for UI-initiated changes that are not block/review actions (chat replies and block cards already explain themselves), debounced 3 s with diffs merged. `block_finished`/`block_skipped` speak for UI actions only. Risk options, skip questions, review nudges and break tips are templates (exact numbers, no model call); briefing, plan-change note, session recap, onboarding opener and end-of-day check-in use the background model when available.
- Proactive levels: `light` = onboarding, briefing, risks, missed/skipped blocks, end-of-day; `full` adds plan-change notes, block lines/recap, review nudge, break tips; `off` = silent.
- "Too hard" lowers mastery by 1 (min 1); "Sick" offers a one-click "Clear today"; all skip answers re-run the plan and report what moved.
- Summarisation marker is stored as setting `summarised_upto_id` (the `chat_messages` table has no such column).
- End-of-day on close: the first close of a day without a check-in posts the check-in and keeps the window open; the second close exits.
- Tool schemas sent to providers are simplified copies (no `format` keyword, no union types) because some OpenAI-compatible servers reject them; the executor's own validation still accepts ids for subjects, weekday names, `today`/`tomorrow`.
- `undo_last` targets the latest student-visible action: actions logged by the app itself (source `system`, e.g. the start-up re-plan) and `lock_block` are not undo targets; restoring the snapshot also rolls back whatever was logged after the target.

## GUI
- Background threads never touch widgets: they post callables to a queue that the main loop drains every 40 ms via `after()`.
- The visible view refreshes on `state_changed`; the other views refresh when they are shown (same result, less work). Lists are only rebuilt when their data actually changed.
- **Start** on a block locks it first, so a re-plan triggered elsewhere cannot remove the block being timed. **Finish** ends the focus phase early and still asks Done / Partial / Didn't study.
- Destructive UI actions (delete subject, reset to demo data, import) use the same Confirm/Cancel card in the coach panel as the chat; large payloads (import) are kept in memory and referenced by a token.
- Only the newest coach message has active option/confirm buttons; older ones are disabled. Undo is shown on the latest message that executed a write.
- Settings are applied with **Save settings**; entering the Settings view reloads the stored values. **Save key** and **Test connection** save the settings first.
- The Week board shows the calendar week that contains today (starting on `week_start`), including past days of that week.
- After a subject is added or edited in the UI, the coach asks for the most important missing piece (once per question), and the answer continues the intake interview.
- No screenshots/icons are bundled; the window uses the default Tk icon.
