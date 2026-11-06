# Phase 3 Implementation Plan: scheduled refresh, weekly discoveries, limited promotion

> **For agentic workers:** execute task by task (one PR per task group), TDD for every behaviour
> (red → green → refactor), `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
> green before every push. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a weekly, deterministic refresh that proposes swaps for rarely played or often skipped tracks and only applies them after the owner approves in a Claude conversation. Alongside it, a weekly "Discoveries" playlist curated by Claude through a Desktop scheduled task, and capped promotion of the discoveries the owner liked.

**Architecture:** every change waiting for the owner is a `proposal` + `proposal_item` rows (data model decision 6). Scheduled code (`refresh_plan` job) only *reads* Apple and the Music app and writes proposals. Writes to Apple happen in two places only: `apply_proposal`, which needs an approved proposal, and `submit_weekly_discoveries`, which runs from the owner's own scheduled task. A new `proposals.py` holds the lifecycle. `curation/listening.py` turns sparse `play_snapshot` rows into play/skip windows.

**Tech stack:** Python 3.11+, MCP SDK, SQLAlchemy Core + Alembic, respx fake Apple API, launchd.

**Spec:** `docs/decision-guide.md` §7.10, §7.11, §6, §10, §11, §13; `docs/data-model.md` (decisions 3, 6, 7, 8); ADR 0010, ADR 0012; `docs/risk-assessment.md` §2.5, §7.

## Global constraints

- No schema change is needed: `proposal`, `proposal_item.replaces_item_id`, `proposal.notified_at`, `job_run.job = 'refresh_plan'`, the `managed_playlist.refresh_*` and `discoveries_*` columns already exist. Any new table or column is a **GATE** (stop and ask the owner).
- Per-item evidence (plays, skips, why) is stored in `proposal.params["evidence"]`, keyed by item id. `params` is already JSON, so this stays inside the ERD.
- Scheduled jobs: deterministic, no LLM calls, **no Apple writes** (unless `REFRESH_AUTO_APPLY=true`, which defaults to false).
- ADR 0010 stays intact: archive to "<name> · Arquivo" and verify before any removal. Remove only `accepted` items. Never remove loved tracks or recently added ones.
- Edits to managed playlists go through `sync_with_history` with origin `refresh` or `promotion`.
- Apple request volume: catalog reads are cached for 7 days. One refresh plan costs about 30–50 reads per playlist per week. No loops over the whole library through the API.
- English everywhere except `README.pt-BR.md` and the ntfy text the owner reads (Portuguese, like the other alerts).

## Design decisions (safest option for each open point; questions listed at the end)

| Topic | Decision |
|---|---|
| Refresh `plan` and Apple | **Read-only.** The old `plan` also added discoveries to "· Descobertas" (a write). That job moves to Claude's weekly curation (B). |
| Play/skip window | Last **42 days** (`REFRESH_WINDOW_DAYS`) from `play_snapshot` deltas. A minimum of **14 days** of history (`REFRESH_MIN_HISTORY_DAYS`) is required before any rotation is proposed. With less, the proposal has no rotation and says why. |
| "Often skipped" | ≥ 3 skips in the window (`REFRESH_MIN_SKIPS`) **and** skip ratio skips/(plays+skips) ≥ 0.5 (`REFRESH_SKIP_RATIO`). |
| "Rarely played" | 0 plays in the window **while the playlist itself was played** (median plays in the window ≥ 1). If the playlist was not played at all, nothing can be learned, so no rotation is proposed. |
| Never candidates | Loved/favorited (Music app or Apple rating). Added to the playlist in the last 14 days (`REFRESH_MIN_DAYS_IN_PLAYLIST`, from the Music app `date added`, else `playlist_event`, else first snapshot). Tracks without snapshot data. |
| Rotation size | At most 20 % of the playlist (`rotate_fraction`). Skipped tracks come first, then the rarely played. |
| Dislikes | Always proposed for removal with reason `disliked`. This does not count toward the 20 %. |
| Replacements | **One-for-one**: each removal gets an `add` item with `replaces_item_id`. The replacement aims at the removed track's familiarity bucket, so the mix of the playlist does not drift. Pools: `known_tracks` (known_track), `deep_cuts` (known_artist_new_track), `discover` (new_artist), all seeded with the playlist's top artists. Candidates whose genre matches the playlist's genres come first. Excluded: blacklist, dislikes, tracks already in the playlist or the Arquivo, tracks the owner rejected before. If a pool is empty, no replacement is proposed and the playlist shrinks by one; this is reported. |
| Approval | Per item (`accepted`/`rejected` + reason) or all at once. A removal and its replacement are decided separately. Once nothing is pending, the proposal becomes `approved` (≥ 1 accepted) or `rejected`. |
| Expiry | `pending_approval` refresh/promotion proposals expire after **7 days** (`PROPOSAL_TTL_DAYS`). The next run creates a fresh one. |
| Idempotence | One open (pending or approved, not applied) refresh proposal per playlist. A re-run reports "still pending" and creates nothing. |
| Schedule | Default Monday 09:00 `America/Sao_Paulo` (`REFRESH_WEEKDAY`, `REFRESH_TIME`), overridable per playlist through `refresh_schedule` = `{"weekday": "monday", "time": "09:00"}`. The launchd agent fires at every distinct slot. The job creates a proposal for each playlist whose slot this week has passed, which also catches up after the Mac was asleep. |
| Notification | ntfy "Refresh pronto para <playlist>: X saem, Y entram". `notified_at` is set only if delivery succeeded. |
| `REFRESH_AUTO_APPLY` | The code path accepts every item and applies it from the job. Default `false`, documented as not recommended. |
| Weekly playlist name | **"<name> · Weekly Discoveries"** (guide §7.12: names in English). The id is saved in `discoveries_apple_playlist_id`. A legacy "<name> · Descobertas" playlist is reused, not renamed. |
| Discoveries batch | 30 tracks by default (`DISCOVERIES_SIZE`), accepted between 20 and 40. Claude chooses, code validates: catalog ids exist; not blacklisted, disliked, already in the main playlist, the Arquivo or the batch; not already known (`known_track`). Every rejected id comes back with its reason. The proposal is `applied` on submit: the owner's scheduled task is the standing approval. |
| "Liked" (promotion) | Loved/favorited, **or** ≥ 3 plays (`PROMOTE_MIN_PLAYS`) with skip ratio < 0.25 (`PROMOTE_MAX_SKIP_RATIO`) since the batch was added (snapshot deltas from the batch's `applied_at`). |
| Promotion cap | `min(30, floor(0.20 × main size))`, never below 0, per week (`PROMOTE_WEEKLY_FRACTION`, `PROMOTE_WEEKLY_MAX`). Promotions applied in the last 7 days count against it. Ranked: loved first, then plays, then lowest skip ratio. |
| Promotion lifecycle | `refresh_plan` creates a `promotion` proposal (pending) for playlists with discoveries enabled. Apply inserts with `Sequencer.insert`, records `playlist_event` origin `promotion` and removes the track from the weekly playlist. Liked tracks stay in the weekly playlist (not replaced by the next batch) while their promotion is open. |
| Pending playlists | `create` proposals with status `approved` and `params.pending = true` (data model decision 6). `finish` sets them `applied`. |
| Legacy data | One-time import at container start (like `state.json`): `refresh_plans` becomes refresh proposals (`pending_approval` if younger than the TTL, else `expired`, `params.imported_from`). `pending_playlists` becomes approved create proposals. The keys are then removed, so the import never runs twice. |
| `schedule-refresh` | Retired. It removes the old weekly agent if present and points to `update_managed_playlist(refresh_enabled=true)` + `schedule install`. `refresh --playlist` creates a read-only proposal. |

## File structure

| File | Responsibility |
|---|---|
| `src/apple_music_mcp/curation/listening.py` (new) | `window_deltas` (pure) + `ListeningWindow` result: plays/skips gained per match key over a date window from sparse snapshots |
| `src/apple_music_mcp/db/repositories.py` | `ListeningRepository.snapshot_rows(...)`, `history_start()` |
| `src/apple_music_mcp/db/history.py` | `ProposalRepository`: items with `replaces`, `list`, `open_for`, `decide_by_item`, `expire_pending`, `mark_notified`, `item_rows` |
| `src/apple_music_mcp/proposals.py` (new) | `ProposalService`: render, decide, finalize status, expire, apply dispatch per kind |
| `src/apple_music_mcp/refresh.py` | `RefreshService.build_plan` (read-only → refresh proposal), `apply_refresh(proposal)`, the legacy `apply` (manual ids) via history |
| `src/apple_music_mcp/promotion.py` (new) | liked criterion, cap, promotion proposal, apply |
| `src/apple_music_mcp/weekly_discoveries.py` (new) | context for Claude, batch validation, fill the weekly playlist |
| `src/apple_music_mcp/legacy.py` (new) | one-time import of `refresh_plans` and `pending_playlists` |
| `src/apple_music_mcp/operations.py` | `PendingPlaylists` over proposals |
| `src/apple_music_mcp/jobs/refresh_plan.py` (new) | due playlists, expiry, refresh + promotion proposals, ntfy, auto-apply path |
| `src/apple_music_mcp/settings.py`, `.env.example` | new settings |
| `src/apple_music_mcp/commands/jobs.py`, `cli.py`, `commands/refresh.py` | `refresh_plan` agent, retire `schedule-refresh` |
| `src/apple_music_mcp/tools/proposal_tools.py` (new) | `list_pending_proposals`, `show_proposal`, `decide_proposal`, `apply_proposal` |
| `src/apple_music_mcp/tools/discoveries_tools.py` (new) | `weekly_discoveries_context`, `submit_weekly_discoveries` |
| `docs/scheduled-tasks/weekly-discoveries.md` (new) | ready-to-paste Claude Desktop scheduled-task prompt |

---

## Task 1 (PR `docs/phase-3-plan`): this plan

- [x] Write `docs/plans/phase-3.md` with decisions and open questions. No code.

## Task 2 (PR `feat/refresh-proposals`): proposals instead of `refresh_plans`, snapshot windows, legacy import

**Interfaces produced**
```python
# curation/listening.py
@dataclass(frozen=True)
class SnapshotRow: key: str; day: date; played: int; skipped: int; favorited: bool; disliked: bool
@dataclass(frozen=True)
class Delta: plays: int; skips: int; favorited: bool; disliked: bool; first_seen: date
    @property
    def skip_ratio(self) -> float | None  # skips / (plays + skips), None when both are 0
@dataclass
class ListeningWindow: start: date; end: date; history_start: date | None; history_days: int; deltas: dict[str, Delta]
def window_deltas(rows: Iterable[SnapshotRow], start: date, end: date) -> ListeningWindow
# db/repositories.py
ListeningRepository.window(start: date, end: date) -> ListeningWindow   # duplicates of one song add up
# db/history.py
ProposedTrack(..., replaces: int | None = None)   # index of the item it replaces in the same create() call
ProposalRepository.list(*, statuses=(...), kind=None, managed_playlist_id=None) -> list[dict]
ProposalRepository.open_for(kind: str, *, managed_playlist_id: int | None, playlist_id: str | None) -> dict | None
ProposalRepository.decide_by_item(proposal_id: int, decisions: dict[int, tuple[str, str | None]]) -> int
ProposalRepository.expire_pending(kinds: Sequence[str], older_than: datetime) -> list[int]
ProposalRepository.mark_notified(proposal_id: int) -> None
# refresh.py
RefreshService.build_plan(playlist_id: str, settings: RefreshSettings | None = None, *, job_run_id: int | None = None) -> dict  # read-only, returns rendered proposal
RefreshService.apply_refresh(proposal_id: int, *, estimates: list[dict] | None = None, allow_rebuild: bool = False) -> dict
# legacy.py
import_legacy_state(app: AppContainer) -> dict[str, int]
```

- [ ] Red: `tests/test_listening.py`: first snapshot only (history 0 days, deltas 0); a gap between snapshots (the counter before the window start is the baseline); a track that first appears after the baseline (baseline 0); a re-run on the same day (last value wins); two tracks with one match key add up; skip ratio.
- [ ] Green: `curation/listening.py` + `ListeningRepository.window`.
- [ ] Red: `tests/test_refresh.py`, rewritten for proposals: the plan never touches Apple (no new playlist, main unchanged, no staging write); too little history means no rotation and a note; skipped and unplayed tracks are proposed; loved, recently added and unknown tracks never are; dislikes are proposed with reason `disliked`; each removal has a replacement with `replaces_item_id`; replacements exclude the blacklist, dislikes, main, Arquivo and earlier rejects; the one-open-proposal rule holds; `apply_refresh` removes only accepted items, archives and verifies first, records `playlist_event` origin `refresh` + `archived`; a pending item is never applied; `needs_decision` keeps the main playlist and the archive copy.
- [ ] Green: `RefreshService.build_plan` / `apply_refresh`; `apply` (manual ids) records history through `sync_with_history(origin="refresh")`.
- [ ] Red/green: `tests/test_legacy.py`: `refresh_plans` is imported once (recent → pending, old → expired) and the key is removed; a second container start imports nothing.
- [ ] `plan_playlist_refresh` tool returns the proposal. `apply_playlist_refresh` keeps working for ids the owner names directly.

## Task 3 (PR `feat/refresh-plan-job`): scheduled job, ntfy, expiry, launchd, settings

**Interfaces produced**
```python
AppSettings.refresh_weekday: str = "monday"; refresh_time: str = "09:00"; refresh_window_days: int = 42
AppSettings.refresh_min_history_days: int = 14; refresh_min_days_in_playlist: int = 14
AppSettings.refresh_min_skips: int = 3; refresh_skip_ratio: float = 0.5; proposal_ttl_days: int = 7
AppSettings.promote_min_plays: int = 3; promote_max_skip_ratio: float = 0.25
AppSettings.promote_weekly_fraction: float = 0.2; promote_weekly_max: int = 30; discoveries_size: int = 30
jobs/refresh_plan.py: refresh_plan_job(app, run_id, outcome, notifier) -> None
jobs/refresh_plan.py: due_playlists(managed: list[dict], settings, now: datetime, last_created: dict[int, datetime]) -> list[dict]
commands/jobs.py: job_schedules() includes "refresh_plan" with one StartCalendarInterval per distinct slot
```
- [ ] Red: `tests/test_refresh_job.py`: only opted-in, due playlists get a proposal; a same-day re-run is idempotent; old pending proposals expire first; ntfy text "Refresh pronto para X: N saem, M entram" + `notified_at`; delivery failure leaves `notified_at` empty; pause mode skips the job; the auto-apply flag off never writes to Apple; auto-apply on applies (fake); per-playlist schedule overrides the default.
- [ ] Green, plus `.env.example`, `run refresh_plan`, `schedule install` (6 agents), and `schedule-refresh` retired (removes the old agent).

## Task 4 (PR `feat/proposal-tools`): review, approve and apply from Desktop/Dispatch

Tools: `list_pending_proposals(kind?)`, `show_proposal(proposal_id)`, `decide_proposal(proposal_id, accept_item_ids?, reject?: [{item_id, reason}], accept_all?, reject_all?, reason?)`, `apply_proposal(proposal_id, estimates?, allow_rebuild?)`.
- [ ] Red: MCP tests through `Client(build_server(...))` for each tool: full lifecycle pending → approved → applied; partial approval; rejection with reason (also shows up in `start_session.avoid`); applying a non-approved proposal fails; expired proposals are not listed; `refresh_enabled` + schedule fields in `update_managed_playlist`.
- [ ] Green; skill §9 rewritten; `EXPECTED_TOOLS`; zip regenerated.

## Task 5 (PR `feat/weekly-discoveries`): context, batch validation, fill the weekly playlist

Tools: `weekly_discoveries_context(playlist_id)` (read-only: profile, mix, top artists, genres, recent history, dislikes, blacklist, rejects, current weekly playlist, candidate pools from `discover`/`deep_cuts`), and `submit_weekly_discoveries(playlist_id, song_ids, notes?)` (a write).
- [ ] Red: validation drops duplicates, blacklisted, disliked, in-main, known tracks and unknown ids, each with a reason; a batch outside 20–40 after drops is refused with nothing written; the playlist is created once and reused (id saved); liked tracks with an open promotion stay; the proposal kind is `discoveries`, status `applied`; MCP-level test.
- [ ] Green; `docs/scheduled-tasks/weekly-discoveries.md` and the skill section.

## Task 6 (PR `feat/promotion`): liked criterion, weekly cap, promotion proposals

```python
promotion.py: promotion_cap(main_size: int, already_this_week: int, fraction: float = 0.2, maximum: int = 30) -> int
promotion.py: is_liked(delta: Delta | None, loved: bool, min_plays: int, max_skip_ratio: float) -> str | None
PromotionService.build(managed: dict, *, job_run_id: int | None = None) -> dict | None
PromotionService.apply(proposal_id: int, *, estimates=None, allow_rebuild=False) -> dict
```
- [ ] Red: the cap is floor(20 %), never above 30, never below 0, and promotions already applied this week count against it; only liked tracks; ranking; apply inserts keeping the order (`Sequencer.insert`), `playlist_event` origin `promotion`, removes from the weekly playlist; the job builds promotions for discoveries-enabled playlists.
- [ ] Green.

## Task 7 (PR `refactor/pending-proposals`): pending playlists as create proposals

- [ ] Red: an AuthError on create stores an approved `create` proposal with `params.pending`; `finish_pending_playlists` creates it and marks it applied; `discard` sets it `rejected`; the legacy `pending_playlists` key is imported once; `login` counts pending from proposals.
- [ ] Green; the `PENDING_KEY` storage is removed.

## Task 8 (PR `docs/phase-3`): docs and release

- [ ] Skill (and zip), CLAUDE.md, README.md + README.pt-BR.md, audit §11 "Phase 3 delivered", `docs/validation/phase-3-runbook.md`, ADR 0010 note (read-only plan, proposals).
- [ ] Open (do not merge) the `develop → main` release PR.

## Open questions for the owner (answered 2026-11-06)

1. "Liked" for promotion: loved, or ≥ 3 plays with skip ratio < 0.25. **Confirmed.**
2. Rotation thresholds: 42-day window, at least 14 days of history; "skipped" = ≥ 3 skips and ≥ 50 % skips; "rarely played" = 0 plays while the playlist was played. **Confirmed.**
3. Weekly playlist name: "<name> · Weekly Discoveries"; the archive stays "<name> · Arquivo". **Keep both as they are.**
4. One-for-one replacement, or may the playlist shrink/grow? **The refresh removes what the owner stopped enjoying and suggests new tracks; the owner chooses per playlist whether it keeps its size or grows.** Implemented as `refresh_grow_by` (0 = keep size, the default; N ≤ 50 = N extra tracks per refresh, bucketed by the playlist's mix targets), stored in the existing `managed_playlist.refresh_schedule` JSON, so no schema change. `plan_playlist_refresh(grow_by=…)` overrides it for one proposal. Extra items have `why: growth` and no `replaces_item_id`. Rejecting replacements item by item still lets a playlist shrink.
5. Phase 2 leftovers: energy curves (study = focus) and job times (snapshot 23:40, backup 23:50, doctor 08:10, recently played 10:30/22:30). **Confirmed: study equals focus for the owner today, and the times stay.**
