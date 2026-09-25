import { db } from '../storage/db';
import { Config } from '../config';
import { indexNote } from '../search/searchIndex';
import { normalizeTags } from './tags';

export function createNote(userId: number, input: any, config: Config) {
  if (Buffer.byteLength(input.body ?? '') > config.maxNoteBytes) throw new Error('note too large');
  const note = db.insertNote(userId, input.title, input.body, normalizeTags(input.tags ?? []));
  indexNote(note);
  return note;
}

export function listNotes(userId: number) {
  return db.notesFor(userId);
}
