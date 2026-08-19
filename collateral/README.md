# Collateral

Ready-to-use material built from the same evidence as the code in this repository.
All of it is anonymised - no customer is named, and every measurement is labelled as
coming from a reference tenant.

| File | What it is | Use it for |
| --- | --- | --- |
| `SAP_BDC_CDC_White_Paper.pdf` | 20 pages, Snowflake-branded, all 7 diagrams, step-by-step implementation | The self-contained reference. Publishing, or handing to someone who has seen none of the other material and needs both the argument and the build |
| `SAP_BDC_CDC_Executive_Summary.pdf` | 5 pages, diagram-led | Leaving behind after a conversation with an architect or a data leader |
| `SAP_BDC_CDC_Solution_Overview.pptx` | 18 slides, Snowflake-branded | Presenting to a customer or to product management. The gap analysis is slides 14-16 and can be dropped or kept depending on audience |

The diagrams in both are in [../assets/diagrams](../assets/diagrams) as PNGs if you want
to reuse them in your own material.

Which one to reach for: the executive summary makes the case in five pages, the white
paper makes it and then shows the implementation, and the deck presents it. Section 8 of
the white paper is the same procedure as [../docs/deployment.md](../docs/deployment.md).

A separate 10-page technical build guide also exists. It is not committed here because
`docs/` is the maintained version - if the two ever disagree, trust `docs/`.
