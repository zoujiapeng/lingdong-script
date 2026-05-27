export interface MarkRecord {
  id: string;
  type: 'feature' | 'template' | 'node';
  tested: boolean;
  testedAt?: string;
  updatedAt: string;
}

const STORAGE_KEY = 'lingdong-item-marks';

class ItemMarker {
  private marks: Map<string, MarkRecord> = new Map();

  constructor() {
    this.load();
  }

  private load(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const arr: MarkRecord[] = JSON.parse(raw);
        for (const r of arr) {
          this.marks.set(`${r.type}:${r.id}`, r);
        }
      }
    } catch {
      // corrupt data — start fresh
    }
  }

  private save(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(this.marks.values())));
    } catch {
      // quota exceeded — silently ignore
    }
  }

  isTested(id: string, type: 'feature' | 'template' | 'node'): boolean {
    return this.marks.get(`${type}:${id}`)?.tested ?? false;
  }

  markTested(id: string, type: 'feature' | 'template' | 'node', tested: boolean): void {
    const key = `${type}:${id}`;
    const existing = this.marks.get(key);
    const now = new Date().toISOString();
    this.marks.set(key, {
      id,
      type,
      tested,
      testedAt: tested ? (existing?.testedAt || now) : undefined,
      updatedAt: now,
    });
    this.save();
  }

  toggleTested(id: string, type: 'feature' | 'template' | 'node'): boolean {
    const current = this.isTested(id, type);
    this.markTested(id, type, !current);
    return !current;
  }

  getAllRecords(): MarkRecord[] {
    return Array.from(this.marks.values());
  }

  exportToJson(): string {
    return JSON.stringify(this.getAllRecords(), null, 2);
  }

  importFromJson(json: string): number {
    try {
      const arr: MarkRecord[] = JSON.parse(json);
      for (const r of arr) {
        this.marks.set(`${r.type}:${r.id}`, r);
      }
      this.save();
      return arr.length;
    } catch {
      return 0;
    }
  }
}

export const itemMarker = new ItemMarker();
