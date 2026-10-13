# Red Note UI and graph precision pass

## Visual decisions

- Use the user's current Red Note profile screenshot as the direct visual reference: near-black surfaces, white primary text, muted gray secondary text, a strong red action color, image-led records, and restrained borders.
- Apply this palette to the Mirror Cabinet, Studio Red Engine workspace, and newly added default source/tool surfaces. Preserve the existing authored Rainee, Dudu, Horse, and cinematic custom node designs.
- Organize the Red Engine page as an application workspace: persistent left navigation, one active central task view, and a right study-context rail with real saved studies. Move long implementation prose into expandable notes rather than making it the primary experience.

The official [Red Note site](https://www.xiaohongshu.com/) provides the product context; the attached user screenshot supplies the specific dark-profile treatment. [ChatGPT Projects documentation](https://help.openai.com/en/articles/10169521-projects-in-chatgpt) informed the project/study navigation metaphor. These are inspiration sources, not a claim that the prototype reproduces either product exactly.

## Placement behavior

- Cabinet group, captured profile, Custom asset, and default-class tools drag onto the board. Dropping creates at the cursor's canvas position and does not recenter the view.
- A grid can be shown or hidden independently of snapping. The step can be 10, 20, 40, or 80 graph units. The drop preview and HUD show graph X/Y coordinates.
- During node movement, edge and center alignment guides appear against other nodes. When Snap is on, alignment takes priority over the grid. Hold Alt while moving to bypass snapping.
- Selected-node X/Y inputs provide numeric placement. Moving an owned branch root also moves its owned nodes. The existing custom artwork is not restyled by these controls.

The interaction pattern follows the official [React Flow helper-lines example](https://reactflow.dev/examples/interaction/helper-lines), which describes visual horizontal/vertical alignment during dragging and snapping to those lines, and its [SnapGrid reference](https://reactflow.dev/api-reference/types/snap-grid).

## Data limits

The Cabinet still reads captured archive records, Studio-local groups, and browser-local Custom assets through separate adapters. A Studio fixture member appears as captured only when it can be matched to a real Mirror profile. Big Lips and Horse references remain pending downloads. The graph is a UI prototype; these placement controls do not change the headless Scatter engine's source pins or revisions.
