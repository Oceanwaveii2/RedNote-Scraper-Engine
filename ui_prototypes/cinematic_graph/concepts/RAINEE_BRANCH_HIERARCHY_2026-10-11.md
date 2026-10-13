# Rainee Scatter branch hierarchy — concept

This is a design sketch, not a claim that the diagrammed runtime or Prose Engine is implemented. The editable [SVG network sketch](../assets/concepts/rainee-hierarchy-story-flow-v2.svg) shows the proposed placement.

## Reading order

1. **One character profile at the top** anchors Rainee's shared identity.
2. **Three blue outlined nodes in the second horizontal row** select independent paths: Core, Big Lips, and Horse. Big Lips and Horse are parallel alternative timelines, universes, or states. Neither is downstream of the other. New routes can be added beside them.
3. **Each route descends into its own character state and source hierarchy.** A mirror profile folder holds the source profile, its post listing, individual posts, and post-level JSON overrides. This hierarchy follows the captured mirror data. The Core example is Lingzu → main profile/post listing → posts → JSON overrides. Equivalent folders may be assigned to the alternatives.
4. **Post inclusion is a derived selection.** Batch-captured posts start positive/included. A negative post has a red outline and is omitted from that branch's computation. The captured post remains in Storage; double-clicking the profile and switching it positive includes it again. The sketch assumes inclusion is scoped to a route's folder; whether one switch should apply globally across all uses of the same profile needs an explicit product decision.
5. **Video junctions live inside a branch.** Their story media contributes to that branch's context. They cannot float outside a route or silently feed other routes.
6. **A music junction can sit anywhere on the board.** Its effect is abstract cross-modal steering, so its connection terminates at the relevant branch interpreter rather than directly claiming a story event or editing captured facts. One music junction may deliberately steer more than one branch through explicit connections.
7. **At the end of each branch's dialogue/story channels sits an interpreter.** It combines selected source-backed context, video observations, dialogue and music steering into a structured interpretation. Observed evidence, inferred meaning and creative premises remain distinguishable.
8. **A planned Prose Engine node follows the interpreter, before the text output.** The branch's current output is a text window; a richer editable text document with typography controls can replace that view later. Each branch can produce its own text without rewriting the mirror source or another branch's accepted state.

```text
                                  Rainee character profile
                              /             |             \
                       BLUE Core     BLUE Big Lips     BLUE Horse
                           |               |               |
                    Core state      Big Lips state     Horse state
                           |               |               |
                mirror profile       branch data        branch data
                └ post listing      + video junction   + video junction
                  ├ + included             |               |
                  │  └ JSON overrides      |               |
                  ├ − excluded             |               |
                  └ + included             |               |
                           |               |               |
                    dialogue/story   dialogue/story    dialogue/story
                           |               |               |
Music junction (free) - - > interpreter < - - > other branch interpreters
                           |               |               |
                     Prose Engine     Prose Engine      Prose Engine
                           |               |               |
                      text output      text output       text output
```

The last diagram line is schematic: music has a separate explicit steering edge to each selected interpreter; interpreters do not exchange branch state merely because they use the same music junction.

## Research alignment

The local [cross-modal research](../../../../Research/CROSS_MODAL_SYSTEM_IMPROVEMENT_CODEX_AND_API_2026-10-07.html) identifies music as needing an active influence on narrative structure instead of being decorative soundtrack. The [Prose Engine discussion brief](../../../../Research/PROSE_ENGINE_DISCUSSION_RESEARCH_2026-10-10.md) separates original media, observations, interpretations, and derived prose. Its Prose Profile Factory and full rich-transcript flow remain research proposals. These constraints motivate the interpreter and Prose Engine positions shown here; they do not prove either is already wired into Scatter.
