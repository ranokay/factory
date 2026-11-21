# web

## Assets

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

- Playwright (`npm:@playwright/test`) is the verification driver for `verify-<app>` skills: it runs reproducible browser checks in CI without a signed-in browser.
- The Gate runs no browser. Browser tests run when you produce Verification Evidence, and the evidence shows the real page at the sizes the change affects.
