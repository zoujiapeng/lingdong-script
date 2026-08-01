export type EditorDraftCommitter = () => string | undefined;

const committers = new Set<EditorDraftCommitter>();

export function registerEditorDraftCommitter(committer: EditorDraftCommitter): () => void {
  committers.add(committer);
  return () => committers.delete(committer);
}

export function commitEditorDrafts(): string | undefined {
  for (const commit of [...committers]) {
    try {
      const error = commit();
      if (error) return error;
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  }
  return undefined;
}
