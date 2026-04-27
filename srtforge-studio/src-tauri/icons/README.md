# Icons

Tauri's bundler reads `icons/icon.png` and `icons/icon.ico` at build time
(see `tauri.conf.json` → `bundle.icon`). They are **not** committed here
yet — drop your own art in or generate them with:

```bash
pnpm tauri icon path/to/source-1024.png
```

That command writes all the sizes Tauri needs (32×32 .png, 128×128 .png,
icon.ico, etc.) into this directory.
