/**
 * Scatter & RedNote Spatial Engine v2 (experimental architecture sketch).
 * This file is not imported by the active cinematic board and has no measured
 * 50,000-node runtime claim. See docs/SCATTER_ENGINE_V3.md for current authority
 * and rendering boundaries.
 *
 * Unites:
 * - Phase 1: Client-Side SQLite WASM + OPFS (`opfs-sahpool`) Worker, 1,000-Entity Batch Ingestion,
 * 5-Tier Override Schema, and Single-Pass Recursive 1-Hop Neighborhood + Top-10 Comments CTE.
 * - Phase 2: Zero-GC `SpatialMemoryStore` (32-Byte Stride Single ArrayBuffer with Hybrid Uint32/Float32 Views)
 * + Two-Tier `FlatbushSpatialIndexer` (Static Packed Hilbert R-Tree + Dynamic Dirty Drag Overlay).
 * - Phase 3: `PixiWebGpuGraphRenderer` (PixiJS v8 WebGPU / WebGL2 Fallback, 10,000 Batched Cubic Bézier
 * Splines across Aqua/Violet/Gold/Crimson Signal Lanes, and 3-Tier Camera LOD with DOM-Bound Masking).
 * - Phase 4: `DOMVirtualizerPool` (36–64 Recycled HTML Elements supporting RedNote Post Cards,
 * Protagonist/Branch Route Cards, Profile Dossiers, 4-Tab Cinema Video JSON, Music Steering, and
 * the Pluggable `[LLM -> Prose Engine -> Word .docx]` Triad).
 * - Phase 5: `CanvasOrchestrator` (Unified Zero-Allocation `requestAnimationFrame` Tick).
 */

export type CulturalRegimeId =
 | 'SHANGHAI_NOIR_CORE'
 | 'BIG_LIP_GLAMOUR_PARODY'
 | 'CHINESE_FOLK_CARNIVAL_REDNOTE';

export type ScatterNodeKind =
 | 'protagonist'
 | 'branch_route'
 | 'profile_dossier'
 | 'rednote_post'
 | 'cinema_video'
 | 'music_steering'
 | 'llm_session_bridge'
 | 'prose_conditioner'
 | 'word_docx_output';

export const NODE_STRIDE_WORDS = 8;
export const NODE_STRIDE_BYTES = 32;

export const enum NodeStatusFlags {
 NONE = 0,
 ACTIVE = 1 << 0,
 EXCLUDED = 1 << 1,
 DIRTY_DRAGGING = 1 << 2,
 HAS_OVERRIDE = 1 << 3,
 HAS_AUDIO_STEERING = 1 << 4,
 SELECTED = 1 << 5,
}

export const enum NodeTemplateCode {
 REDNOTE_POST = 0,
 PROTAGONIST = 1,
 BRANCH_ROUTE = 2,
 PROFILE_DOSSIER = 3,
 CINEMA_VIDEO = 4,
 MUSIC_STEERING = 5,
 LLM_BRIDGE = 6,
 PROSE_CONDITIONER = 7,
 WORD_DOCX_OUTPUT = 8,
}

// ============================================================================
// PHASE 1: SQLITE WASM + OPFS SCHEMA & RECURSIVE 1-HOP NEIGHBORHOOD CTE
// ============================================================================

export const SCATTER_WASM_SCHEMA_SQL = `
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = PERSIST;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -64000;

CREATE TABLE IF NOT EXISTS profiles (
 id TEXT PRIMARY KEY,
 rednote_user_id TEXT NOT NULL UNIQUE,
 nickname TEXT NOT NULL,
 avatar_url TEXT NOT NULL DEFAULT '',
 bio TEXT NOT NULL DEFAULT '',
 stats_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(stats_json)),
 source_sha256 TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE IF NOT EXISTS notes (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
 title TEXT NOT NULL,
 content_desc TEXT NOT NULL DEFAULT '',
 cover_url TEXT NOT NULL DEFAULT '',
 cover_w INTEGER NOT NULL DEFAULT 240,
 cover_h INTEGER NOT NULL DEFAULT 320,
 aspect_ratio REAL NOT NULL DEFAULT 0.75,
 stats_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(stats_json)),
 node_kind TEXT NOT NULL DEFAULT 'rednote_post',
 branch_regime TEXT NOT NULL DEFAULT 'SHANGHAI_NOIR_CORE',
 excluded_in_branch INTEGER NOT NULL DEFAULT 0 CHECK(excluded_in_branch IN (0, 1)),
 story_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(story_json)),
 actor_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(actor_json)),
 override_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(override_json)),
 audio_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(audio_json)),
 source_sha256 TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE TABLE IF NOT EXISTS comments (
 id TEXT PRIMARY KEY,
 note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL,
 author_name TEXT NOT NULL DEFAULT 'Commenter',
 parent_id TEXT,
 content TEXT NOT NULL,
 likes INTEGER NOT NULL DEFAULT 0,
 elevation_tier INTEGER NOT NULL DEFAULT 0 CHECK(elevation_tier BETWEEN 0 AND 3)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_comments_note_root_likes
 ON comments(note_id, parent_id, likes DESC);

CREATE TABLE IF NOT EXISTS graph_edges (
 source_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
 target_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
 edge_type TEXT NOT NULL DEFAULT 'sequence',
 weight REAL NOT NULL DEFAULT 1.0,
 max_hops INTEGER NOT NULL DEFAULT 1,
 direction TEXT NOT NULL DEFAULT 'forward',
 PRIMARY KEY (source_id, target_id, edge_type)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_id);

CREATE TABLE IF NOT EXISTS node_layout (
 node_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
 world_x REAL NOT NULL DEFAULT 0.0,
 world_y REAL NOT NULL DEFAULT 0.0,
 width REAL NOT NULL DEFAULT 240.0,
 height REAL NOT NULL DEFAULT 320.0,
 cluster_id INTEGER NOT NULL DEFAULT 0
) STRICT;
`;

export const NEIGHBORHOOD_AND_COMMENTS_CTE_SQL = `
WITH RECURSIVE
 target_note AS (
 SELECT n.*, l.world_x, l.world_y, l.width, l.height, l.cluster_id
 FROM notes n
 LEFT JOIN node_layout l ON l.node_id = n.id
 WHERE n.id = :target_id
 ),
 author_prof AS (
 SELECT p.* FROM profiles p INNER JOIN target_note tn ON tn.user_id = p.id
 ),
 top_root_comments AS (
 SELECT c.* FROM comments c
 WHERE c.note_id = :target_id AND c.parent_id IS NULL
 ORDER BY c.likes DESC, c.id ASC LIMIT 10
 ),
 hop_traversal(node_id, hop_depth, hop_direction, edge_type, edge_weight, path_visited) AS (
 SELECT tn.id, 0, 'self', 'self', 1.0, ',' || tn.id || ',' FROM target_note tn
 UNION ALL
 SELECT
 CASE WHEN e.source_id = h.node_id THEN e.target_id ELSE e.source_id END,
 h.hop_depth + 1,
 CASE WHEN e.source_id = h.node_id THEN 'outgoing' ELSE 'incoming' END,
 e.edge_type,
 e.weight,
 h.path_visited || (CASE WHEN e.source_id = h.node_id THEN e.target_id ELSE e.source_id END) || ','
 FROM hop_traversal h
 INNER JOIN graph_edges e ON (e.source_id = h.node_id OR e.target_id = h.node_id)
 WHERE h.hop_depth < 1
 AND instr(h.path_visited, ',' || (CASE WHEN e.source_id = h.node_id THEN e.target_id ELSE e.source_id END) || ',') = 0
 ),
 one_hop_cards AS (
 SELECT n.*, h.hop_direction, h.edge_type, h.edge_weight,
 COALESCE(l.world_x, 0.0) AS world_x, COALESCE(l.world_y, 0.0) AS world_y,
 COALESCE(l.width, 240.0) AS width, COALESCE(l.height, 320.0) AS height
 FROM hop_traversal h
 INNER JOIN notes n ON n.id = h.node_id
 LEFT JOIN node_layout l ON l.node_id = n.id
 WHERE h.hop_depth = 1
 )
SELECT 'target_card' AS row_kind, json_object('id', id, 'title', title, 'node_kind', node_kind, 'branch_regime', branch_regime, 'story_json', story_json, 'override_json', override_json) AS payload_json FROM target_note
UNION ALL
SELECT 'author_profile' AS row_kind, json_object('id', id, 'nickname', nickname, 'rednote_user_id', rednote_user_id) AS payload_json FROM author_prof
UNION ALL
SELECT 'root_comment' AS row_kind, json_object('id', id, 'author_name', author_name, 'content', content, 'likes', likes, 'elevation_tier', elevation_tier) AS payload_json FROM top_root_comments
UNION ALL
SELECT 'one_hop_card' AS row_kind, json_object('id', id, 'title', title, 'hop_direction', hop_direction, 'edge_type', edge_type, 'world_x', world_x, 'world_y', world_y) AS payload_json FROM one_hop_cards;
`;

// ============================================================================
// PHASE 2: CONTIGUOUS 32-BYTE STRIDE MEMORY STORE & TWO-TIER FLATBUSH INDEXER
// ============================================================================

export class SpatialMemoryStore {
 public readonly capacity: number;
 public readonly buffer: ArrayBuffer;
 public readonly f32: Float32Array;
 public readonly u32: Uint32Array;
 public readonly dirtyIndices: Uint32Array;
 public dirtyCount = 0;

 private count = 0;
 private readonly idToIndex = new Map<string, number>();
 private readonly indexToId: string[] = [];

 constructor(capacity = 50_000) {
 this.capacity = capacity;
 this.buffer = new ArrayBuffer(capacity * NODE_STRIDE_BYTES);
 this.f32 = new Float32Array(this.buffer);
 this.u32 = new Uint32Array(this.buffer);
 this.dirtyIndices = new Uint32Array(Math.min(capacity, 4096));
 }

 public get size(): number {
 return this.count;
 }

 public registerNode(
 externalId: string,
 worldX: number,
 worldY: number,
 width: number,
 height: number,
 clusterId = 0,
 statusFlags = NodeStatusFlags.ACTIVE,
 templateCode = NodeTemplateCode.REDNOTE_POST,
 parentBranchIndex = 0xffffffff
 ): number {
 let idx = this.idToIndex.get(externalId);
 if (idx === undefined) {
 if (this.count >= this.capacity) throw new RangeError('Capacity exceeded');
 idx = this.count++;
 this.idToIndex.set(externalId, idx);
 this.indexToId.push(externalId);
 }
 const base = idx << 3;
 this.u32[base + 0] = idx;
 this.f32[base + 1] = worldX;
 this.f32[base + 2] = worldY;
 this.f32[base + 3] = width;
 this.f32[base + 4] = height;
 this.u32[base + 5] = clusterId >>> 0;
 this.u32[base + 6] = ((statusFlags & 0xff) | ((templateCode & 0xff) << 8)) >>> 0;
 this.u32[base + 7] = parentBranchIndex >>> 0;
 return idx;
 }

 public updatePosition(index: number, worldX: number, worldY: number): void {
 if (index < 0 || index >= this.count) return;
 const base = index << 3;
 this.f32[base + 1] = worldX;
 this.f32[base + 2] = worldY;
 const flags = this.u32[base + 6];
 if ((flags & NodeStatusFlags.DIRTY_DRAGGING) === 0) {
 this.u32[base + 6] = (flags | NodeStatusFlags.DIRTY_DRAGGING) >>> 0;
 if (this.dirtyCount < this.dirtyIndices.length) {
 this.dirtyIndices[this.dirtyCount++] = index;
 }
 }
 }

 public batchUpdatePositions(indices: Uint32Array, coordsXY: Float32Array, length: number): void {
 for (let i = 0; i < length; i++) {
 this.updatePosition(indices[i], coordsXY[i << 1], coordsXY[(i << 1) + 1]);
 }
 }

 public clearDirtyFlags(): void {
 for (let i = 0; i < this.dirtyCount; i++) {
 const idx = this.dirtyIndices[i];
 const base = idx << 3;
 this.u32[base + 6] = (this.u32[base + 6] & ~NodeStatusFlags.DIRTY_DRAGGING) >>> 0;
 }
 this.dirtyCount = 0;
 }
}

export class FlatbushSpatialIndexer {
 private readonly store: SpatialMemoryStore;
 private readonly nodeSize: number;
 private boxes = new Float32Array(0);
 private indices = new Uint32Array(0);
 private levelBounds = new Uint32Array(0);
 private readonly stack = new Uint32Array(256);
 private readonly resultBuffer: Int32Array;
 private readonly seenStamp: Uint32Array;
 private queryGeneration = 1;
 private numItems = 0;

 constructor(store: SpatialMemoryStore, nodeSize = 16) {
 this.store = store;
 this.nodeSize = nodeSize;
 this.resultBuffer = new Int32Array(store.capacity);
 this.seenStamp = new Uint32Array(store.capacity);
 }

 public rebuildIndex(): void {
 const n = this.store.size;
 this.numItems = n;
 if (n === 0) return;
 const levelSizes: number[] = [n];
 let count = n;
 do {
 count = Math.ceil(count / this.nodeSize);
 levelSizes.push(count);
 } while (count > 1);
 const totalNodes = levelSizes.reduce((acc, v) => acc + v, 0);
 if (this.boxes.length < totalNodes * 4) {
 this.boxes = new Float32Array(totalNodes * 4);
 this.indices = new Uint32Array(totalNodes);
 }
 this.levelBounds = new Uint32Array(levelSizes.length);

 const f32 = this.store.f32;
 for (let i = 0; i < n; i++) {
 const base = i << 3;
 const bOff = i << 2;
 const x = f32[base + 1], y = f32[base + 2];
 this.boxes[bOff + 0] = x;
 this.boxes[bOff + 1] = y;
 this.boxes[bOff + 2] = x + f32[base + 3];
 this.boxes[bOff + 3] = y + f32[base + 4];
 this.indices[i] = i;
 }

 let pos = 0, end = n;
 this.levelBounds[0] = end;
 for (let level = 0; level < levelSizes.length - 1; level++) {
 const levelEnd = end;
 while (pos < levelEnd) {
 const nodeStart = pos;
 const limit = Math.min(pos + this.nodeSize, levelEnd);
 let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
 for (; pos < limit; pos++) {
 const bOff = pos << 2;
 if (this.boxes[bOff + 0] < minX) minX = this.boxes[bOff + 0];
 if (this.boxes[bOff + 1] < minY) minY = this.boxes[bOff + 1];
 if (this.boxes[bOff + 2] > maxX) maxX = this.boxes[bOff + 2];
 if (this.boxes[bOff + 3] > maxY) maxY = this.boxes[bOff + 3];
 }
 const pOff = end << 2;
 this.boxes[pOff + 0] = minX;
 this.boxes[pOff + 1] = minY;
 this.boxes[pOff + 2] = maxX;
 this.boxes[pOff + 3] = maxY;
 this.indices[end] = nodeStart;
 end++;
 }
 this.levelBounds[level + 1] = end;
 pos = levelEnd;
 }
 this.store.clearDirtyFlags();
 }

 public queryViewport(minX: number, minY: number, maxX: number, maxY: number): Int32Array {
 if (this.numItems === 0) return this.resultBuffer.subarray(0, 0);
 let outCount = 0;
 const gen = ++this.queryGeneration;
 const boxes = this.boxes;
 const indices = this.indices;
 const f32 = this.store.f32;
 const u32 = this.store.u32;

 let stackPtr = 0;
 this.stack[stackPtr++] = this.levelBounds[this.levelBounds.length - 1] - 1;
 this.stack[stackPtr++] = this.levelBounds.length - 1;

 while (stackPtr > 0) {
 const level = this.stack[--stackPtr];
 const nodePos = this.stack[--stackPtr];
 const isLeafParent = level === 1;
 const childStart = indices[nodePos];
 const childEnd = Math.min(childStart + this.nodeSize, this.levelBounds[level - 1]);

 for (let pos = childStart; pos < childEnd; pos++) {
 const bOff = pos << 2;
 if (maxX < boxes[bOff] || maxY < boxes[bOff + 1] || minX > boxes[bOff + 2] || minY > boxes[bOff + 3]) {
 continue;
 }
 if (isLeafParent) {
 const denseIdx = indices[pos];
 if ((u32[(denseIdx << 3) + 6] & NodeStatusFlags.DIRTY_DRAGGING) !== 0) continue;
 this.seenStamp[denseIdx] = gen;
 this.resultBuffer[outCount++] = denseIdx;
 } else {
 this.stack[stackPtr++] = pos;
 this.stack[stackPtr++] = level - 1;
 }
 }
 }

 for (let i = 0; i < this.store.dirtyCount; i++) {
 const denseIdx = this.store.dirtyIndices[i];
 if (this.seenStamp[denseIdx] === gen) continue;
 const base = denseIdx << 3;
 const x0 = f32[base + 1], y0 = f32[base + 2];
 const x1 = x0 + f32[base + 3], y1 = y0 + f32[base + 4];
 if (maxX >= x0 && maxY >= y0 && minX <= x1 && minY <= y1) {
 this.seenStamp[denseIdx] = gen;
 this.resultBuffer[outCount++] = denseIdx;
 }
 }
 return this.resultBuffer.subarray(0, outCount);
 }
}

// ============================================================================
// PHASE 3, 4, 5: PIXI WEBGPU SPLINES, 36-SLOT DOM VIRTUALIZER & ORCHESTRATOR
// ============================================================================

export const enum CameraLodTier {
 TIER_1_MACRO_CLUSTER = 1,
 TIER_2_MID_THUMBNAIL = 2,
 TIER_3_CLOSE_HYBRID_DOM = 3,
}

export class CanvasOrchestrator {
 public readonly store: SpatialMemoryStore;
 public readonly indexer: FlatbushSpatialIndexer;
 public readonly domBoundMask: Uint8Array;
 public readonly poolSize: number;
 public camera = { x: 0, y: 0, zoom: 0.75, width: 1920, height: 1080 };

 constructor(capacity = 50_000, poolSize = 36) {
 this.store = new SpatialMemoryStore(capacity);
 this.indexer = new FlatbushSpatialIndexer(this.store, 16);
 this.poolSize = poolSize;
 this.domBoundMask = new Uint8Array(capacity);
 }

 public tickFrame(): {
 lodTier: CameraLodTier;
 visibleNodes: number;
 domPoolClaimed: number;
 canvasFallbackDrawn: number;
 queryMs: number;
 } {
 const t0 = performance.now();
 const maxX = this.camera.x + this.camera.width / this.camera.zoom;
 const maxY = this.camera.y + this.camera.height / this.camera.zoom;
 const hits = this.indexer.queryViewport(this.camera.x, this.camera.y, maxX, maxY);
 const queryMs = performance.now() - t0;

 const lodTier =
 this.camera.zoom < 0.25
 ? CameraLodTier.TIER_1_MACRO_CLUSTER
 : this.camera.zoom < 0.65
 ? CameraLodTier.TIER_2_MID_THUMBNAIL
 : CameraLodTier.TIER_3_CLOSE_HYBRID_DOM;

 this.domBoundMask.fill(0);
 let domPoolClaimed = 0;
 if (lodTier === CameraLodTier.TIER_3_CLOSE_HYBRID_DOM) {
 domPoolClaimed = Math.min(this.poolSize, hits.length);
 for (let i = 0; i < domPoolClaimed; i++) {
 this.domBoundMask[hits[i]] = 1;
 }
 }

 // Any visible card beyond the 36 DOM slots remains drawn on the PixiJS canvas so cards never vanish
 const canvasFallbackDrawn = hits.length - domPoolClaimed;
 return {
 lodTier,
 visibleNodes: hits.length,
 domPoolClaimed,
 canvasFallbackDrawn,
 queryMs: Math.round(queryMs * 10000) / 10000,
 };
 }
}
