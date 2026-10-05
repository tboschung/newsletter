# Morning Signal design context

## Product and audience

Morning Signal is a personal morning digest for technically curious readers who want AI news, technical releases, and relevant jobs without an endless feed. The landing page has one job: help a visitor choose a digest preset and subscribe.

## Visual direction

The interface extends the owner’s homelab page: a precise monospace voice, hard pixel-like edges, dark terminal surfaces, and cyan/yellow/coral signals. Its signature is the “digest packet,” a compact preview of the finite email people receive.

## Runtime tokens

Canonical tokens live in the `:root` block of `index.html`. `--night` and `--deep` define the page and shadows; `--panel` and `--raised` group content; `--paper` and `--muted` carry copy; `--aqua`, `--sun`, and `--coral` communicate action, focus, and errors. Corners remain square and shadows use hard offsets. Motion is limited to the ticker and stops under reduced-motion preferences.

## Interaction contract

Native controls own keyboard behavior. Validation is inline, preserves entered values, and focuses the first invalid control. The submit button keeps stable dimensions while busy. Success and failure feedback use a persistent live region; browser dialogs and transient-only messages are not used.
