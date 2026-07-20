# Bundled label fonts

The specimen labels (`app/services/labels.py`) are rendered by WeasyPrint into a
PDF **server-side**, so they depend on the label font being available to the
rendering process. Rather than rely on the font happening to be installed on the
host OS — which it is *not* on a fresh Windows or macOS machine, where WeasyPrint
would silently fall back to a different metric and change the label width — the
font is **bundled here and referenced via `@font-face` with absolute `file://`
URLs** (`labels._FONT_FACE_CSS`). This makes label rendering byte-identical on
every platform.

## What's here

Four faces of **Fira Sans Compressed** (regular / bold / italic / bold-italic —
the labels use bold-italic for scientific names, italic via `<em>`, bold via
`<strong>`):

- `FiraSansCompressed-Regular.ttf`
- `FiraSansCompressed-Bold.ttf`
- `FiraSansCompressed-Italic.ttf`
- `FiraSansCompressed-BoldItalic.ttf`

## License

Fira Sans is licensed under the **SIL Open Font License, Version 1.1** — see
`OFL.txt` in this directory. The OFL explicitly permits bundling/redistribution
with an application as long as the license travels with the font files, which is
what this directory does.

Digitized data copyright 2012–2018: The Mozilla Foundation, Telefonica S.A.,
Carrois Corporate GbR and bBox Type GmbH, with Reserved Font Name "Fira".
