import Database from 'better-sqlite3';
import { loadConfig } from '../config';

const conn = new Database(loadConfig().dbPath);

export const db = {
  insertAccount: (email: string, salt: Buffer, hash: Buffer) => conn.prepare('INSERT INTO accounts VALUES (?, ?, ?)').run(email, salt, hash),
  findAccount: (email: string) => conn.prepare('SELECT * FROM accounts WHERE email = ?').get(email) as any,
  insertNote: (owner: number, title: string, body: string, tags: string[]) => ({ id: 1, ownerId: owner, title, body, tags }),
  notesFor: (owner: number) => conn.prepare('SELECT * FROM notes WHERE owner_id = ?').all(owner),
  noteById: (id: string) => conn.prepare('SELECT * FROM notes WHERE id = ?').get(id) as any,
  addCollaborator: (noteId: string, email: string) => conn.prepare('INSERT INTO collaborators VALUES (?, ?)').run(noteId, email),
};
