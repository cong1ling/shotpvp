# Gameplay Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete 64×30 ricochet arena with 3×3 tanks, destructible bricks, power-ups, scoring, game-over, and restart.

**Architecture:** Keep deterministic rules in `game.py`, serialize complete authoritative snapshots in `protocol.py`, and let `server.py` own timing/randomness. Clients reconstruct snapshots, predict only legal local movement, and render all multi-cell visuals.

**Tech Stack:** Python standard library, UDP, `struct`, Win32 console input, ANSI rendering, assertion-based tests.

---

### Task 1: Core spatial model and map

**Files:** Modify `game.py`; test `test_logic.py`.

- [ ] Add failing tests for 3×3 occupancy, rotation-independent movement, spawn clearance, and 1×1 small tanks.
- [ ] Run `python test_logic.py` and confirm the new assertions fail.
- [ ] Add tile constants, the 64×30 symmetric map, tank `size`, `occupied_cells()`, `muzzle_position()`, and shared placement helpers.
- [ ] Run `python test_logic.py` and confirm spatial tests pass.

### Task 2: Ricochet projectile rules

**Files:** Modify `game.py`; test `test_logic.py`.

- [ ] Add failing tests for boundary, brick, steel, and corner reflection; brick destruction; self-hit; shield absorption; and disappearance after five bounces.
- [ ] Run the tests and verify failures describe missing bounce behavior.
- [ ] Extend `Bullet` with `bounces`; implement axis reflection and one collision resolution per tick; remove owner immunity after muzzle creation.
- [ ] Run all logic tests and confirm projectile tests pass.

### Task 3: Power-ups and round state

**Files:** Modify `game.py`; test `test_logic.py`.

- [ ] Add tests for shrink pickup/expiry deferral, triple-shot consumption, shield cap, valid item spawning, countdown, first-to-five, and two-player restart.
- [ ] Run the tests and confirm failure.
- [ ] Add `PowerUp`, effect timers, pickup resolution, authoritative spawn helper, and `World` round phase state while preserving a small `step_world` compatibility wrapper only if tests require it.
- [ ] Run all logic tests and confirm pass.

### Task 4: Snapshot protocol v3

**Files:** Modify `protocol.py`, `test_protocol.py`.

- [ ] Add failing round-trip tests containing phase metadata, packed map tiles, extended tanks, bullets, and power-ups; add a truncated-packet rejection test.
- [ ] Run `python test_protocol.py` and confirm failure.
- [ ] Define protocol version 2 structs and complete encode/decode validation for the authoritative snapshot.
- [ ] Run `python test_protocol.py` and confirm pass.

### Task 5: Authoritative server integration

**Files:** Modify `server.py`.

- [ ] Replace parallel grid/tanks/bullets variables with one authoritative `World` instance.
- [ ] Route inputs and restart requests into world actions; seed a dedicated `random.Random`; broadcast protocol v3 state.
- [ ] Run `python -m py_compile server.py` and a short noninteractive server smoke check.

### Task 6: Client reconstruction and input

**Files:** Modify `client.py`.

- [ ] Reconstruct extended world snapshots including map changes and items.
- [ ] Update local prediction to validate the full current tank footprint and disable actions outside active play.
- [ ] Send fire as restart intent during game-over; preserve RTT and interpolation.
- [ ] Run `python -m py_compile client.py`.

### Task 7: Multi-cell renderer

**Files:** Modify `renderer.py`.

- [ ] Add pure buffer tests in `test_renderer.py` for four 3×3 tank orientations, one-cell small tanks, muzzle barrels, both wall types, and each item glyph.
- [ ] Refactor buffer construction into a pure method and render Unicode tank bodies, non-colliding barrels, bullets, items, countdown, winner, effects, and scores.
- [ ] Run `python test_renderer.py` and confirm pass.

### Task 8: Documentation and full verification

**Files:** Modify `README.md`, `AGENTS.md` only where current facts changed.

- [ ] Document new controls, rules, glyph legend, map size, restart flow, and minimum terminal size.
- [ ] Run `python test_logic.py`, `python test_protocol.py`, `python test_renderer.py`, and `python -m py_compile *.py`.
- [ ] Start one server and two Windows clients; verify countdown, pickup effects, brick destruction, all ricochets, self-kill, win, restart, RTT, and disconnect HUD.

> Note: this directory is not a Git repository, so commit steps are intentionally omitted.
