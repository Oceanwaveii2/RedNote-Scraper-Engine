/* Spatial ownership for the visual board. Mirror records and authored node JSON stay elsewhere. */
(() => {
  'use strict';

  class ScatterSpatialCore {
    constructor(cellSize = 768) {
      this.cellSize = cellSize;
      this.bounds = new Map();
      this.cells = new Map();
      this.membership = new Map();
      this.stamps = new Map();
      this.generation = 0;
    }

    cellKeys(x, y, w, h) {
      const left = Math.floor(x / this.cellSize);
      const top = Math.floor(y / this.cellSize);
      const right = Math.floor((x + Math.max(w, 1)) / this.cellSize);
      const bottom = Math.floor((y + Math.max(h, 1)) / this.cellSize);
      const keys = [];
      for (let cy = top; cy <= bottom; cy++) {
        for (let cx = left; cx <= right; cx++) keys.push(`${cx},${cy}`);
      }
      return keys;
    }

    upsert(id, x, y, w, h) {
      if (!id || ![x, y, w, h].every(Number.isFinite) || w < 0 || h < 0) return false;
      const previous = this.bounds.get(id);
      if (previous && previous.x === x && previous.y === y && previous.w === w && previous.h === h) return false;
      const oldKeys = this.membership.get(id) || [];
      const newKeys = this.cellKeys(x, y, w, h);
      const newSet = new Set(newKeys);
      for (const key of oldKeys) {
        if (newSet.has(key)) continue;
        const cell = this.cells.get(key);
        cell?.delete(id);
        if (cell?.size === 0) this.cells.delete(key);
      }
      const oldSet = new Set(oldKeys);
      for (const key of newKeys) {
        if (oldSet.has(key)) continue;
        let cell = this.cells.get(key);
        if (!cell) this.cells.set(key, cell = new Set());
        cell.add(id);
      }
      this.membership.set(id, newKeys);
      this.bounds.set(id, { x, y, w, h });
      return true;
    }

    remove(id) {
      for (const key of this.membership.get(id) || []) {
        const cell = this.cells.get(key);
        cell?.delete(id);
        if (cell?.size === 0) this.cells.delete(key);
      }
      this.membership.delete(id);
      this.bounds.delete(id);
      this.stamps.delete(id);
    }

    replace(nodes) {
      const retained = new Set();
      for (const node of nodes) {
        retained.add(node.id);
        this.upsert(node.id, node.x, node.y, node.w, node.h);
      }
      for (const id of this.bounds.keys()) if (!retained.has(id)) this.remove(id);
    }

    query(x, y, w, h, padding = 0) {
      const left = x - padding;
      const top = y - padding;
      const right = x + w + padding;
      const bottom = y + h + padding;
      const result = [];
      const generation = ++this.generation;
      for (const key of this.cellKeys(left, top, right - left, bottom - top)) {
        for (const id of this.cells.get(key) || []) {
          if (this.stamps.get(id) === generation) continue;
          this.stamps.set(id, generation);
          const box = this.bounds.get(id);
          if (box && box.x <= right && box.x + box.w >= left && box.y <= bottom && box.y + box.h >= top) result.push(id);
        }
      }
      return result;
    }
  }

  window.ScatterSpatialCore = ScatterSpatialCore;
})();
