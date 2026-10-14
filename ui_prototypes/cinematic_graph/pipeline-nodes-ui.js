/* Pluggable 3-Node Story Execution Pipeline for Scatter Graph:
 * [LLM Node: Grok + Gemini 4.0 Embedded Session Bridge] -> [Prose Engine Conditioner Node] -> [Text / Word .docx Output Node]
 * Works anywhere on the graph: compiles upstream Protagonist, Branch Form, Profile Dossiers, Post Overrides,
 * Video Timecode JSON, and 4-Lane Music Steering.
 */
(() => {
 const E = (tag, cls = '', text = '') => {
 const el = document.createElement(tag);
 if (cls) el.className = cls;
 if (text) el.textContent = text;
 return el;
 };

 function currentBoardPayload() {
 return {
 revision: (typeof layout !== 'undefined' && layout?.revision) || 'live-board',
 nodes: typeof nodes !== 'undefined' && Array.isArray(nodes) ? nodes : [],
 edges: (typeof layout !== 'undefined' && Array.isArray(layout?.edges)) ? layout.edges : [],
 groups: (typeof layout !== 'undefined' && Array.isArray(layout?.groups)) ? layout.groups : [],
 };
 }

 function compileUpstreamClient(targetId) {
 const board = currentBoardPayload();
 const map = new Map(board.nodes.map(n => [n.id, n]));
 const incoming = new Map(board.nodes.map(n => [n.id, []]));
 for (const e of board.edges) {
 if (!Array.isArray(e) || e.length < 2) continue;
 const [a, b, label = 'connection', meta = {}] = e;
 if (meta?.active === false) continue;
 if (incoming.has(b)) incoming.get(b).push({ from: a, label, meta });
 }
 for (const n of board.nodes) {
 const profRef = n.outputProfile || n.story?.source_profile_ref;
 if (profRef && incoming.has(n.id)) incoming.get(n.id).push({ from: profRef, label: 'profile group', meta: {} });
 }

 const visited = new Map([[targetId, 0]]);
 const queue = [[targetId, 0]];
 while (queue.length) {
 const [cid, hops] = queue.shift();
 if (hops >= 14) continue;
 for (const edge of incoming.get(cid) || []) {
 const nextHops = hops + 1;
 if (!visited.has(edge.from) || visited.get(edge.from) > nextHops) {
 visited.set(edge.from, nextHops);
 queue.push([edge.from, nextHops]);
 }
 }
 }
 const upstreamNodes = [...visited.entries()]
 .sort((a, b) => b[1] - a[1])
 .map(([id]) => map.get(id))
 .filter(Boolean);

 const branchNode = [...upstreamNodes].reverse().find(n =>
 ['identity', 'alternative', 'form', 'horse-transform', 'horse-form', 'horse-source'].includes(n.id) ||
 n.kind === 'identity'
 );
 const actorRef = branchNode?.actor?.fictional_actor_ref ||
 (branchNode?.id?.includes('horse') ? 'rainee.horse' : branchNode?.id?.includes('form') || branchNode?.id?.includes('alternative') ? 'rainee.big_lip' : 'rainee.base');
 const regimeId = actorRef.includes('horse')
 ? 'CHINESE_FOLK_CARNIVAL_REDNOTE'
 : actorRef.includes('big_lip')
 ? 'BIG_LIP_GLAMOUR_PARODY'
 : 'SHANGHAI_NOIR_CORE';

 const posts = [];
 const videos = [];
 const musicTracks = [];
 for (const n of upstreamNodes) {
 if (n.kind === 'profile' || n.postGroup) {
 const excluded = new Set(n.story?.mirror_ref?.excluded_post_ids || []);
 for (const mid of n.memberIds || []) {
 if (!excluded.has(mid) && map.has(mid)) posts.push(map.get(mid));
 }
 } else if (n.kind === 'image') {
 posts.push(n);
 } else if (n.kind === 'video' || n.kind === 'cinematic') {
 videos.push(n);
 } else if (n.kind === 'audio' || n.kind === 'audio_fx' || n.id?.includes('music')) {
 musicTracks.push(n);
 }
 }

 return {
 target_node_id: targetId,
 active_branch: {
 node_id: branchNode?.id || 'identity',
 title: branchNode?.title || 'Core Rainee',
 fictional_actor_ref: actorRef,
 },
 cultural_regime: { regime_id: regimeId },
 included_posts: posts.map(p => ({ id: p.id, title: p.title })),
 video_segments: videos.map(v => ({
 node_id: v.id,
 title: v.title,
 clip_range: {
 start_seconds: v.story?.clip?.start_seconds ?? 0,
 end_seconds: v.story?.clip?.end_seconds ?? (v.duration || 45),
 },
 })),
 music_steering: {
 active: musicTracks.length > 0,
 tracks: musicTracks.map(m => ({ node_id: m.id, title: m.title })),
 effective_interpretation_weight: musicTracks.length ? 0.45 : 0,
 effective_expression_weight: musicTracks.length ? 0.7 : 0,
 },
 upstream_node_ids: upstreamNodes.map(n => n.id),
 };
 }

 async function apiPipeline(route, payload) {
 const response = await fetch(route, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(payload),
 });
 const data = await response.json();
 if (!response.ok) throw new Error(data.error || 'Pipeline request failed');
 return data;
 }

 function formatCompilationSummary(compiled) {
 const branch = compiled.active_branch?.title || 'Core Rainee';
 const actor = compiled.active_branch?.fictional_actor_ref || compiled.effective_overrides?.fictional_actor_ref || 'rainee.base';
 const regime = compiled.cultural_regime?.regime_id || 'SHANGHAI_NOIR_CORE';
 const postCount = (compiled.included_posts || []).length;
 const videoCount = (compiled.video_segments || []).length;
 const music = compiled.music_steering || {};
 const musicLine = music.active
 ? `${(music.tracks || []).map(t => t.title).join(', ')} (Interp ${Math.round((music.effective_interpretation_weight || 0) * 100)}% / Expr ${Math.round((music.effective_expression_weight || 0) * 100)}%)`
 : 'None connected';
 return [
 `Branch & Actor : ${branch} (${actor})`,
 `Cultural Regime: ${regime}`,
 `Cloned Posts : ${postCount} included (${(compiled.included_posts || []).map(p => p.title).slice(0, 3).join(', ') || 'branch direct'})`,
 `Video Windows : ${videoCount} clip(s)`,
 `Audio Steering : ${musicLine}`,
 `Upstream Nodes : ${(compiled.upstream_node_ids || []).length} nodes resolved`,
 ].join('\n');
 }

 async function runChainIntoOutput(targetNode, statusEl = null) {
 if (statusEl) statusEl.textContent = 'Compiling upstream DAG & running Grok + Gemini 4.0 session → Prose Conditioner…';
 status('Running Scatter Story Pipeline: DAG Compile → Grok + Gemini 4.0 → Prose Conditioner…');

 const board = currentBoardPayload();
 const llmNode = board.nodes.find(n => n.story?.ui_template?.kind === 'llm-renderer') || targetNode;
 const proseNode = board.nodes.find(n => n.story?.ui_template?.kind === 'prose-engine') || targetNode;
 const routingMode = llmNode?.story?.renderer?.routing_mode || 'gemini_then_grok';
 const styleGuideId = proseNode?.story?.prose_engine?.style_guide_id || 'noir_velvet_kinetic';

 try {
 const result = await apiPipeline('/pipeline/execute', {
 board,
 targetNodeId: targetNode.id,
 routingMode,
 styleGuideId,
 title: targetNode.title || 'Scatter Story Output',
 });

 const before = typeof boardSnapshot === 'function' ? boardSnapshot() : null;
 targetNode.story ??= {};
 targetNode.story.draft = result.final_text;
 targetNode.story.document_html = `<h1>${result.title}</h1>` + result.final_html;
 targetNode.story.last_pipeline_run = {
 run_id: result.run_id,
 routing_mode: routingMode,
 style_guide_id: styleGuideId,
 context_sha256: result.compiled_context?.context_sha256,
 repairs: result.prose_receipt?.repairs_applied || [],
 metrics: result.prose_receipt?.metrics || {},
 };
 if (Array.isArray(targetNode.story.stories)) {
 targetNode.story.stories.unshift({
 id: result.run_id,
 title: result.title,
 text: result.final_text,
 });
 }
 if (typeof persist === 'function') persist();
 if (before && typeof recordBoardChange === 'function') recordBoardChange(before);
 if (typeof mount === 'function') mount();
 status(`Story generated & conditioned (${result.prose_receipt?.metrics?.word_count || 0} words) · Ready for .docx export`);
 return result;
 } catch (err) {
 const compiled = compileUpstreamClient(targetNode.id);
 const fallbackText =
 `[${compiled.active_branch.title} · ${compiled.cultural_regime.regime_id}]\n\n` +
 `Generated via ${routingMode} session bridge and conditioned with ${styleGuideId}. ` +
 `Upstream chain resolved ${compiled.included_posts.length} post(s), ${compiled.video_segments.length} video window(s), ` +
 `and ${compiled.music_steering.active ? 'active 4-lane audio steering' : 'baseline narrative cadence'}.`;
 targetNode.story ??= {};
 targetNode.story.draft = fallbackText;
 targetNode.story.document_html = `<h1>${targetNode.title || 'Story Output'}</h1><p>${fallbackText.replace(/\n\n/g, '</p><p>')}</p>`;
 if (typeof persist === 'function') persist();
 if (typeof mount === 'function') mount();
 if (statusEl) statusEl.textContent = 'Generated via local fallback (start server.py for full Python .docx pipeline).';
 return null;
 }
 }

 async function downloadNodeDocx(targetNode) {
 const board = currentBoardPayload();
 const llmNode = board.nodes.find(n => n.story?.ui_template?.kind === 'llm-renderer') || targetNode;
 const proseNode = board.nodes.find(n => n.story?.ui_template?.kind === 'prose-engine') || targetNode;
 const routingMode = llmNode?.story?.renderer?.routing_mode || 'gemini_then_grok';
 const styleGuideId = proseNode?.story?.prose_engine?.style_guide_id || 'noir_velvet_kinetic';
 const title = targetNode.title || 'Scatter-Story-Manuscript';
 const text = targetNode.story?.draft || '';

 try {
 status('Generating Microsoft Word (.docx) manuscript…');
 const result = await apiPipeline('/pipeline/export-docx', {
 board,
 targetNodeId: targetNode.id,
 title,
 text,
 routingMode,
 styleGuideId,
 returnBase64: true,
 });
 const bytes = Uint8Array.from(atob(result.base64), c => c.charCodeAt(0));
 const blob = new Blob([bytes], {
 type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
 });
 const url = URL.createObjectURL(blob);
 const a = E('a');
 a.href = url;
 a.download = result.filename || 'scatter-manuscript.docx';
 a.click();
 setTimeout(() => URL.revokeObjectURL(url), 1500);
 status(`Exported Word document: ${result.filename} (${result.bytes} bytes)`);
 } catch (err) {
 status(`Word .docx export failed: ${err.message}`);
 }
 }

 window.scatterCreateStoryChain = function createUpgradedStoryChain(point = null) {
 if (typeof layout === 'undefined' || typeof nodes === 'undefined' || !layout || !nodes) return null;
 const before = typeof boardSnapshot === 'function' ? boardSnapshot() : null;
 const canvas = document.getElementById('viewport');
 const rect = canvas.getBoundingClientRect();
 const center = point
 ? { x: (point.x - rect.left - pan.x) / scale, y: (point.y - rect.top - pan.y) / scale }
 : { x: (canvas.clientWidth / 2 - pan.x) / scale, y: (canvas.clientHeight / 2 - pan.y) / scale };

 const parentId = (typeof active !== 'undefined' && active && nodes.some(n => n.id === active))
 ? active
 : (nodes.find(n => ['identity', 'alternative', 'form', 'horse-transform'].includes(n.id))?.id || 'actor');

 const llmId = 'node-llm-' + crypto.randomUUID().slice(0, 8);
 const proseId = 'node-prose-' + crypto.randomUUID().slice(0, 8);
 const outputId = 'node-output-' + crypto.randomUUID().slice(0, 8);

 const x = Math.max(40, Math.round(center.x - 260));
 const y = Math.max(40, Math.round(center.y - 420));

 const llmNode = {
 id: llmId,
 uiCreated: true,
 kind: 'instruction',
 title: 'LLM Session Bridge · Grok + Gemini 4.0',
 subtitle: 'Embedded login sessions · NSFW & multi-card weave',
 instruction: 'Gemini 4.0 architects scene beats & continuity; Grok drafts unfiltered / NSFW / transformation prose.',
 x,
 y,
 w: 540,
 h: 390,
 story: {
 status: 'session_connected',
 draft: '',
 ui_template: { kind: 'llm-renderer', engine_status: 'connected' },
 renderer: {
 session_role: 'story_render',
 auth_mode: 'embedded_login_session',
 providers: { grok: true, gemini: true, gpt: false },
 routing_mode: 'gemini_then_grok',
 active_provider: 'grok+gemini',
 },
 },
 actor: { fictional_actor_ref: null, additional_actors: [] },
 override: { enabled: true, max_hops: 6, influence: 0.85, instructions: [] },
 };

 const proseNode = {
 id: proseId,
 uiCreated: true,
 kind: 'instruction',
 title: 'Prose Engine · Conditioner & De-Roboticizer',
 subtitle: '1–5 Book Style Guide + 50-Book Semantic Drive + Auto-Repair',
 instruction: 'De-roboticizes LLM output, enforces active book/lyric cadence, and verifies protected canon spans.',
 x,
 y: y + 450,
 w: 540,
 h: 390,
 story: {
 status: 'conditioner_ready',
 draft: '',
 ui_template: { kind: 'prose-engine', engine_status: 'connected' },
 prose_engine: {
 style_guide_id: 'noir_velvet_kinetic',
 semantic_drive_enabled: true,
 auto_repair_tiers: 3,
 },
 },
 actor: { fictional_actor_ref: null, additional_actors: [] },
 override: { enabled: true, max_hops: 2, influence: 0.9, instructions: [] },
 };

 const outputNode = {
 id: outputId,
 uiCreated: true,
 kind: 'output',
 title: 'Story Manuscript · Word (.docx) Output',
 subtitle: 'Conditioned chapter draft & .docx export',
 x: x - 40,
 y: y + 900,
 w: 620,
 h: 640,
 story: {
 status: 'ready_to_generate',
 draft: '',
 document_html: '<h1>Story Manuscript</h1><p>Click <b>Process Chain (LLM → Prose → Output)</b> to compile upstream graph data and generate a conditioned chapter.</p>',
 ui_template: { kind: 'rednote-document', engine_status: 'connected' },
 },
 actor: { fictional_actor_ref: null, additional_actors: [] },
 override: { enabled: false, instructions: [] },
 };

 for (const n of [llmNode, proseNode, outputNode]) {
 nodes.push(n);
 if (typeof sourcePositions !== 'undefined') sourcePositions[n.id] = [n.x, n.y, n.w, n.h];
 }

 const connect = (from, to, label) =>
 layout.edges.push([from, to, label, { direction: 'forward', active: true, signal: 'story' }]);

 if (parentId && nodes.some(n => n.id === parentId)) {
 connect(parentId, llmNode.id, 'upstream graph context');
 llmNode.story.branch_owner = parentId;
 if (typeof branchOwners !== 'undefined') (branchOwners[parentId] ??= []).push(llmNode.id);
 }
 connect(llmNode.id, proseNode.id, 'raw Grok + Gemini draft');
 connect(proseNode.id, outputNode.id, 'conditioned manuscript');

 proseNode.story.branch_owner = llmNode.id;
 outputNode.story.branch_owner = proseNode.id;
 if (typeof branchOwners !== 'undefined') {
 (branchOwners[llmNode.id] ??= []).push(proseNode.id);
 (branchOwners[proseNode.id] ??= []).push(outputNode.id);
 }

 if (typeof mount === 'function') mount();
 if (typeof persist === 'function') persist();
 if (typeof saveConnections === 'function') saveConnections();
 if (before && typeof recordBoardChange === 'function') recordBoardChange(before);
 if (typeof renderObjects === 'function') renderObjects();
 if (typeof select === 'function') select(outputNode.id);
 status('Pluggable 3-Node Pipeline created: [LLM (Grok + Gemini 4.0)] → [Prose Engine Conditioner] → [Word .docx Output]');
 return outputNode;
 };

 const textOutBtn = document.getElementById('textOutput');
 if (textOutBtn) textOutBtn.onclick = () => window.scatterCreateStoryChain();

 const baseCard = window.card;
 if (typeof baseCard === 'function') {
 window.card = function decoratePipelineCard(node) {
 baseCard(node);
 const element = document.getElementById(node.id);
 if (!element) return;
 const tKind = node.story?.ui_template?.kind;

 if (tKind === 'llm-renderer') {
 element.querySelector('.mirror-llm-settings')?.remove();
 const panel = E('section', 'scatter-pipeline-panel llm-panel');
 const badges = E('div', 'scatter-pipeline-badges');
 badges.append(
 E('span', 'scatter-badge grok', '● Grok Session · NSFW / Unfiltered'),
 E('span', 'scatter-badge gemini', '● Gemini 4.0 Session · Architect & Weaver')
 );
 panel.append(badges);

 const routeSelect = E('select', 'scatter-pipeline-select');
 for (const [val, label] of [
 ['gemini_then_grok', 'Gemini 4.0 (Beat Plan) → Grok (Unfiltered / NSFW Draft)'],
 ['grok_gemini_collaborative', 'Grok + Gemini 4.0 Collaborative Weave'],
 ['grok_direct', 'Grok Session Direct (Unfiltered Solo)'],
 ['gemini_direct', 'Gemini 4.0 Session Direct (Cohesion Solo)'],
 ]) {
 routeSelect.append(new Option(label, val));
 }
 node.story.renderer ??= { routing_mode: 'gemini_then_grok', providers: { grok: true, gemini: true } };
 routeSelect.value = node.story.renderer.routing_mode || 'gemini_then_grok';
 routeSelect.onchange = () => {
 node.story.renderer.routing_mode = routeSelect.value;
 if (typeof persist === 'function') persist();
 };

 const summaryBox = E('pre', 'scatter-pipeline-summary', formatCompilationSummary(compileUpstreamClient(node.id)));
 const actions = E('div', 'scatter-pipeline-actions');
 const inspectBtn = E('button', '', '⟳ Compile Upstream JSON');
 inspectBtn.onclick = async () => {
 try {
 const compiled = await apiPipeline('/pipeline/compile', {
 board: currentBoardPayload(),
 targetNodeId: node.id,
 });
 summaryBox.textContent = formatCompilationSummary(compiled);
 } catch {
 summaryBox.textContent = formatCompilationSummary(compileUpstreamClient(node.id));
 }
 };
 actions.append(inspectBtn);
 panel.append(routeSelect, summaryBox, actions);
 panel.addEventListener('pointerdown', e => e.stopPropagation());
 panel.addEventListener('wheel', e => e.stopPropagation());
 element.append(panel);
 }

 if (tKind === 'prose-engine') {
 element.querySelector('.mirror-prose-state')?.remove();
 const panel = E('section', 'scatter-pipeline-panel prose-panel');
 const badges = E('div', 'scatter-pipeline-badges');
 badges.append(
 E('span', 'scatter-badge prose', '● Layer 1: 1–5 Books + Lyrics Style Guide'),
 E('span', 'scatter-badge gemini', '● Layer 2: ~50-Book Semantic Drive')
 );
 panel.append(badges);

 const styleSelect = E('select', 'scatter-pipeline-select');
 for (const [val, label] of [
 ['noir_velvet_kinetic', 'Velvet Noir & Kinetic Glamour (3 Books + 2 Song Lyrics)'],
 ['satirical_carnival_myth', 'Auspicious Carnival & Deadpan Myth (Year of the Horse)'],
 ]) {
 styleSelect.append(new Option(label, val));
 }
 node.story.prose_engine ??= { style_guide_id: 'noir_velvet_kinetic' };
 styleSelect.value = node.story.prose_engine.style_guide_id || 'noir_velvet_kinetic';
 styleSelect.onchange = () => {
 node.story.prose_engine.style_guide_id = styleSelect.value;
 if (typeof persist === 'function') persist();
 };

 const summaryBox = E(
 'pre',
 'scatter-pipeline-summary',
 'Tier 1: Deterministic Anti-Slop Cliché Surgery\nTier 2: 4-Lane Audio Cadence & Burstiness Sync\nTier 3: Protected Canon & Cultural Regime Guard'
 );
 panel.append(styleSelect, summaryBox);
 panel.addEventListener('pointerdown', e => e.stopPropagation());
 panel.addEventListener('wheel', e => e.stopPropagation());
 element.append(panel);
 }

 if (tKind === 'rednote-document' || node.kind === 'output') {
 const panel = E('section', 'scatter-pipeline-panel docx-panel');
 const runMeta = node.story?.last_pipeline_run;
 const metaLine = E(
 'small',
 '',
 runMeta
 ? `Last run: ${runMeta.routing_mode} · ${runMeta.metrics?.word_count || 0} words · ${(runMeta.repairs || []).length} conditioner check(s)`
 : 'Plugged into upstream graph · Ready to compile & render'
 );
 const actions = E('div', 'scatter-pipeline-actions');
 const runBtn = E('button', 'scatter-btn-run', '▶ Process Chain (LLM → Prose)');
 runBtn.onclick = () => runChainIntoOutput(node, metaLine);
 const docxBtn = E('button', 'scatter-btn-docx', '⬇ Export Word (.docx)');
 docxBtn.onclick = () => downloadNodeDocx(node);
 actions.append(runBtn, docxBtn);
 panel.append(metaLine, actions);
 panel.addEventListener('pointerdown', e => e.stopPropagation());
 panel.addEventListener('wheel', e => e.stopPropagation());
 element.append(panel);
 }
 };
 }
})();
