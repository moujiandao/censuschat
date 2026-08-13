# Data Model Page Revamp Design

## Goal

Make the interview manual's data-model page understandable on a first read by replacing implementation-heavy density with a simple explanation of what is stored, how valid rollups work, and what cannot be rolled up.

## Approved content model

The page follows one mental model in three steps:

1. **What the data stores.** Each source row represents one Census block group from the 2020 ACS five-year estimates.
2. **How larger answers are built.** Additive counts can be summed across the block groups belonging to a county or state. Example: county population is the sum of its block-group population counts.
3. **What must not be combined.** Medians cannot be summed or averaged. Example: averaging block-group median incomes does not produce a valid county median income.

Three supporting rules remain:

- Match the statistical universe. People, households, workers, and occupied housing units are different populations.
- Treat missing data honestly. NULL means "not reported," never zero. A verified top-coded income value is rendered as "$250,000 or more."
- Keep the older 2015-2019 vintage out of this build. It overlaps four years with the 2020 five-year release and uses different block-group boundaries, so it does not support a clean trend comparison.

## Layout

Replace the current concept table and long bullet list with three visually distinct explanation cards. Follow them with a compact "quick rules" strip and a short 2015-2019 decision note. Preserve the existing source paths and page count.

## Testing seam

The public seam is `build_manual(output_path: Path)`. The PDF contract test will extract the generated page text and require the three example-led ideas while rejecting the old dense section label.

## Success criteria

- A reader can explain the page as "grain, valid rollup, invalid rollup."
- Population count and median-income examples are visible without reading source code.
- The page remains one Letter-sized page with no clipping, overflow, or reduced body readability.
- No runtime application behavior changes.
