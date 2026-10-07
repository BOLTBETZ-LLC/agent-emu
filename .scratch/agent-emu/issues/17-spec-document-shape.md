# Spec document shape

Type: grilling
Status: resolved
Blocked by:

## Question

Where does the final build-ready spec live, who reads it (builder agents, Aaron), and what sections must it have so a builder can start without asking anything?

## Answer

Aaron, 2026-10-07: "i dont care as long as its the best for agents to work with and move as fast as possible and not do anything unnecessary or do massive tests. critical paths only".

- **Location:** `.scratch/agent-emu/spec.md`, the tracker's spec convention, so agents find it next to the map and tickets.
- **Reader:** builder agents. No prose for humans beyond a 10-line summary at the top.
- **Shape:**
  1. Summary.
  2. Decisions table: each row links to the ticket that holds the detail.
  3. Architecture: processes, VMM fork changes, guest image, kernel config.
  4. Build order: critical path only, each step with a concrete exit check.
  5. Per-step acceptance checks.
  6. Known traps.
  7. Out of scope.
- **Tests:** only the checks on the critical path. These are boot to first screen, ≤400 MB unique per Device, 10 at once, and round trip under 50 ms, plus a narrow check per step. No broad test suites, no extra audits, no polish passes.
- **Every step names its exit check and stops there.** Nothing optional sits on the critical path.
