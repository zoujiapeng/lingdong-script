import { describe, expect, it } from 'vitest';
import platformCss from '../styles/platform.css?raw';

describe('flow studio responsive layout', () => {
  it('uses drawer side panels at the Electron default width', () => {
    const style = document.createElement('style');
    style.textContent = platformCss;
    document.head.append(style);
    const mediaRules = Array.from(style.sheet?.cssRules || [])
      .filter((rule): rule is CSSMediaRule => 'media' in rule && 'cssRules' in rule);
    const defaultWindowRule = mediaRules.find((rule) => {
      const maxWidth = Number(/max-width:\s*(\d+)px/.exec(rule.media.mediaText)?.[1] || 0);
      return maxWidth >= 900 && Array.from(rule.cssRules).some(
        (nested) => 'selectorText' in nested && nested.selectorText === '.arp-mobile-panel-button',
      );
    });

    expect(defaultWindowRule).toBeDefined();
    const rules = Array.from(defaultWindowRule!.cssRules)
      .filter((rule): rule is CSSStyleRule => 'selectorText' in rule && 'style' in rule);
    expect(rules.find((rule) => rule.selectorText === '.arp-mobile-panel-button')?.style.display).toBe('inline-grid');
    expect(rules.find((rule) => (
      rule.selectorText.includes('.arp-studio-shell .node-library')
      && rule.selectorText.includes('.arp-studio-shell .property-panel')
    ))?.style.position).toBe('absolute');
  });
});
