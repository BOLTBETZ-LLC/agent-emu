---
name: agent-emu
description: Control panel for a Fleet of up to eight Android Devices, laid out as an assay plate on a graphite bench.
colors:
  graphite-0: "#09090a"
  graphite-1: "#101112"
  graphite-2: "#161719"
  graphite-3: "#1e1f22"
  graphite-4: "#2a2b2e"
  graphite-5: "#3a3b3f"
  graphite-6: "#5f6166"
  graphite-7: "#95979c"
  graphite-8: "#c4c6ca"
  graphite-9: "#eeeff0"
  reagent-teal: "#3fd6b4"
  reagent-teal-ink: "#032a21"
  reagent-amber: "#f4b73f"
  reagent-amber-ink: "#2a1d02"
  reagent-red: "#ff6f61"
  reagent-red-ink: "#2e0805"
  transfer-blue: "#7fb2ff"
typography:
  display:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 700
    lineHeight: 1.2
  headline:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 700
    letterSpacing: "0.01em"
  title:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  body-small:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    letterSpacing: "0.03em"
    fontFeature: "tnum"
  mono:
    fontFamily: "Cascadia Mono, Consolas, ui-monospace, monospace"
    fontSize: "11.5px"
    fontWeight: 400
    lineHeight: 1.45
rounded:
  tape: "1px"
  badge: "4px"
  toast: "6px"
  control: "7px"
  group: "8px"
  well: "14px"
  plate: "18px"
spacing:
  hair: "4px"
  well-pad: "8px"
  well-gap: "10px"
  pad: "14px"
  gap: "14px"
  tape: "30px"
  acts: "36px"
  top-bar: "52px"
components:
  button:
    backgroundColor: "{colors.graphite-3}"
    textColor: "{colors.graphite-9}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 12px"
  button-hover:
    backgroundColor: "{colors.graphite-4}"
  button-active:
    backgroundColor: "{colors.graphite-5}"
  button-primary:
    backgroundColor: "{colors.reagent-teal}"
    textColor: "{colors.reagent-teal-ink}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 12px"
  button-danger:
    backgroundColor: "{colors.graphite-3}"
    textColor: "{colors.reagent-red}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 12px"
  button-confirm:
    backgroundColor: "{colors.reagent-red}"
    textColor: "{colors.reagent-red-ink}"
    rounded: "{rounded.control}"
    height: "32px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.graphite-8}"
    rounded: "{rounded.control}"
    height: "32px"
  button-small:
    rounded: "{rounded.control}"
    height: "28px"
    padding: "0 9px"
  input:
    backgroundColor: "{colors.graphite-1}"
    textColor: "{colors.graphite-9}"
    rounded: "{rounded.control}"
    height: "32px"
    padding: "0 9px"
  tape-id:
    backgroundColor: "{colors.graphite-8}"
    textColor: "{colors.graphite-1}"
    typography: "{typography.title}"
    rounded: "{rounded.tape}"
    height: "22px"
    padding: "0 8px"
  badge:
    backgroundColor: "{colors.graphite-3}"
    textColor: "{colors.graphite-7}"
    typography: "{typography.label}"
    rounded: "{rounded.badge}"
    height: "20px"
    padding: "0 8px"
  well:
    backgroundColor: "{colors.graphite-2}"
    rounded: "{rounded.well}"
    padding: "{spacing.well-pad}"
  plate:
    backgroundColor: "{colors.graphite-0}"
    rounded: "{rounded.plate}"
    padding: "6px"
  panel:
    backgroundColor: "{colors.graphite-2}"
    rounded: "{rounded.well}"
  top-bar:
    backgroundColor: "{colors.graphite-2}"
    height: "{spacing.top-bar}"
---

# Design System: agent-emu

## Overview

**Creative North Star: "The Assay Plate"**

The Fleet is a lab plate on a graphite bench: eight fixed wells in two rows of four, each well a phone-shaped recess at true iPhone 17 Pro Max aspect (1320 x 2868). Every well is always present, empty or not, so the plate itself is the inventory; there is no instance table beside a single viewer. A well's state is read from its rim the way a reagent is read from a tube: dashed and colourless when empty, amber while booting, teal when running, red when failed.

The bench is dark, dense and neutral. All structure comes from one ten-step graphite ramp with near-zero chroma; hue is reserved for reagents, which carry state and nothing else. Labels are set like lab tape in Bahnschrift with tabular figures, so Device ids, RAM and frame counts line up and read as instrument output. The room is a working desk at 1920 and 2560 wide: the plate fills the height, the focused Device stands full height in the stage, and controls sit in a collapsible side panel.

Motion is functional and short. Boot is a reagent fill rising up the well rim with progress, then a teal pulse when the Device is ready; the focused well lifts 5px while the others hold still. Reduced motion removes all of it.

**Key Characteristics:**
- Ten graphite steps for every neutral; hue only on reagents.
- Phone-shaped recesses with a state rim are the core unit, at true device aspect.
- Bahnschrift tape labels with tabular figures for ids, states and measurements.
- Tonal layering for depth; shadow only on the lifted, focused well and floating panel.
- Inline progress everywhere: busy buttons breathe a 2px bar, wells fill their rim.

## Colors

A near-achromatic graphite bench with four saturated reagents that only ever mean state or action.

### Primary
- **Indicator Teal** (`reagent-teal`): running Devices (rim, badge, RAM budget segment), the one primary action per region (Start), checkbox accent, input focus ring, the live brand dot. Text on it is always **Teal Ink** (`reagent-teal-ink`).

### Secondary
- **Boot Amber** (`reagent-amber`): booting, queued and stopping states; the rising rim fill; warnings in the budget caption and `W` log lines. Ink pair `reagent-amber-ink`.

### Tertiary
- **Fault Red** (`reagent-red`): failed wells (rim, veil tint, heading), destructive buttons (outline variant at rest, filled only for confirm), the 4000 MB host RAM floor marker, `E`/`F` log lines, app-side issues. Ink pair `reagent-red-ink`.
- **Transfer Blue** (`transfer-blue`): drag-and-drop targets only (APK drop zone, a well accepting a drop) and the swipe touch trace. Never a state colour.

### Neutral
- **Bench Black** (`graphite-0`): the plate tray, phone glass, log well.
- **Bench** (`graphite-1`): page ground, input fields.
- **Surface** (`graphite-2`): top bar, wells, stage, side panel.
- **Raised** (`graphite-3`): resting buttons, hover rows, badge ground.
- **Rule** (`graphite-4`): borders, dividers, empty rim, unfilled rim track.
- **Stroke** (`graphite-5`): control borders, dashed empty-rim, pressed segment.
- **Lift** (`graphite-6`): hover borders, selected well border.
- **Muted Ink** (`graphite-7`): secondary text, captions, field labels, placeholders.
- **Soft Ink** (`graphite-8`): ghost button text, log text, tape id ground.
- **Ink** (`graphite-9`): primary text, focus outline, selected tape id.

### Named Rules
**The Bench Ramp Rule.** Every neutral is one of the ten graphite steps. Tints are made by `color-mix` of a reagent into a ramp step (12-13% for badges, 6% for a failed veil), never by a new hex.

**The Reagent Means State Rule.** Teal, amber and red appear only to report a Device state, a budget limit, or the action that changes one. Blue appears only while something is being dropped or swiped.

## Typography

**Display Font:** Bahnschrift (with DIN Alternate, Segoe UI, system-ui)
**Body Font:** Segoe UI Variable Text (with Segoe UI, system-ui)
**Label/Mono Font:** Cascadia Mono (with Consolas, ui-monospace)

**Character:** A condensed DIN face for everything that names or measures (ids, states, section titles), a quiet humanist sans for sentences, and a monospace for raw device output. All three ship with Windows, which the panel needs because it loads nothing from the network.

### Hierarchy
- **Display** (700, 26px): the focused Device id in the stage sidebar.
- **Headline** (700, 19px, 0.01em): the product name in the top bar.
- **Title** (600-700, 15px): side panel section summaries, panel head, well tape ids; veil headings run at 17px/600.
- **Body** (400, 14px, 1.5): default text and controls.
- **Body small** (400, 12.5-13px, 1.45): notes, captions, facts lists, field labels, table cells.
- **Label** (600, 12px, 0.03em, uppercase, semi-condensed): state badges and the app/system side tag (11.5px, 0.04em). Labels with figures use tabular numerals.
- **Mono** (400, 11-12px, 1.45): log stream, raw error text in a failed veil, command output, `kbd`.

### Named Rules
**The Tape Rule.** Anything that identifies or measures (Device id, state, RAM, fps, counts) is set in Bahnschrift or with `tabular-nums` so columns do not jitter while values update.

## Layout

A full-viewport, three-column desk under a 52px top bar: the plate (max-content), the focus stage (fills), and the side panel (`clamp(360px, 18vw, 460px)`), separated by 14px gaps with 14px outer padding. The top bar holds the brand, the host RAM budget bar (up to 760px, segmented per Device with the 4000 MB floor marked), a live status line, and Start / Stop all / Focus view.

The plate is a 4 x 2 grid of wells with 10px gaps. Phone height is derived from the viewport so two rows exactly fill the height under the top bar (minimum 250px); width follows at 1320/2868. Each well stacks a 30px tape row, the recess, and a 36px action row, with 8px inner padding. The stage centres one phone at full available height using a size container, with a 236px side column (196px under 2200px wide).

The panel collapses to a 48px icon rail; the stage can be hidden. Under 1500px wide the stage loses its side column and the open panel floats over the stage (fixed, 360px) instead of taking a column.

## Elevation & Depth

Depth is tonal: the plate tray sits darkest (`graphite-0`) with a faint inset shadow, wells and panels rise one step to `graphite-2`, controls to `graphite-3`. Shadows are reserved for things that are physically lifted out of the bench.

### Shadow Vocabulary
- **Tray inset** (`box-shadow: inset 0 1px 3px rgba(0,0,0,.6)`): the plate tray, so wells read as seated in it.
- **Lifted well** (`box-shadow: 0 10px 28px -10px rgba(0,0,0,.8), 0 0 0 1px var(--z6)` with `translateY(-5px)`): the focused well only.
- **Floating panel** (`box-shadow: 0 12px 24px -8px rgba(0,0,0,.8)`): the side panel when it overlays the stage on narrow windows.
- **Ready pulse** (teal ring expanding 0 to 14px over 0.7s): once, when a Device becomes ready.

### Named Rules
**The One Lift Rule.** Only the focused well lifts; the other seven hold their place. Selection is shown by lift and a `graphite-6` border, never by a reagent colour.

## Shapes

Soft-cornered instruments on a soft-cornered tray: controls at 7px, grouped controls at 8px, wells, stage and panel at 14px, the plate tray at 18px. Badges are 4px and tape ids are nearly square (1px), like a strip of label tape. The phone glass uses the device's own elliptical corner (`12.5% / 5.75%`) inside a 5px near-black bezel; the rim follows that shape 4px outside it. Empty and queued states swap the solid rim for a 1.5px dashed stroke, and the APK drop zone uses the same dashed stroke at 9px.

## Components

### Buttons
Quiet, tactile, one vocabulary everywhere.
- **Shape:** gently rounded (7px), 32px tall, 12px side padding, 550 weight, 16px stroke icon with 6px gap.
- **Default:** `graphite-3` ground with a `graphite-5` border; hover steps up one graphite step on both; press nudges 1px down onto `graphite-5`.
- **Primary:** filled Indicator Teal with Teal Ink text. One per region: top bar Start, Start N, a well's Start.
- **Danger:** default ground with red text; hover adds a red border and 12% red tint. **Confirm** is the filled red version, used only for the second click of a destructive action.
- **Ghost:** transparent, Soft Ink text; used for icon rails, well action rows and toggles.
- **Small / Icon:** 28px tall (9px padding), icon-only squares at 28 or 32px; well actions are 26px wide.
- **Busy:** `aria-busy` draws a 2px breathing bar in the current colour along the bottom edge; the label stays readable.
- **Disabled:** 42% opacity.

### Chips
- **State badge:** 20px tall, 4px radius, uppercase Bahnschrift label with a 6px dot in the current colour. Muted on `graphite-3` when empty; teal, amber or red text on a 12-13% reagent tint when running, booting/queued/stopping, or failed.
- **Segmented toggle:** 4px-padded group with an 8px outline; the pressed segment fills `graphite-5`.

### Cards / Containers
- **Corner Style:** 14px.
- **Background:** `graphite-2` on the `graphite-1` page.
- **Shadow Strategy:** none at rest (see Elevation & Depth).
- **Border:** 1px `graphite-4`.
- **Internal Padding:** 14px for stage and panel sections, 8px inside wells.

### Inputs / Fields
- **Style:** `graphite-1` ground, 1px `graphite-5` border, 7px radius, 32px tall, 9px side padding; placeholder in Muted Ink.
- **Focus:** 2px teal outline with the border turning teal.
- **Fields:** a 76px muted label column beside the control. Checkboxes are 16px with teal accent; the count stepper is a bordered 32px group with a Bahnschrift figure.

### Navigation
- **Side panel:** collapsible `details` sections with a Bahnschrift 15px summary, a chevron that rotates 90 degrees when open, a muted right-aligned aside naming the focused Device, and `graphite-3` on hover. Collapsed, the panel becomes a 48px rail of ghost icon buttons that open a section.
- **Focus:** a global 2px Ink outline offset 2px; a focused phone canvas outlines its rim instead.

### The Well (signature)
A well is a tape row (id chip, state badge, tabular fps/RAM meta, crash link), a phone recess, and a ghost action row (back, home, recents | open app, screenshot | restart, stop). The recess holds the glass and a **rim** masked to a 3px ring: an amber linear fill rises from the bottom to the boot progress, then turns solid teal with a single ready pulse. Failed wells turn the rim red and tint the veil red. When there is no picture, a centred **veil** states what the well is doing in plain words with a Bahnschrift heading, one action, and raw error text in mono if any. Taps leave a fading Ink ring; swipes leave a blue one. Status toasts float over the bottom of the well and fade in and out.

### Host RAM Budget Bar
A 10px bar on `graphite-1` with a `graphite-4` border: one teal segment per running Device (amber while booting), a mixed teal segment for shared memory, transparent free space, and a 2px red marker at the 4000 MB free floor. A tabular caption below reads Devices own + shared, Host free, and how many more fit; it turns amber when room is short.

## Do's and Don'ts

### Do:
- **Do** take every neutral from `graphite-0` to `graphite-9` and make tints with `color-mix` against a ramp step.
- **Do** show a Device's state on its rim and badge in the reagent colour, and nowhere else.
- **Do** keep all eight wells on the plate at all times, at true 1320 x 2868 aspect.
- **Do** set ids, states and measurements in Bahnschrift or with tabular figures.
- **Do** show slow work inline: the rising rim fill, the breathing 2px bar on a busy button, a status toast in the well.
- **Do** keep one teal primary action per region, with Teal Ink text on it.
- **Do** honour `prefers-reduced-motion` by removing transitions and animations.

### Don't:
- **Don't** add hue to the graphite ramp or introduce off-ramp neutrals.
- **Don't** use teal, amber or red for decoration, selection or branding beyond the live dot.
- **Don't** use blue for anything but drop targets and swipe traces.
- **Don't** lift or shadow anything but the focused well and the floating panel.
- **Don't** replace the plate with a table or list of Devices beside a single viewer.
- **Don't** fill a destructive button red until the confirm step.
