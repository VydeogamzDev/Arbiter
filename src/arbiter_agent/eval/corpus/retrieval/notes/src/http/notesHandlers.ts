import { createNote, listNotes } from '../notes/noteService';
import { shareNote } from '../sharing/share';
import { searchNotes } from '../search/searchIndex';
import { Config } from '../config';

export const list = (req: any, res: any) => res.json(listNotes(req.user.id));
export const create = (config: Config) => (req: any, res: any) => res.json(createNote(req.user.id, req.body, config));
export const share = (req: any, res: any) => res.json(shareNote(req.user.id, req.params.id, req.body.email));
export const search = (req: any, res: any) => res.json(searchNotes(req.user.id, String(req.query.q)));
