"""Ingestion Graph and Provenance Engine.

Models and records the immutable lineage from raw Xiaohongshu social media
into translations, classifications, Asset Cards, and Scatter Game Engine nodes.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

@dataclass
class GraphNode:
    id: str
    type: str  # 'profile', 'post', 'media', 'translation', 'classification', 'asset_card', 'scatter_node', 'simulation_data'
    label: str
    provenance: str  # human-readable source explanation
    payload: Dict[str, Any] = field(default_factory=dict)
    immutable: bool = True

@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    relation: str  # 'extracts_into', 'translates_into', 'classifies_as', 'bridges_to', 'generates'

class IngestionGraph:
    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: List[GraphEdge] = []

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, source_id: str, target_id: str, relation: str) -> None:
        self.edges.append(GraphEdge(source_id=source_id, target_id=target_id, relation=relation))

    def build_post_pipeline(self, parsed_post: Dict[str, Any], asset_card: Dict[str, Any], scatter_node_id: Optional[str] = None) -> None:
        """Builds standard 7-tier provenance pipeline as specified on Page 4."""
        post_id = parsed_post["postId"]
        profile_id = parsed_post["profileId"] or "profile_root"
        
        # 1. Profile Node
        p_node_id = f"node_prof_{profile_id}"
        if p_node_id not in self.nodes:
            self.add_node(GraphNode(
                id=p_node_id,
                type="profile",
                label=f"Profile: @{parsed_post.get('authorName', 'Author')}",
                provenance=f"Scraped from Xiaohongshu profile {profile_id}",
                payload={"profileId": profile_id, "url": parsed_post.get("postUrl", "")}
            ))

        # 2. Post Node
        post_node_id = f"node_post_{post_id}"
        self.add_node(GraphNode(
            id=post_node_id,
            type="post",
            label=f"Note: {parsed_post.get('title') or post_id}",
            provenance=f"Immutable raw DOM capture from {parsed_post.get('postUrl')}",
            payload={"postId": post_id, "likes": parsed_post.get("likes", 0)}
        ))
        self.add_edge(p_node_id, post_node_id, "contains_post")

        # 3. Raw Content Node (Media & Text)
        content_node_id = f"node_content_{post_id}"
        self.add_node(GraphNode(
            id=content_node_id,
            type="media",
            label=f"Evidence: {len(parsed_post.get('mediaFiles', []))} files, {len(parsed_post.get('comments', []))} comments",
            provenance="Extracted media binaries and loaded public comments (author IDs omitted)",
            payload={"mediaFiles": parsed_post.get("mediaFiles", []), "chineseCaption": parsed_post.get("description", "")}
        ))
        self.add_edge(post_node_id, content_node_id, "extracts_evidence")

        # 4. Translation Node
        trans_node_id = f"node_trans_{post_id}"
        self.add_node(GraphNode(
            id=trans_node_id,
            type="translation",
            label=f"Bilingual Translation Layer",
            provenance="Cached non-destructive English translation of title, caption, and comments",
            payload={"englishTitle": parsed_post.get("titleEnglish", ""), "englishDesc": parsed_post.get("descriptionEnglish", "")}
        ))
        self.add_edge(content_node_id, trans_node_id, "translates_into")

        # 5. Classification Node
        class_node_id = f"node_class_{post_id}"
        self.add_node(GraphNode(
            id=class_node_id,
            type="classification",
            label=f"Taxonomy: {asset_card.get('primary_class')} / {asset_card.get('character_association')}",
            provenance="Auto-grouped subject matter with user-supervised tag confirmation",
            payload={"primaryClass": asset_card.get("primary_class"), "character": asset_card.get("character_association")}
        ))
        self.add_edge(trans_node_id, class_node_id, "classifies_as")

        # 6. Asset Card Node
        card_node_id = f"node_card_{asset_card.get('card_id')}"
        self.add_node(GraphNode(
            id=card_node_id,
            type="asset_card",
            label=f"Asset Card #{asset_card.get('card_id')}",
            provenance="Normalized engine-ready asset with separate evidence, content, and classification layers",
            payload=asset_card
        ))
        self.add_edge(class_node_id, card_node_id, "assembles_card")

        # 7. Scatter Node & Simulation Data (if bridged)
        if scatter_node_id:
            scat_node_id = f"node_scatter_{scatter_node_id}"
            self.add_node(GraphNode(
                id=scat_node_id,
                type="scatter_node",
                label=f"Scatter Node: {scatter_node_id}",
                provenance=f"Connected into Scatter Graph AI Volition Engine",
                payload={"scatterNodeId": scatter_node_id}
            ))
            self.add_edge(card_node_id, scat_node_id, "bridges_to")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "label": n.label,
                    "provenance": n.provenance,
                    "payload": n.payload,
                    "immutable": n.immutable
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {"source": e.source_id, "target": e.target_id, "relation": e.relation}
                for e in self.edges
            ]
        }
