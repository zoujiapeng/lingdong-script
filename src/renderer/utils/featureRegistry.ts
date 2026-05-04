/**
 * Feature Registry Bridge
 * Converts Python @feature definitions to frontend-compatible node items.
 */
import { FeatureDefinition } from '../types';

export interface FeatureNodeItem {
  type: 'tool_call';
  label: string;
  description: string;
  icon: string;
  color: string;
  category: string;
  feature_name: string;
  dsl_keyword?: string;
  dsl_template?: string;
}

const CATEGORY_MAP: Record<string, string> = {
  action: 'device',
  perception: 'image',
  control: 'control',
  macro: 'control',
  ai: 'system',
  system: 'system',
  debug: 'system',
};

const CATEGORY_LABELS: Record<string, string> = {
  action: '交互动作',
  perception: '感知识别',
  control: '控制流',
  macro: '宏指令',
  ai: 'AI 规划',
  system: '系统功能',
  debug: '调试诊断',
};

/** Convert a FeatureDefinition to a displayable node item */
export function featureToNodeItem(feature: FeatureDefinition): FeatureNodeItem {
  return {
    type: 'tool_call',
    label: feature.display_name,
    description: feature.description.split('\n')[0].replace(/^【[^】]*】\s*/, '').slice(0, 80),
    icon: feature.visual?.icon || 'Wrench',
    color: feature.visual?.color || '#8b5cf6',
    category: CATEGORY_MAP[feature.category] || 'tools',
    feature_name: feature.name,
    dsl_keyword: feature.dsl_keyword,
    dsl_template: feature.dsl_template,
  };
}

/** Get features that should appear in the node library */
export function getLibraryFeatures(features: FeatureDefinition[]): FeatureNodeItem[] {
  return features
    .filter((f) => !f.hidden && !f.deprecated)
    .map(featureToNodeItem);
}

/** Get category label for a feature category */
export function getFeatureCategoryLabel(cat: string): string {
  return CATEGORY_LABELS[cat] || cat;
}
