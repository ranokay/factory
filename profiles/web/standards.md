# web

## Assets

- `mise run assets` writes `public/favicon.svg`, `public/favicon.ico`, `public/apple-touch-icon.png`, the manifest icons and the extension sizes from `assets/brand/icon.svg`. Run it instead of hand-editing one of those files.
- Files under `public/assets/` are lowercase kebab-case and use `.svg` for vector or `.webp`/`.avif` for photos. The `web-assets` Gate enforces names and extensions.
- Give every image `width` and `height`, or reserve its space with `aspect-ratio`, so the page does not shift.
- Strip EXIF and colour-profile metadata from photos before committing. Keep SVG hand-written or exported from the brand masters, never traced or raster-embedded.
- Serve one size per layout. Add `srcset` only for alternatives you have measured.
- Lazy-load below-the-fold images. Load the hero eagerly with `fetchpriority="high"`.
- Write meaningful alt text, or `alt=""` when the image is decorative.

## Pages

- Ship semantic HTML: `button`, `a`, `nav`, `main`, `label`. A clickable `div` is a bug.
- Every interactive control works from the keyboard and shows a visible focus ring. Never remove the outline without a replacement.
- Text and UI meet WCAG AA contrast, and colour is never the only signal.
- Set `lang`, a descriptive `title` and the viewport meta tag. The page does not scroll sideways at 320 px.
- Prefer CSS for layout and disclosure. Keep JavaScript out of the critical path.
- Never put a secret in client code. Anything shipped is public.
- Use logical CSS properties (`margin-inline`, `padding-block`) and `rem` for text so RTL and user font sizes keep working.

## Verification

- The universal Verification table picks the driver. For a web page, prefer Playwright (`npm:@playwright/test`) when the check must be reproducible without a signed-in browser, and Ardia CLI when the flow depends on the user's session. Playwright is pinned in this Profile's `mise.toml`.
- The Gate runs no browser. Browser tests run when you produce Verification Evidence, and the evidence shows the real page at the sizes the change affects.

## New projects

- For a new web project without an established stack, consider Convex, Tailwind, React and Vite with bun. For complex web or React Native apps, consider Zustand, React Query, TanStack Start, Clerk or self-hosted Better Auth, and ArkType or Zod when validation performance permits. Choose dependencies for the actual task.
