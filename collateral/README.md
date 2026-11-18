# Collateral

Ready-to-use material built from the same evidence as the code in this repository.
All of it is anonymised - no customer is named, and every measurement is labelled as
coming from a reference tenant.

| File | What it is | Use it for |
| --- | --- | --- |
| `SAP_BDC_CDC_White_Paper.pdf` | 13 pages, Snowflake-branded: full-bleed cover, key-figures strip, numbered sections, all diagrams, step-by-step implementation | The self-contained reference. Publishing, or handing to someone who has seen none of the other material and needs both the argument and the build |
| `SAP_BDC_CDC_Executive_Summary.pdf` | 2 pages: numbered sections, callouts, capture-cycle graphic | Leaving behind after a conversation with an architect or a data leader |
| `SAP_BDC_CDC_Enablement_Recommendation.pdf` | 7 pages, same design as the white paper | Internal enablement: the standard configuration as a specification, and what to ask SAP for on custom products |
| `SAP_BDC_CDC_Solution_Overview.pptx` | 22 slides on the official Snowflake template: chapter dividers, at-a-glance numbers, card layouts, native tables, every diagram | Presenting to a customer or to product management |
| `SAP_BDC_CDC_Executive_Overview.pptx` | 13 slides on the official Snowflake template | A shorter executive conversation: the situation, the numbers, the risk and the recommendation |

The diagrams are in [../assets/diagrams](../assets/diagrams) as PNGs if you want to reuse
them in your own material. `D*` are the analytical diagrams; `B1_hero.png` and
`B2_cycle.png` are the brand graphics used on covers and summary pages.

Which one to reach for: the executive summary makes the case in two pages, the white
paper makes it and then shows the implementation, and the decks present it. The
deployment section of the white paper is the same procedure as
[../docs/deployment.md](../docs/deployment.md).

## Rebuilding

Every file here is generated - edit the generators, never the files.

```bash
python3 assets/tools/generate_brand_graphics.py      # B1_hero.png, B2_cycle.png
python3 collateral/tools/generate_collateral.py all  # all five documents
```

- Content (every claim and figure) lives in `collateral/tools/generate_collateral.py`.
- Deck styling lives in `collateral/tools/brand_render.py`, built on the Snowflake
  January 2026 template. Point `SNOWFLAKE_PPTX_TEMPLATE` at the template if it is not at
  `~/.snowflake/cortex/skills/pptx/snowflake-template.pptx`.
- Stat strips and at-a-glance slides only show a figure that the document's own text
  already states, so the styling cannot introduce a new number.

A separate 10-page technical build guide also exists. It is not committed here because
`docs/` is the maintained version - if the two ever disagree, trust `docs/`.
