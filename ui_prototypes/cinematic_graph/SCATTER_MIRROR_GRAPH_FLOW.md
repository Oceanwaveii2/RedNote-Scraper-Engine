# Scatter ↔ Mirror Storage flow

Concept mockup: [Scatter storage and branch view](assets/scatter-mirror-flow-concept.png). This is a design image. Its portraits, asset names, and comments are illustrative, not archive evidence.

## Source ownership and present status

| Item | Origin | Current state |
| --- | --- | --- |
| Rainee, the master protagonist | Custom character | Authored in Scatter; not a Red Note profile |
| Lingzu | Red Note | Captured in Mirror Storage |
| Big Lips reference | Red Note | Reference identified; assets not downloaded yet |
| Horse reference | Red Note | Reference identified; assets not downloaded yet |
| Music videos | Custom | Import into Custom Storage, separate from the Red Note archive |

Big Lips and Horse may have designed branch and character cards on the prototype board. Those designs do not mean their Red Note source media has been captured or pinned as evidence. A pending source should remain visibly pending until its archive record and media are available.

## Mirror Cabinet UI logic

```mermaid
flowchart LR
  S[Studio Storage<br/>add or remove group members here] -->|group membership message| C[Scatter Mirror Cabinet<br/>open or close popup shelf]
  A[Mirror archive API<br/>captured profiles and posts] -->|read-only records| C
  X[Custom asset store<br/>music videos and images] --> C
  C -->|drag group card| G[Distinct Group node<br/>stores Studio group ID]
  C -->|drag captured profile card| P[Linked Profile node<br/>stores archive profile ID]
  C -->|drag custom media card| M[Custom media node]
  G -->|scroll and select member| P2[Captured profile view]
  P --> P2
  P2 -->|select post| O[Cloned post view<br/>graph-local JSON override]
  O -->|select comment avatar| L[Live Red Note commenter profile URL]
  S -. membership updates .-> G
```

The Cabinet is a view of Studio groups plus captured Mirror records. Dragging creates linked graph objects, not copies of media bytes. Group membership can only be changed in Studio Storage; the graph reads it. A group may contain Studio fixtures or uncaptured references, which remain marked unresolved instead of becoming fake captured profiles. Clicking a captured member opens its paged post database; opening a post shows its clone, captured comments, and a JSON override saved only on the graph node. The comment avatar uses the captured author-profile URL when available. The current browser prototype opens that URL in a browser tab; routing it into the dedicated app-owned live Red Note window remains an integration step.

```mermaid
mindmap
  root((Scatter project))
    Sources
      Custom character
        Rainee master protagonist
        Authored alternative states
      Mirror Storage
        Lingzu captured profile
        Posts included by default
        Negative post exceptions
        Read-only source IDs and previews
      Pending Red Note references
        Big Lips assets not downloaded
        Horse assets not downloaded
      Custom Storage
        Music videos
        Images
        Browser-owned file bytes
    Graph classes
      Protagonist
      Alternative branch blue
      Profile folder
      Music junction
      Video story connector
      Prose Engine slot
      Red Note document output
    Editing
      Drag source onto canvas
      Connect ports
      Browse profile posts
      Format document
      Undo or redo board edits
```

```mermaid
flowchart TB
  R[Custom Rainee master<br/>not a Red Note profile] --> B1[Blue branch: Core]
  R --> B2[Blue branch: Big Lips]
  R --> B3[Blue branch: Horse]
  M[Mirror Storage<br/>captured Lingzu profile and posts] -->|read-only archive ID if selected| P[Lingzu profile folder]
  P --> B1
  L[Big Lips Red Note reference<br/>assets pending download] -. no archive media yet .-> B2
  H[Horse Red Note reference<br/>assets pending download] -. no archive media yet .-> B3
  C[Custom Storage<br/>music videos and imported images] -->|local asset ID| A[Custom media node]
  B1 --> V1[Branch data and video]
  B2 --> V2[Branch data and video]
  B3 --> V3[Branch data and video]
  A -. optional music steering .-> J((Music junction))
  J -. interpreted influence .-> V2
  V1 --> E1[Prose Engine slot]
  V2 --> E2[Prose Engine slot]
  V3 --> E3[Prose Engine slot]
  E1 --> O1[Red Note document]
  E2 --> O2[Red Note document]
  E3 --> O3[Red Note document]
```

## Current implementation boundary

- The floating Mirror Cabinet has Groups, Profiles, and Custom tabs. Studio sends its locally saved group membership to the Cabinet through an origin-checked browser message. The Cabinet resolves each member against captured archive profiles; missing members remain unresolved. Dropping a group creates a distinct graph node with a scrollable captured-member list. Reopening Studio Storage is how membership is edited.
- The board reads the existing archive through a GET-only loopback proxy. A dragged profile node stores its archive profile ID and source ID; it does not clone or modify the source.
- A cloned post opened from a linked profile or group has a graph-local JSON override editor. Saving that override changes the graph node only, leaving captured post JSON intact.
- Only captured profiles appear in the Mirror Profiles dock. At present, Lingzu is captured; Big Lips and Horse have no downloaded source assets to drag from that dock. Rainee belongs to Custom character data, never to that dock.
- The profile browser pages through captured posts. Posts are positive by default; unchecking one saves a negative exception in that specific graph profile placement only. The archive stays unchanged.
- Custom file bytes live in this browser's IndexedDB and are referenced by custom asset ID from graph nodes. They are separate from the Red Note archive.
- The Prose Engine node is deliberately empty. It declares a place in the flow; it does not claim the narrative processor is built or running.
- The document card has a local editable draft, basic font and paragraph controls, HTML export, and a Red Note styled output card. Its side panel can load the first page of captured comments for a selected archive post, including source profile links where captured. This is reference display, not AI interpretation.
- Graph edits still persist in the prototype's browser storage. The semantic Scatter engine requires a revisioned Red Engine study pin with a content hash before it may process a Mirror source. Dragging a profile creates the UI archive pointer now; it does not silently create that study pin or queue AI analysis. Typed graph binding and engine-backed revisions require the next integration pass.

## DesignCraft source decision

[DesignCraft](https://github.com/storytold/designcraft) publishes a Rust page-layout application under MIT OR Apache-2.0. Its installed Windows folder contains executables, not reusable source. The source also has a full web build, but its README says that build has no control channel; embedding the entire application would not give a graph node a small editable document component. The graph prototype therefore uses a small browser editor with the requested font and text controls. No DesignCraft code has been copied or compiled into the graph. A dedicated DesignCraft document-model or WASM integration is a separate build task.
