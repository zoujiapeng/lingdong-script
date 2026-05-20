import { describe, it, expect } from 'vitest';
import { BASE_PRIMITIVES, COMPOSITE_TEMPLATES } from '../utils/htnTemplateDefs';

describe('BASE_PRIMITIVES', () => {
  it('has at least 5 primitives', () => {
    expect(BASE_PRIMITIVES.length).toBeGreaterThanOrEqual(5);
  });

  it('each primitive has required fields', () => {
    const ids = new Set<string>();
    for (const p of BASE_PRIMITIVES) {
      expect(p.id).toBeTruthy();
      expect(p.name).toBeTruthy();
      expect(p.primitive).toBeDefined();
      expect(p.primitive!.dslKeyword).toBeTruthy();
      expect(p.primitive!.flowType).toBeTruthy();
      expect(p.primitive!.paramBinding).toBeDefined();
      expect(typeof p.primitive!.paramBinding).toBe('object');
      expect(p.category).toBeTruthy();
      expect(Array.isArray(p.tags)).toBe(true);
      // No duplicate IDs
      expect(ids.has(p.id!)).toBe(false);
      ids.add(p.id!);
    }
  });

  it('each primitive has valid parameters', () => {
    for (const p of BASE_PRIMITIVES) {
      expect(Array.isArray(p.parameters)).toBe(true);
      for (const param of p.parameters || []) {
        expect(param.name).toBeTruthy();
        expect(param.type).toMatch(/^(number|string|boolean|region)$/);
        expect(param.description).toBeTruthy();
      }
    }
  });

  it('all flowType values are lowercase strings', () => {
    for (const p of BASE_PRIMITIVES) {
      expect(p.primitive!.flowType).toMatch(/^[a-z_]+$/);
    }
  });
});

describe('COMPOSITE_TEMPLATES', () => {
  it('has at least 3 composite templates', () => {
    expect(COMPOSITE_TEMPLATES.length).toBeGreaterThanOrEqual(3);
  });

  it('each composite has name and children', () => {
    const ids = new Set<string>();
    for (const t of COMPOSITE_TEMPLATES) {
      expect(t.id).toBeTruthy();
      expect(t.name).toBeTruthy();
      expect(Array.isArray(t.children)).toBe(true);
      expect(t.children!.length).toBeGreaterThan(0);
      expect(ids.has(t.id!)).toBe(false);
      ids.add(t.id!);
    }
  });

  it('children reference valid child IDs', () => {
    const allIds = new Set([
      ...BASE_PRIMITIVES.map(p => p.id),
      ...COMPOSITE_TEMPLATES.map(t => t.id),
    ]);
    for (const t of COMPOSITE_TEMPLATES) {
      for (const child of t.children || []) {
        if (child.childId && allIds.has(child.childId)) {
          // Valid reference
        } else if (child.childId) {
          // Could be a placeholder like "param:xxx"
          expect(child.childId.startsWith('param:')).toBe(true);
        }
      }
    }
  });

  it('no ID collision between primitives and composites', () => {
    const primIds = new Set(BASE_PRIMITIVES.map(p => p.id));
    for (const t of COMPOSITE_TEMPLATES) {
      expect(primIds.has(t.id)).toBe(false);
    }
  });
});
