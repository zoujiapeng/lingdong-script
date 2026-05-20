import { describe, it, expect } from 'vitest';
import { getNodeDimensions, NODE_DIMENSIONS, DEFAULT_FLOW_NAME } from '../utils/constants';

describe('constants', () => {
  it('getNodeDimensions returns startEnd for start/end types', () => {
    expect(getNodeDimensions('start')).toEqual(NODE_DIMENSIONS.startEnd);
    expect(getNodeDimensions('end')).toEqual(NODE_DIMENSIONS.startEnd);
  });

  it('getNodeDimensions returns subScript for sub_script', () => {
    expect(getNodeDimensions('sub_script')).toEqual(NODE_DIMENSIONS.subScript);
  });

  it('getNodeDimensions returns normal for other types', () => {
    expect(getNodeDimensions('click')).toEqual(NODE_DIMENSIONS.normal);
    expect(getNodeDimensions('wait')).toEqual(NODE_DIMENSIONS.normal);
    expect(getNodeDimensions('loop')).toEqual(NODE_DIMENSIONS.normal);
  });

  it('DEFAULT_FLOW_NAME is set', () => {
    expect(DEFAULT_FLOW_NAME).toBe('未命名流程');
  });
});
