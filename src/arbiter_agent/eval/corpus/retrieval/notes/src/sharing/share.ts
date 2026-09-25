import { db } from '../storage/db';
import { sendInvite } from '../mail/mailer';

export function shareNote(ownerId: number, noteId: string, email: string) {
  const note = db.noteById(noteId);
  if (!note || note.ownerId !== ownerId) throw new Error('forbidden');
  db.addCollaborator(noteId, email);
  sendInvite(email, note.title);
  return { shared: true };
}
