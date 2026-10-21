Greystar Networks is a web hosting and network infrastructure brand. The interface is technical and premium: soft warm-gray surfaces, big rounded shapes, pill controls, one acid-yellow accent and thin geometric type. Use the tokens in `tokens.json` and the components in `components/` (global `GreystarUI`); do not invent values.

## Content fundamentals

- Write short, direct, factual copy. Lead with the state or the number, then the action. "Server online. 99.98% uptime, last 30 days." not "Great news! Your server is running smoothly."
- Address the customer as "you" and the company as "we" only when it acts ("We migrated your site"). Prefer neutral system voice for status.
- Sentence case everywhere: buttons, headings, chips, table headers. Keep product nouns lowercase unless they are proper names (Dallas, DA11).
- Put units next to numbers, spaced from the value and set quieter: `99.98 %`, `12 ms`, `34 deals`. Use real hostnames, IPs and specs as sample data, set in `mono-md`.
- Name states with a word, never an icon or color alone: Online, Degraded, Offline, Provisioning.
- No emoji, no exclamation marks, no marketing superlatives.
- Button labels are verb phrases: "Deploy server", "Open console", "View logs", "New task".

## Visual foundations

**Ground and layers.** Build every screen from `surface-page` (warm greige). Group content on `surface-card`. Lift interactive and hero elements onto `surface-raised` (white) with `shadow-soft`. Use `surface-sunken` for wells, tracks and unselected chips. Use `surface-ink` for always-dark panels such as nav rails and one inverted card per view. Use `surface-slate` for mid-gray tag chips with `on-slate` text.

**Text.** Set text in `ink`; secondary text, labels, placeholders and units in `ink-muted`. On `surface-ink` use `on-ink` and `on-ink-muted`. On bright fills use `on-fill` (never white). On the pastel `tint-*` fills use `on-fill` and `on-fill-muted`. Links and small data labels use `teal`.

**Accent.** `volt` is the single point of focus: the active marker, the key number, the primary action in dark theme. Use it as a fill with `on-fill` text, at most once or twice per view, never as text on a light surface. `lime` means positive, `ember` is the warm signal and warm hero accent, `amber` is warning, `periwinkle` is information. The four `tint-rose`, `tint-sand`, `tint-lilac` and `tint-sky` fills color category tiles, one hue per category.

**Action.** Primary buttons use `action` with `on-action`: black in light theme, volt in dark. Secondary buttons are `surface-raised` with `shadow-soft`. Do not put two primary buttons in one view.

**Type.** Urbanist for everything, JetBrains Mono for hostnames, IPs, commands and dense data. Headlines and numbers are light (300): `display-xl`, `display-lg`, `metric-xl`, `metric-lg`. Interface text is `body` at 400 and `label` at 500. Use `caption` only for tiny uppercase eyebrows. Never go below 11px, never bold the display styles.

**Shape.** Everything is round. Cards and panels use `radius-lg`, page shells and hero panels `radius-xl`, nested tiles `radius-sm` or `radius-md`, anything pressable `radius-pill`. Do not mix square corners into a view. Nested radius is always smaller than its parent.

**Shading.** Depth comes from tone first and shadow second: stack `surface-page`, `surface-card`, `surface-raised`. Use `shadow-soft` at rest, `shadow-float` for menus and dragged items, `shadow-inset` for pressed and sunken states, `shadow-halo` around one `volt` marker on a timeline. No hard borders on cards; `hairline` is for dividers only. Controls that need a visible edge (inputs, ghost buttons) use `border-strong`.

**Spacing.** Use the `space-*` scale: `space-5` (24px) card padding and card gaps, `space-4` between related items, `space-2` between chips. Control heights are `control-sm`, `control-md`, `control-lg`.

**Focus and states.** Keyboard focus is a 2px solid `focus-ring` outline with a 3px offset (black in light theme, volt in dark theme; volt on `surface-ink`). Pressed controls scale to 97% and take `shadow-inset`. Disabled controls drop to 45% opacity and lose their shadow.

**Themes.** Light is the default. Dark swaps surfaces and ink and keeps the accent fills identical. Always reference tokens, never hex values, so both themes work.

**Imagery.** Prefer solid shapes, rack-unit pill rows and flat data over photography. If a 3D or illustrated object is used, keep it matte, soft-lit and in the accent palette on a `surface-page` ground. No gradients, no purple-blue washes.

## Iconography

Use thin line icons: 24px grid, 1.5px stroke, round caps and joins, drawn in `currentColor`. `GreystarUI.Icon` ships seven (`arrow-up-right`, `search`, `plus`, `check`, `server`, `activity`, `sliders`). No icon set was supplied, so Lucide is the closest match for anything else; flag any other icon family before adding it. Icons sit inside pills and circular buttons, never on bare backgrounds as decoration. No emoji.

## Logo

No logo file was supplied. Set the name "Greystar Networks" in `display-md` (or `display-xl` on a cover) in `ink`. Do not draw or approximate a mark. Add the real logo under `assets/Logos/` when it exists.
