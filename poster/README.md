# Conference poster

`dtsg_poster.tex` — A0 landscape poster for the DTSG internship project.

## Build

```bash
pdflatex dtsg_poster.tex   # run twice: the header and footer bands use
                           # TikZ overlays that need a second pass
```

## Assets

| File | Status |
| --- | --- |
| `architecture.png` | included — Figure 1, cropped from the report PDF at 300 dpi |
| `igdtuw-logo.jpg` | **add your own** — drop it in this directory |

Without the logo the poster still compiles; a blank placeholder sits in its
place, so add the file before printing.

## Layout

Three columns, read left to right: the problem and the data model (1–3), how the
system works (4–6), and the evidence (7–9). Column widths are 28 / 37.8 / 28 % of
the text width with 1.2 % gaps; `\vfill` between boxes spreads each column to the
footer, so if you add or cut text the columns re-balance themselves.

Type scale, colours and box styles are defined once at the top of the file —
change `\Body`, `IGGreen` or the `posterbase` style and the whole poster follows.
