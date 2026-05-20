/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from 'vitest';
import { NODE_DEFINITIONS, NODE_CATEGORIES, getNodeDefinition, getNodesByCategory } from '../utils/nodeDefinitions';

describe('nodeDefinitions', () => {
  it('all node types have valid definitions', () => {
    const entries = Object.entries(NODE_DEFINITIONS);
    expect(entries.length).toBeGreaterThan(0);

    for (const [type, def] of entries) {
      expect(def.type).toBe(type);
      expect(def.label).toBeTruthy();
      expect(def.description).toBeTruthy();
      expect(def.icon).toBeTruthy();
      expect(def.color).toMatch(/^#[0-9a-fA-F]{6}$/);
      expect(def.category).toBeTruthy();
      expect(def.defaultParams).toBeDefined();
      expect(def.ports).toBeDefined();
      expect(Array.isArray(def.ports.inputs)).toBe(true);
      expect(Array.isArray(def.ports.outputs)).toBe(true);
    }
  });

  it('all categories are valid', () => {
    const categoryIds = NODE_CATEGORIES.map(c => c.id);
    for (const def of Object.values(NODE_DEFINITIONS)) {
      expect(categoryIds).toContain(def.category);
    }
  });

  it('getNodeDefinition returns correct definition', () => {
    const def = getNodeDefinition('click');
    expect(def).toBeDefined();
    expect(def.type).toBe('click');
    expect(def.defaultParams).toHaveProperty('x');
    expect(def.defaultParams).toHaveProperty('y');
    expect(def.defaultParams).toHaveProperty('method');
  });

  it('getNodesByCategory returns correct nodes', () => {
    const systemNodes = getNodesByCategory('system');
    expect(systemNodes.length).toBeGreaterThan(0);
    for (const n of systemNodes) {
      expect(n.category).toBe('system');
    }
  });

  it('start node has no inputs', () => {
    const def = getNodeDefinition('start');
    expect(def.ports.inputs).toHaveLength(0);
    expect(def.ports.outputs.length).toBeGreaterThan(0);
  });

  it('end node has no outputs', () => {
    const def = getNodeDefinition('end');
    expect(def.ports.outputs).toHaveLength(0);
    expect(def.ports.inputs.length).toBeGreaterThan(0);
  });

  it('click node has correct default params', () => {
    const def = getNodeDefinition('click');
    expect(def.defaultParams).toMatchObject({
      x: expect.any(Number),
      y: expect.any(Number),
      method: expect.any(String),
      delay_after: expect.any(Number),
    });
  });

  it('condition node has yes/no output ports', () => {
    const def = getNodeDefinition('condition');
    const outputLabels = def.ports.outputs.map(o => o.label);
    expect(outputLabels).toContain('是');
    expect(outputLabels).toContain('否');
  });

  it('loop node has loop_body and loop_end output ports', () => {
    const def = getNodeDefinition('loop');
    const outputLabels = def.ports.outputs.map(o => o.label);
    expect(outputLabels).toContain('循环体');
    expect(outputLabels).toContain('结束后');
  });
});

describe('node parameter schemas', () => {
  it('all defaultParams values match their expected types', () => {
    for (const [type, def] of Object.entries(NODE_DEFINITIONS)) {
      const params = def.defaultParams;
      for (const [key, value] of Object.entries(params)) {
        if (key === 'label' || key === 'text' || key === 'description' || key === 'target' || key === 'name') {
          expect(typeof value).toBe('string');
        } else if (key === 'x' || key === 'y' || key === 'timeout' || key === 'duration' || key === 'delay_after') {
          expect(typeof value).toBe('number');
          if (type === 'click') {
            expect(value).not.toBeNaN();
          }
        }
      }
    }
  });
});

describe('NODE_CATEGORIES', () => {
  it('has all expected categories', () => {
    const ids = NODE_CATEGORIES.map(c => c.id);
    expect(ids).toContain('basic');
    expect(ids).toContain('image');
    expect(ids).toContain('control');
    expect(ids).toContain('device');
    expect(ids).toContain('data');
    expect(ids).toContain('system');
    expect(ids).toContain('tools');
  });

  it('each category has at least one node', () => {
    for (const cat of NODE_CATEGORIES) {
      const nodes = getNodesByCategory(cat.id);
      expect(nodes.length).toBeGreaterThan(0);
    }
  });
});
