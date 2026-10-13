"""Red Note Media and Profile Workspace Manager.

Manages isolated filesystem workspaces for each downloaded profile,
preventing dump of unrelated profiles into one directory.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List

class WorkspaceManager:
    def __init__(self, root_dir: Path):
        self.root_dir = Path(root_dir)
        self.profiles_dir = self.root_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def get_profile_workspace(self, profile_id: str) -> Path:
        """Returns or creates the isolated directory structure for a profile."""
        clean_id = str(profile_id).strip()
        p_dir = self.profiles_dir / clean_id
        
        # Directive page 2 workspace layout:
        (p_dir / "source").mkdir(parents=True, exist_ok=True)
        (p_dir / "posts").mkdir(parents=True, exist_ok=True)
        (p_dir / "images").mkdir(parents=True, exist_ok=True)
        (p_dir / "videos").mkdir(parents=True, exist_ok=True)
        (p_dir / "comments").mkdir(parents=True, exist_ok=True)
        (p_dir / "emojis").mkdir(parents=True, exist_ok=True)
        (p_dir / "translations").mkdir(parents=True, exist_ok=True)
        (p_dir / "reconstructed_site").mkdir(parents=True, exist_ok=True)
        (p_dir / "scatter_assets").mkdir(parents=True, exist_ok=True)

        return p_dir

    def save_profile_metadata(self, profile_id: str, data: Dict[str, Any]) -> Path:
        p_dir = self.get_profile_workspace(profile_id)
        target = p_dir / "profile.json"
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def load_profile_metadata(self, profile_id: str) -> Optional[Dict[str, Any]]:
        target = self.get_profile_workspace(profile_id) / "profile.json"
        if target.exists():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def store_post_record(self, profile_id: str, post_id: str, post_data: Dict[str, Any]) -> Path:
        p_dir = self.get_profile_workspace(profile_id)
        post_file = p_dir / "posts" / f"{post_id}.json"
        post_file.write_text(json.dumps(post_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return post_file

    def calculate_file_hash(self, file_path: Path) -> str:
        """Calculates SHA-256 for media integrity verification."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def list_profiles(self) -> List[str]:
        if not self.profiles_dir.exists():
            return []
        return [p.name for p in self.profiles_dir.iterdir() if p.is_dir()]
