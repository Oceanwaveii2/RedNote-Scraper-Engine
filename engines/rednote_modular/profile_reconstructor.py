"""Offline Profile and DOM Reconstructor.

Rebuilds complete, navigable local HTML and structured JSON views of captured
profiles and notes so they can be browsed completely offline without internet.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, List

HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{profile_name} - 小红书 / Red Note 本地离线档案</title>
  <style>
    :root {{
      --rednote-red: #FF2442;
      --bg-primary: #121212;
      --bg-card: #1e1e1e;
      --text-primary: #f0f0f0;
      --text-secondary: #999999;
      --border-color: #2c2c2c;
    }}
    body {{
      margin: 0;
      background: var(--bg-primary);
      color: var(--text-primary);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
    }}
    .header {{
      background: #181818;
      border-bottom: 1px solid var(--border-color);
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
    }}
    .logo {{
      color: var(--rednote-red);
      font-weight: 900;
      font-size: 20px;
      letter-spacing: -0.5px;
    }}
    .badge {{
      background: rgba(255, 36, 66, 0.15);
      color: var(--rednote-red);
      border: 1px solid var(--rednote-red);
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: bold;
    }}
    .container {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 24px 16px;
    }}
    .profile-card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 24px;
      display: flex;
      gap: 24px;
      align-items: center;
      margin-bottom: 28px;
    }}
    .avatar {{
      width: 88px;
      height: 88px;
      border-radius: 50%;
      object-fit: cover;
      border: 2px solid var(--rednote-red);
      background: #2a2a2a;
    }}
    .stats-row {{
      display: flex;
      gap: 20px;
      margin-top: 12px;
      font-size: 13px;
      color: var(--text-secondary);
    }}
    .stats-row strong {{
      color: var(--text-primary);
    }}
    .masonry-feed {{
      column-count: 3;
      column-gap: 16px;
    }}
    @media (max-width: 800px) {{
      .masonry-feed {{ column-count: 2; }}
    }}
    .post-card {{
      break-inside: avoid;
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      margin-bottom: 16px;
      overflow: hidden;
      transition: transform 0.15s ease;
    }}
    .post-card:hover {{
      transform: translateY(-2px);
      border-color: #444;
    }}
    .post-cover {{
      width: 100%;
      max-height: 400px;
      object-fit: cover;
      display: block;
      background: #000;
    }}
    .post-info {{
      padding: 12px;
    }}
    .post-title {{
      font-size: 13px;
      font-weight: 600;
      line-height: 1.4;
      margin-bottom: 6px;
      color: #fff;
    }}
    .post-meta {{
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      color: var(--text-secondary);
    }}
    .like-btn {{
      color: var(--rednote-red);
    }}
  </style>
</head>
<body>
  <header class="header">
    <div style="display:flex;align-items:center;gap:12px;">
      <span class="logo">小红书 · RED NOTE</span>
      <span class="badge">OFFLINE RECONSTRUCTION</span>
    </div>
    <div style="font-size:12px;color:var(--text-secondary);">
      Profile ID: <code>{profile_id}</code>
    </div>
  </header>

  <main class="container">
    <div class="profile-card">
      <img src="{avatar_url}" alt="Avatar" class="avatar" onerror="this.src='https://via.placeholder.com/88?text=User'">
      <div>
        <h1 style="margin:0 0 4px 0;font-size:22px;">{author_name}</h1>
        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">小红书号：{profile_id}</div>
        <p style="margin:0;font-size:13px;color:#ccc;line-height:1.5;">{bio}</p>
        <div class="stats-row">
          <span>关注 <strong>{following}</strong></span>
          <span>粉丝 <strong>{followers}</strong></span>
          <span>获赞与收藏 <strong>{likes}</strong></span>
          <span>笔记 <strong>{notes_count}</strong></span>
        </div>
      </div>
    </div>

    <h2 style="font-size:16px;margin-bottom:16px;border-left:4px solid var(--rednote-red);padding-left:8px;">
      全部笔记 ({notes_count})
    </h2>

    <div class="masonry-feed">
      {posts_html}
    </div>
  </main>
</body>
</html>
"""

class ProfileReconstructor:
    @staticmethod
    def render_offline_html(profile_data: Dict[str, Any], posts: List[Dict[str, Any]], target_file: Path) -> Path:
        """Generates a standalone offline reconstructed HTML file."""
        posts_html_chunks = []
        for p in posts:
            cover = p.get("coverImage") or (p.get("mediaFiles", [""])[0] if p.get("mediaFiles") else "")
            title = p.get("title") or "无题笔记"
            likes = p.get("likes") or 0
            author = p.get("authorName") or profile_data.get("author_name", "Author")
            
            chunk = f"""
            <div class="post-card">
              <img src="{cover}" alt="{title}" class="post-cover" onerror="this.style.display='none'">
              <div class="post-info">
                <div class="post-title">{title}</div>
                <div class="post-meta">
                  <span>@{author}</span>
                  <span class="like-btn">♥ {likes}</span>
                </div>
              </div>
            </div>
            """
            posts_html_chunks.append(chunk)

        full_html = HTML_TEMPLATE.format(
            profile_name=profile_data.get("author_name", "User"),
            profile_id=profile_data.get("profile_id", "profile"),
            avatar_url=profile_data.get("avatar_url", ""),
            author_name=profile_data.get("author_name", "User"),
            bio=profile_data.get("bio", "暂无个人简介"),
            following=profile_data.get("following_count", 0),
            followers=profile_data.get("followers_count", 0),
            likes=profile_data.get("likes_count", 0),
            notes_count=len(posts),
            posts_html="".join(posts_html_chunks)
        )

        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(full_html, encoding="utf-8")
        return target_file
