import { describe, it, expect } from 'vitest';
import { featureToNodeItem, getLibraryFeatures, getFeatureCategoryLabel } from '../utils/featureRegistry';
import type { FeatureDefinition } from '../types';

function makeFeature(overrides: Partial<FeatureDefinition> & { name: string }): FeatureDefinition {
  return {
    display_name: 'Test Feature',
    description: 'A test feature description',
    category: 'action',
    returns: '',
    hidden: false,
    deprecated: false,
    params: [],
    visual: undefined,
    dsl_keyword: undefined,
    dsl_template: undefined,
    ...overrides,
  } as FeatureDefinition;
}

describe('featureRegistry', () => {
  it('featureToNodeItem converts basic feature', () => {
    const item = featureToNodeItem(makeFeature({ name: 'click_target', display_name: '点击目标' }));
    expect(item.type).toBe('tool_call');
    expect(item.label).toBe('点击目标');
    expect(item.feature_name).toBe('click_target');
    expect(item.category).toBe('device');
  });

  it('featureToNodeItem uses visual metadata', () => {
    const item = featureToNodeItem(makeFeature({
      name: 'ocr_recognize',
      visual: { icon: 'Scan', color: '#ff0000', ports_in: [], ports_out: [] },
    }));
    expect(item.icon).toBe('Scan');
    expect(item.color).toBe('#ff0000');
  });

  it('featureToNodeItem defaults icon and color', () => {
    const item = featureToNodeItem(makeFeature({ name: 'bare' }));
    expect(item.icon).toBe('Wrench');
    expect(item.color).toBe('#8b5cf6');
  });

  it('featureToNodeItem truncates long description', () => {
    const desc = '【测试】' + 'x'.repeat(200);
    const item = featureToNodeItem(makeFeature({ name: 'long', description: desc }));
    expect(item.description.length).toBeLessThanOrEqual(80);
    expect(item.description).not.toContain('【测试】');
  });

  it('featureToNodeItem maps feature category', () => {
    expect(featureToNodeItem(makeFeature({ name: 'a', category: 'action' })).category).toBe('device');
    expect(featureToNodeItem(makeFeature({ name: 'b', category: 'perception' })).category).toBe('image');
    expect(featureToNodeItem(makeFeature({ name: 'c', category: 'control' })).category).toBe('control');
    expect(featureToNodeItem(makeFeature({ name: 'd', category: 'unknown' })).category).toBe('tools');
  });

  it('featureToNodeItem preserves dsl fields', () => {
    const item = featureToNodeItem(makeFeature({
      name: 'dsl_feature',
      dsl_keyword: 'CLICK',
      dsl_template: 'CLICK {x} {y}',
    }));
    expect(item.dsl_keyword).toBe('CLICK');
    expect(item.dsl_template).toBe('CLICK {x} {y}');
  });

  it('getLibraryFeatures filters hidden and deprecated', () => {
    const features = [
      makeFeature({ name: 'a', display_name: '正常' }),
      makeFeature({ name: 'b', display_name: '隐藏', hidden: true }),
      makeFeature({ name: 'c', display_name: '废弃', deprecated: true }),
      makeFeature({ name: 'd', display_name: '都', hidden: true, deprecated: true }),
    ];
    const items = getLibraryFeatures(features);
    expect(items).toHaveLength(1);
    expect(items[0].feature_name).toBe('a');
  });

  it('getFeatureCategoryLabel returns Chinese label', () => {
    expect(getFeatureCategoryLabel('action')).toBe('交互动作');
    expect(getFeatureCategoryLabel('perception')).toBe('感知识别');
    expect(getFeatureCategoryLabel('unknown')).toBe('unknown');
  });
});
