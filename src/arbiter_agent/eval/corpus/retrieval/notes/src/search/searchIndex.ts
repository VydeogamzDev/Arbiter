const index = new Map<number, Map<string, Set<string>>>();

export function indexNote(note: any) {
  const words = (note.title + ' ' + note.body).toLowerCase().split(/\W+/);
  const perUser = index.get(note.ownerId) ?? new Map();
  for (const w of words) perUser.set(w, (perUser.get(w) ?? new Set()).add(note.id));
  index.set(note.ownerId, perUser);
}

export function searchNotes(userId: number, q: string) {
  return [...(index.get(userId)?.get(q.toLowerCase()) ?? [])];
}
