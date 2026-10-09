---
name: agent-emu
description: Control panel for a Fleet of up to eight Android Devices, shot like phones standing on a seamless studio sweep.
colors:
  indigo: "light-dark(#4b44d6, #8f89ff)"
  green: "light-dark(#18864b, #3fcf83)"
  amber: "light-dark(#a86200, #f0b03f)"
  red: "light-dark(#cc3326, #ff6d60)"
  sweep-top: "light-dark(#d9d6d0, #1e1e21)"
  sweep-mid: "light-dark(#e2dfda, #141416)"
  sweep-floor: "light-dark(#f8f7f4, #0b0b0d)"
  pool: "light-dark(rgb(255 255 255 / .75), rgb(255 255 255 / .045))"
  ink: "light-dark(#1c1b19, #f2f1ee)"
  ink-2: "light-dark(#5f5c56, #a6a39d)"
  ink-3: "light-dark(#8d8982, #75726d)"
  line: "light-dark(rgb(28 27 25 / .10), rgb(255 255 255 / .09))"
  line-2: "light-dark(rgb(28 27 25 / .16), rgb(255 255 255 / .15))"
  fill: "light-dark(rgb(28 27 25 / .05), rgb(255 255 255 / .06))"
  fill-2: "light-dark(rgb(28 27 25 / .09), rgb(255 255 255 / .11))"
  card: "light-dark(rgb(255 255 255 / .78), rgb(36 36 39 / .72))"
  card-solid: "light-dark(#ffffff, #232326)"
  bar: "light-dark(rgb(246 245 242 / .72), rgb(22 22 24 / .70))"
  btn-ink: "light-dark(#1c1b19, #f2f1ee)"
  btn-ink-text: "light-dark(#ffffff, #141414)"
  glass: "#050506"
typography:
  display:
    fontFamily: "Segoe UI Variable Display, Segoe UI, system-ui, -apple-system, sans-serif"
    fontSize: "26px"
    fontWeight: 650
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Segoe UI Variable Display, Segoe UI, system-ui, -apple-system, sans-serif"
    fontSize: "24px"
    fontWeight: 650
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Segoe UI Variable Display, Segoe UI, system-ui, -apple-system, sans-serif"
    fontSize: "15px"
    fontWeight: 650
    letterSpacing: "-0.01em"
  caption-name:
    fontFamily: "Segoe UI Variable Display, Segoe UI, system-ui, -apple-system, sans-serif"
    fontSize: "13.5px"
    fontWeight: 650
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "-0.003em"
  label:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, sans-serif"
    fontSize: "12px"
    fontWeight: 560
    fontFeature: "tnum"
  mono:
    fontFamily: "Cascadia Mono, Consolas, ui-monospace, monospace"
    fontSize: "11.5px"
    fontWeight: 400
    lineHeight: 1.45
rounded:
  kbd: "5px"
  seg-item: "7px"
  field: "8px"
  seg: "10px"
  well: "12px"
  sheet: "14px"
  list: "16px"
  panel: "18px"
  pill: "999px"
spacing:
  hair: "6px"
  gap: "8px"
  inset: "18px"
  grid: "18px"
  page-y: "22px"
  page-x: "28px"
  column: "32px"
  caption: "46px"
  bar: "56px"
components:
  button:
    backgroundColor: "{colors.fill-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    height: "30px"
    padding: "0 13px"
  button-ink:
    backgroundColor: "{colors.btn-ink}"
    textColor: "{colors.btn-ink-text}"
    rounded: "{rounded.pill}"
    height: "30px"
    padding: "0 13px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    height: "30px"
  button-quiet-hover:
    backgroundColor: "{colors.fill}"
    textColor: "{colors.ink}"
  button-danger:
    backgroundColor: "{colors.fill-2}"
    textColor: "{colors.red}"
    rounded: "{rounded.pill}"
    height: "30px"
  button-confirm:
    backgroundColor: "{colors.red}"
    textColor: "#ffffff"
    rounded: "{rounded.pill}"
    height: "30px"
  button-small:
    rounded: "{rounded.pill}"
    height: "26px"
    padding: "0 10px"
  input:
    backgroundColor: "{colors.fill}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    height: "30px"
    padding: "0 10px"
  segmented:
    backgroundColor: "{colors.fill-2}"
    rounded: "{rounded.seg}"
    padding: "3px"
  segmented-selected:
    backgroundColor: "{colors.card-solid}"
    textColor: "{colors.ink}"
    rounded: "{rounded.seg-item}"
    height: "26px"
  toolbar:
    backgroundColor: "{colors.bar}"
    height: "{spacing.bar}"
    padding: "0 20px"
  inspector:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.panel}"
    padding: "{spacing.inset}"
  facts:
    backgroundColor: "{colors.fill}"
    rounded: "{rounded.well}"
    padding: "10px 12px"
  popover:
    backgroundColor: "{colors.card-solid}"
    rounded: "{rounded.sheet}"
    padding: "16px"
---

# Design System: agent-emu

## Overview

**Creative North Star: "The Studio Sweep"**

The Fleet is a product shoot. Eight phones stand on a seamless cove that fades from a darker wall to a pale floor, each one a physical object: black glass in a thin bezel with a soft contact shadow under it, and a quiet caption under that. The focused phone stands large beside them, with a frosted inspector card at its side. By day the sweep is warm paper grey; by night it is deep graphite. Both themes come from the same tokens through `light-dark()`.

Everything around the phones is quiet material: a translucent toolbar over the sweep, frosted cards, near-black ink pill buttons (white in dark mode). Hue appears only to report state (green running, amber warming, red fault) and in one reserved colour, indigo, for focus, selection and the thing your hand is on. The type is the system face, set with tight tracking and tabular figures.

Motion is short and physical. A booting phone's glass warms up like studio lights coming on, through four phase marks, and gives one soft glint when it is ready. Buttons press in to 96.5%, the selected phone rises 4px, and popovers and toasts settle in over about 0.2s. Reduced motion removes all of it.

**Key Characteristics:**
- Seamless sweep ground: a vertical gradient plus a soft light pool behind the grid.
- Phones are objects: black glass, layered bezel rings, two-part contact shadow, true 1320 x 2868 aspect.
- Translucent and frosted surfaces (`backdrop-filter` blur) over the sweep, never opaque slabs.
- Ink pill buttons; state colour only as a dot or text, never as a fill, except for the confirm step.
- Indigo is reserved for focus, selection, drop targets and touch traces.

## Colors

A warm-neutral tonal family for the sweep and ink, three state colours, and one reserved indigo. Every token is a `light-dark()` pair.

### Primary
- **Studio Indigo** (`indigo`): the selected phone's ring, every focus outline, checkbox accent, text selection, APK drop targets, swipe traces, links inside notes, and the busy pulse on a caption dot. It never shows state.

### Secondary
- **Running Green** (`green`): the running dot on captions and in tables, the connected dot by the wordmark, passing counts in Test runs.
- **Warm-up Amber** (`amber`): booting, queued and stopping dots (breathing), the boot phase marks, `W` log lines.
- **Fault Red** (`red`): failed captions and glass headings, the host-RAM floor marker, `E`/`F` log lines, failed checks and counts, the danger button text, and the filled confirm button.

### Neutral
- **Sweep Wall / Mid / Floor** (`sweep-top`, `sweep-mid`, `sweep-floor`): the page gradient, top to bottom. **Light Pool** (`pool`) is the soft radial light behind the grid.
- **Ink** (`ink`), **Ink 2** (`ink-2`), **Ink 3** (`ink-3`): primary text; secondary text and labels; idle dots, separators and placeholders.
- **Line** (`line`), **Line 2** (`line-2`): hairline dividers; card edges, hover borders, dashed drop zones.
- **Fill** (`fill`), **Fill 2** (`fill-2`): input and quiet-hover ground; default button and segmented-control ground.
- **Card** (`card`): frosted inspector, run list and case list. **Card Solid** (`card-solid`): popovers, the selected segment, the current run.
- **Bar** (`bar`): the translucent toolbar.
- **Button Ink** (`btn-ink`) with **Button Ink Text** (`btn-ink-text`): the primary pill.
- **Glass** (`glass`): the phone screen, the same in both themes.

### Named Rules
**The One Reserved Colour Rule.** Indigo means "this one, here, now": focus, selection, a drop target, a touch. It is never used for state or decoration.

**The State Dot Rule.** Green, amber and red appear as a 7px dot, a text colour or a thin mark. The only solid red fill is the second click of a destructive action.

## Typography

**Display Font:** Segoe UI Variable Display (with Segoe UI, system-ui, -apple-system, sans-serif)
**Body Font:** Segoe UI Variable Text (with Segoe UI, system-ui, -apple-system, sans-serif)
**Label/Mono Font:** Cascadia Mono (with Consolas, ui-monospace, monospace)

**Character:** The Windows system faces in their optical sizes: Display for names and headings with negative tracking, Text for everything else, Cascadia for raw device output. The panel loads nothing from the network, so it uses what ships with the machine.

### Hierarchy
- **Display** (650, 26px, 1.1, -0.02em): the run heading in Test runs; the big memory figure runs at 600/26px.
- **Headline** (650, 24px, 1.1, -0.02em): the focused phone's id in the inspector.
- **Title** (650, 15px, -0.01em): the wordmark, popover and section headings, the heading on a phone's glass. Run stats use 600/22px.
- **Caption name** (650, 13.5px, -0.01em): the phone id under each phone.
- **Body** (400, 13px, 1.45, -0.003em): default text and controls; button labels at 560.
- **Label** (560, 11.5-12px): state pills, caption meta, small buttons, legends, notes; figures in tabular numerals.
- **Mono** (400, 11-11.5px, 1.45-1.5): logs, command output, raw errors on the glass (10.5px), `kbd`.

### Named Rules
**The Steady Figures Rule.** Any number that updates (RAM, fps, counts, uptime, durations) uses `tabular-nums` so it does not jitter.

## Layout

A full-viewport desk under a 56px toolbar. The toolbar holds the wordmark with its connection dot, a centred Phones / Test runs segmented control, and on the right the memory meter, the Start split button and Stop all.

The Phones view is two columns with a 32px gap and 22px / 28px page padding: a 4 x 2 grid of phones (18px gaps, each phone over a 46px caption) and the focus area. Phone height is derived from the container so two rows fill the height, capped so the grid takes at most about 60% of the width. The focus area holds the large phone, centred at full height, and the inspector (`clamp(300px, 22vw, 380px)`, 300px under 1280px wide) with 28px between them. Inside the inspector, content sits on an 18px inset.

Test runs is a 300px run list beside a scrolling detail column (24px gap): heading and stats, the case list, then a screenshot grid (min 150px cells, 16px gaps).

Under 980px the page scrolls, the toolbar wraps, the grid drops to two columns of 300px phones, the large phone hides, and the inspector follows below.

## Elevation & Depth

A hybrid: frosted translucency for chrome, soft lit shadows for objects. The shadow colour is a token (`light-dark(rgb(40 34 24 / .16), rgb(0 0 0 / .55))`), warm by day.

### Shadow Vocabulary
- **Contact shadow** (`0 14px 26px -12px var(--shadow), 0 26px 30px -26px var(--shadow)`): every phone, with the bezel rings above it.
- **Lifted phone** (`0 22px 34px -14px var(--shadow), 0 34px 36px -28px var(--shadow)` with `translateY(-4px)`): the selected phone.
- **Card edge** (`0 0 0 .5px var(--line-2), 0 12px 34px -18px var(--shadow)`): the inspector; lists use the hairline alone.
- **Raised chip** (`0 1px 3px var(--shadow), 0 0 0 .5px var(--line)`): the selected segment and the current run.
- **Floating sheet** (`0 18px 50px -12px var(--shadow), 0 0 0 .5px var(--line-2)`): popovers.
- **Toast** (`0 12px 30px -12px rgb(0 0 0 / .5)`): toasts over the page.

### Named Rules
**The Lit Object Rule.** Phones and floating things cast soft shadows; chrome is frosted instead. Shadows are always soft and offset downward, never hard.

## Shapes

Pills for anything you press (buttons, steppers, the memory meter, the glass HUD). Rounded rectangles grow with size: 7px segments, 8px fields, 10px segmented track, 12px wells and drop zones, 14px popovers, toasts and screenshot thumbs, 16px case list, 18px inspector and run list. The phone uses the device's elliptical corner (`13% / 6%`). Dots are circles; legend swatches are 2px squares. The APK drop zone is a 1.5px dashed `line-2` stroke.

## Components

### Buttons
Soft pills, one vocabulary everywhere.
- **Shape:** full pill (999px), 30px tall, 13px side padding, 560 weight, 16px stroke icon with a 6px gap.
- **Default:** `fill-2` ground with Ink text; hover mixes 6% ink in; press scales to 0.965.
- **Ink (primary):** Button Ink with Button Ink Text; hover mixes 14% indigo in. One per region: Start, Start on the glass.
- **Quiet:** transparent, Ink 2 text; hover fills `fill` and turns Ink.
- **Danger:** default ground with red text. **Confirm** is solid red with white text, only for the second click.
- **Small / Icon:** 26px tall, 10px padding, 12px text; icon squares at 30 or 26px.
- **Busy:** a white sheen sweeps across the button and input is blocked.
- **Disabled:** 38% opacity.
- **Split:** Start and its menu chevron share one pill with a 1px seam.

### Chips
- **State pill:** a 7px dot plus a 560-weight label in Ink 2. Dot is green running, breathing amber while warming or stopping, red (with red text) when failed, Ink 3 when off.
- **Segmented control:** `fill-2` track, 3px padding, 10px radius; the selected item turns Card Solid with the raised-chip shadow.

### Cards / Containers
- **Corner Style:** 18px for the inspector and run list, 16px for the case list, 12px for inner wells (facts, drop zone).
- **Background:** frosted Card (blur 20px) over the sweep; inner wells on `fill`.
- **Shadow Strategy:** card edge (see Elevation & Depth).
- **Border:** none; a 0.5px `line-2` ring drawn in the shadow.
- **Internal Padding:** 18px; facts wells 10px / 12px.

### Inputs / Fields
- **Style:** `fill` ground, transparent 1px border, 8px radius, 30px tall, 10px padding; placeholder in Ink 3.
- **Focus:** 2px indigo outline. Hover shows a `line-2` border.
- **Fields:** a muted label column (92px in popovers, 62px in the inspector) beside the control. Checkboxes are 16px with indigo accent; the count stepper is a 30px pill.

### Navigation
- **Toolbar:** translucent Bar with `saturate(1.6) blur(18px)` and a hairline bottom edge.
- **Views:** the centred segmented control switches Phones and Test runs.
- **Popovers:** native `[popover]` sheets under the toolbar, 360px wide, 16px inner padding.
- **Focus:** global 2px indigo outline, offset 2px; a focused phone canvas outlines 8px outside the bezel.

### The Phone (signature)
Black glass at device aspect inside layered bezel rings (1px edge, 3.5px bezel, 4.5px outer hairline) with the contact shadow. Off phones show a faint diagonal reflection and a Start pill on the glass. While booting, a warm radial light brightens the glass with progress, four phase marks fill in amber (widths 1:2:4:1), and elapsed time sits beneath. On ready, one white glint sweeps across. When running, hovering the glass raises a frosted dark HUD pill at the bottom with system keys and Stop. Taps leave a fading white ring; swipes an indigo one. Selected: lifted 4px with a 6.5px indigo ring. Under the phone, the caption: Display-face id, state pill, and a tabular meta line separated by middots.

### Toasts
Frosted dark pills (14px radius, blur 16px) centred near the bottom, white 12.5px text, sliding up 8px on entry; errors turn deep red.

## Do's and Don'ts

### Do:
- **Do** take every colour from the `:root` tokens and their `light-dark()` pairs, so both themes stay in step.
- **Do** keep all eight phones on the sweep, at true 1320 x 2868 aspect, with bezel and contact shadow.
- **Do** show state as a dot or text colour in green, amber or red.
- **Do** use indigo only for focus, selection, drop targets and touch traces.
- **Do** use ink pill buttons, one ink primary per region.
- **Do** set changing numbers in tabular figures.
- **Do** confirm destructive actions in place: red text first, solid red only on the second click.
- **Do** honour `prefers-reduced-motion` by removing transitions and animations.

### Don't:
- **Don't** replace the phone grid with a table or list of Devices beside a single viewer.
- **Don't** fill buttons or surfaces with state colours, except the confirm step.
- **Don't** use indigo to show state or as decoration.
- **Don't** use hard offset shadows; every shadow is soft and lit from above.
- **Don't** put opaque slabs over the sweep; chrome is translucent or frosted.
